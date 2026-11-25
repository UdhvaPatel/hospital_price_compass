"""Polite, resumable downloads of hospital price files, each fingerprinted with SHA-256.

Two modes:
- fetch():  save to disk (data/raw/<host>/<file>). Interrupted downloads resume from the
            .part file, even after a restart, when the server supports byte ranges.
- stream(): hand the bytes to a `sink` without storing the file. Used for files too big
            for disk (Mayo Clinic Arizona, 21.5 GB). Dropped connections resume mid-stream.

Unchanged files are skipped: if the server's ETag / Last-Modified / size match the last
successful download, nothing is transferred. If the server gives no validators the file is
downloaded and the hash comparison reports `unchanged_content`.

Every attempt is appended to data/logs/downloads.jsonl; the latest good state per URL is in
data/state/files.json.

Run:  python -m hpc.download [--ccn 030022 ...] [--max-gb 2] [--stream-large] [--force]
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import re
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Callable, Iterator, Protocol
from urllib.parse import unquote, urlparse

import requests
import urllib3

from hpc.config import DATA_DIR, ROOT, metro_config

RAW_DIR = DATA_DIR / "raw"
STATE_FILE = DATA_DIR / "state" / "files.json"
LOG_FILE = DATA_DIR / "logs" / "downloads.jsonl"

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
NETWORK_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Sink(Protocol):
    def write(self, data: bytes) -> object: ...


@dataclass
class Probe:
    status: int | None = None
    final_url: str = ""
    etag: str = ""
    last_modified: str = ""
    size: int | None = None
    accept_ranges: bool = False
    content_type: str = ""
    filename: str = ""
    error: str = ""


@dataclass
class Result:
    url: str
    status: str  # downloaded | streamed | unchanged | unchanged_content | too_large | failed
    http_status: int | None = None
    final_url: str = ""
    path: str = ""
    size: int | None = None
    sha256: str = ""
    etag: str = ""
    last_modified: str = ""
    bytes_transferred: int = 0
    resumed_from: int = 0
    retries: int = 0
    duration_s: float = 0.0
    reason: str = ""
    keys: list[str] = field(default_factory=list)


class TransferError(Exception):
    def __init__(self, reason: str, http_status: int | None = None, retryable: bool = False):
        super().__init__(reason)
        self.reason, self.http_status, self.retryable = reason, http_status, retryable


class TooLarge(Exception):
    pass


class RestartRequired(Exception):
    """Server ignored our Range request or the file changed while we were resuming."""


# ------------------------------------------------------------------ helpers

def _content_range_total(value: str) -> int | None:
    m = re.match(r"bytes\s+\d+-\d+/(\d+)", value or "")
    return int(m.group(1)) if m else None


def _filename(url: str, content_disposition: str = "") -> str:
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition or "")
    name = unquote(m.group(1)) if m else unquote(Path(urlparse(url).path).name)
    name = re.sub(r"[^A-Za-z0-9._,-]+", "_", name).strip("._") or "price_file"
    return name[:180]


def sha256_file(path: Path, chunk: int = 1 << 20) -> hashlib._Hash:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h


def rel_path(path: Path) -> str:
    """Store paths relative to the project so state works on any machine (e.g. CI)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def abs_path(stored: str) -> Path:
    p = Path(stored)
    return p if p.is_absolute() else ROOT / p


def same_version(old: dict, probe: Probe) -> bool:
    """True when the server says the file hasn't changed since the last good download."""
    if not old or not old.get("sha256"):
        return False
    if probe.etag and old.get("etag"):
        return probe.etag == old["etag"]
    if probe.last_modified and old.get("last_modified"):
        if probe.last_modified != old["last_modified"]:
            return False
        return probe.size is None or old.get("size") is None or probe.size == old["size"]
    return False


# ------------------------------------------------------------------ downloader

class Downloader:
    def __init__(
        self,
        user_agent: str,
        raw_dir: Path = RAW_DIR,
        state_file: Path = STATE_FILE,
        log_file: Path = LOG_FILE,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 60,
        max_retries: int = 6,
        backoff_seconds: float = 2.0,
        chunk_size: int = 1 << 20,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        progress: Callable[[str], None] | None = None,
        progress_every_bytes: int = 500_000_000,
        progress_every_seconds: float = 60.0,
        progress_min_size: int = 200_000_000,
    ):
        """progress: called with a one-line status for big transfers (e.g. `print`); None = silent."""
        self.raw_dir, self.state_file, self.log_file = Path(raw_dir), Path(state_file), Path(log_file)
        self.progress = progress
        self.progress_every_bytes, self.progress_every_seconds = progress_every_bytes, progress_every_seconds
        self.progress_min_size = progress_min_size
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
        self.delay, self.timeout = delay_seconds, timeout_seconds
        self.max_retries, self.backoff, self.chunk = max_retries, backoff_seconds, chunk_size
        self.session = session or requests.Session()
        self.sleep = sleep
        self._last_hit: dict[str, float] = {}
        self.state: dict[str, dict] = (
            json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
        )

    # -------------------------------------------------------- politeness / IO
    def _polite(self, url: str) -> None:
        host = urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last_hit.get(host, -1e9))
        if wait > 0:
            self.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_file)

    def _log(self, result: Result) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": now(), **asdict(result)}) + "\n")

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self.backoff * (2 ** attempt)
        if retry_after and retry_after.isdigit():
            delay = max(delay, min(int(retry_after), 300))
        self.sleep(min(delay, 300))

    # -------------------------------------------------------- probe
    def probe(self, url: str) -> Probe:
        """Ask for the first byte only: gets validators and total size without a download."""
        p = Probe()
        for attempt in range(self.max_retries + 1):
            self._polite(url)
            try:
                with self.session.get(
                    url, headers={**self.headers, "Range": "bytes=0-0"},
                    stream=True, timeout=self.timeout, allow_redirects=True,
                ) as r:
                    h = r.headers
                    p.status, p.final_url = r.status_code, r.url
                    if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                        self._backoff(attempt, h.get("Retry-After"))
                        continue
                    p.etag = h.get("ETag", "")
                    p.last_modified = h.get("Last-Modified", "")
                    p.content_type = h.get("Content-Type", "")
                    p.filename = _filename(r.url, h.get("Content-Disposition", ""))
                    if r.status_code == 206:
                        p.accept_ranges = True
                        p.size = _content_range_total(h.get("Content-Range", ""))
                    elif r.status_code == 200 and h.get("Content-Length", "").isdigit() \
                            and not h.get("Content-Encoding"):
                        p.size = int(h["Content-Length"])
                    p.error = "" if r.status_code in (200, 206) else f"HTTP {r.status_code}"
                    return p
            except NETWORK_ERRORS as exc:
                p.error = f"{type(exc).__name__}: {exc}"[:300]
                if attempt < self.max_retries:
                    self._backoff(attempt)
        return p

    # -------------------------------------------------------- core transfer
    def _transfer(
        self, url: str, write: Callable[[bytes], None], offset: int, validator: str,
        hasher: hashlib._Hash, max_bytes: int | None, result: Result, total: int | None = None,
    ) -> int:
        """Copy bytes from `offset` to the end, reconnecting and resuming on failures.
        Returns the final offset (= total size)."""
        attempt = 0
        report = None
        if self.progress and (total is None or total >= self.progress_min_size):
            report = ProgressReporter(self.progress, total, self.progress_every_bytes,
                                      self.progress_every_seconds, start_offset=offset)
        while True:
            headers = dict(self.headers)
            if offset:
                headers["Range"] = f"bytes={offset}-"
                if validator:
                    headers["If-Range"] = validator
            self._polite(url)
            try:
                with self.session.get(url, headers=headers, stream=True, timeout=self.timeout,
                                      allow_redirects=True) as r:
                    result.http_status, result.final_url = r.status_code, r.url
                    if r.status_code in RETRY_STATUS:
                        raise TransferError(f"HTTP {r.status_code}", r.status_code, retryable=True)
                    if r.status_code == 416 and offset:
                        raise RestartRequired("range not satisfiable")
                    if r.status_code not in (200, 206):
                        raise TransferError(f"HTTP {r.status_code}", r.status_code)
                    if offset and r.status_code == 200:
                        raise RestartRequired("server sent the whole file instead of the rest")
                    for block in r.raw.stream(self.chunk, decode_content=False):
                        if not block:
                            continue
                        if max_bytes is not None and offset + len(block) > max_bytes:
                            raise TooLarge(f"file exceeds {max_bytes:,} bytes")
                        write(block)
                        hasher.update(block)
                        offset += len(block)
                        result.bytes_transferred += len(block)
                        if report:
                            report.update(offset)
                    expected = _content_range_total(r.headers.get("Content-Range", ""))
                    if expected is None and r.status_code == 200 \
                            and r.headers.get("Content-Length", "").isdigit():
                        expected = int(r.headers["Content-Length"])
                    if expected is not None and offset < expected:
                        raise requests.exceptions.ChunkedEncodingError(
                            f"connection closed at {offset:,} of {expected:,} bytes")
                    if report:
                        report.finish(offset)
                    return offset
            except TransferError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                if report:
                    report.retry(offset, attempt + 1, exc.reason)
                self._backoff(attempt)
            except NETWORK_ERRORS + (urllib3.exceptions.HTTPError,) as exc:
                if attempt >= self.max_retries:
                    raise TransferError(f"{type(exc).__name__}: {exc}"[:300]) from exc
                if report:
                    report.retry(offset, attempt + 1, type(exc).__name__)
                self._backoff(attempt)
            attempt += 1
            result.retries = attempt

    # -------------------------------------------------------- disk mode
    def fetch(self, url: str, max_bytes: int | None = None, force: bool = False,
              keys: list[str] | None = None, trust_state: bool = False) -> Result:
        """trust_state: treat a file as unchanged when the server's validators match the saved state,
        even if the file isn't on this disk (CI runners start empty; the vault keeps fingerprints)."""
        t0 = time.monotonic()
        result = Result(url=url, status="failed", keys=keys or [])
        try:
            p = self.probe(url)
            result.http_status, result.final_url = p.status, p.final_url
            result.etag, result.last_modified, result.size = p.etag, p.last_modified, p.size
            if p.error:
                result.reason = p.error
                return result
            old = self.state.get(url, {})
            on_disk = bool(old.get("path")) and abs_path(old["path"]).exists()
            if not force and same_version(old, p) and (on_disk or (trust_state and old.get("sha256"))):
                result.status, result.path, result.sha256 = "unchanged", old["path"], old["sha256"]
                result.reason = "server validators match last download"
                return result
            if max_bytes is not None and p.size is not None and p.size > max_bytes:
                result.status, result.reason = "too_large", f"{p.size:,} bytes > limit {max_bytes:,}"
                return result

            host = re.sub(r"[^A-Za-z0-9.-]+", "_", urlparse(p.final_url or url).netloc)
            dest = self.raw_dir / host / p.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            part, meta_path = dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".part.json")
            validator = p.etag if p.etag and not p.etag.startswith("W/") else p.last_modified
            meta = {"url": url, "etag": p.etag, "last_modified": p.last_modified, "size": p.size}

            offset, hasher = 0, hashlib.sha256()
            if part.exists() and meta_path.exists() and p.accept_ranges and validator:
                if json.loads(meta_path.read_text()) == meta:
                    offset = part.stat().st_size
                    hasher = sha256_file(part)
            if offset == 0:
                part.unlink(missing_ok=True)
            meta_path.write_text(json.dumps(meta))
            result.resumed_from = offset

            for _ in range(2):  # second pass only if the server forces a restart from zero
                try:
                    with open(part, "ab" if offset else "wb") as f:
                        total = self._transfer(url, f.write, offset, validator, hasher, max_bytes, result, p.size)
                    break
                except RestartRequired:
                    offset, hasher = 0, hashlib.sha256()
                    result.resumed_from = 0
            else:
                raise TransferError("server would not resume or restart cleanly")

            if p.size is not None and total != p.size:
                raise TransferError(f"size mismatch: got {total:,}, expected {p.size:,}")
            digest = hasher.hexdigest()
            part.replace(dest)
            meta_path.unlink(missing_ok=True)
            result.path, result.size, result.sha256 = rel_path(dest), total, digest
            result.status = "unchanged_content" if old.get("sha256") == digest else "downloaded"
            self.state[url] = {
                "path": rel_path(dest), "final_url": result.final_url, "sha256": digest, "size": total,
                "etag": p.etag, "last_modified": p.last_modified, "fetched_at": now(),
                "changed_at": old.get("changed_at") if result.status == "unchanged_content" else now(),
            }
            self._save_state()
        except TooLarge as exc:
            result.status, result.reason = "too_large", str(exc)
            with contextlib.suppress(Exception):
                part.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
        except TransferError as exc:
            result.reason, result.http_status = exc.reason, exc.http_status or result.http_status
        except OSError as exc:
            result.reason = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            result.duration_s = round(time.monotonic() - t0, 2)
            self._log(result)
        return result

    # -------------------------------------------------------- streaming mode
    def stream(self, url: str, sink: Sink, force: bool = False, keys: list[str] | None = None) -> Result:
        """Send the file's bytes to `sink.write` without storing it. If the server restarts the
        file from zero, `sink.reset()` is called when available; otherwise the stream fails."""
        t0 = time.monotonic()
        result = Result(url=url, status="failed", keys=keys or [])
        try:
            p = self.probe(url)
            result.http_status, result.final_url = p.status, p.final_url
            result.etag, result.last_modified, result.size = p.etag, p.last_modified, p.size
            if p.error:
                result.reason = p.error
                return result
            old = self.state.get(url, {})
            if not force and same_version(old, p):
                result.status, result.sha256 = "unchanged", old["sha256"]
                result.reason = "server validators match last download"
                return result
            validator = p.etag if p.etag and not p.etag.startswith("W/") else p.last_modified
            hasher = hashlib.sha256()
            try:
                total = self._transfer(url, sink.write, 0, validator, hasher, None, result, p.size)
            except RestartRequired:
                if not hasattr(sink, "reset"):
                    raise TransferError("server restarted the file and the sink cannot reset")
                sink.reset()
                hasher = hashlib.sha256()
                total = self._transfer(url, sink.write, 0, validator, hasher, None, result, p.size)
            if p.size is not None and total != p.size:
                raise TransferError(f"size mismatch: got {total:,}, expected {p.size:,}")
            digest = hasher.hexdigest()
            result.size, result.sha256 = total, digest
            result.status = "unchanged_content" if old.get("sha256") == digest else "streamed"
            self.state[url] = {
                "path": "", "final_url": result.final_url, "sha256": digest, "size": total,
                "etag": p.etag, "last_modified": p.last_modified, "fetched_at": now(),
                "changed_at": old.get("changed_at") if result.status == "unchanged_content" else now(),
            }
            self._save_state()
        except TransferError as exc:
            result.reason, result.http_status = exc.reason, exc.http_status or result.http_status
        finally:
            result.duration_s = round(time.monotonic() - t0, 2)
            self._log(result)
        return result


def _fmt_bytes(n: float) -> str:
    return f"{n / 1e9:,.1f} GB" if n >= 1e9 else f"{n / 1e6:,.0f} MB"


def _fmt_eta(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


class ProgressReporter:
    """One status line every `every_bytes` or `every_seconds`, whichever comes first:
    '2.5 / 21.5 GB (11.6%) at 12.1 MB/s, about 26 min left'. Speed counts only bytes
    transferred in this run (not bytes resumed from an earlier one)."""

    def __init__(self, emit: Callable[[str], None], total: int | None, every_bytes: int,
                 every_seconds: float, start_offset: int = 0, clock: Callable[[], float] = time.monotonic):
        self.emit, self.total, self.clock = emit, total, clock
        self.every_bytes, self.every_seconds = every_bytes, every_seconds
        self.start_offset = start_offset
        self.t0 = self.last_t = clock()
        self.last_offset = start_offset
        if start_offset:
            emit(f"    ... resuming at {_fmt_bytes(start_offset)}")

    def line(self, offset: int) -> str:
        elapsed = max(self.clock() - self.t0, 1e-9)
        rate = (offset - self.start_offset) / elapsed
        done = _fmt_bytes(offset)
        if self.total:
            # same unit for both numbers: "0.5 / 21.5 GB"
            unit, div = ("GB", 1e9) if self.total >= 1e9 else ("MB", 1e6)
            nd = 1 if unit == "GB" else 0
            s = (f"{offset / div:,.{nd}f} / {self.total / div:,.{nd}f} {unit} "
                 f"({100 * offset / self.total:.1f}%) at {rate / 1e6:,.1f} MB/s")
            if rate > 0 and offset < self.total:
                s += f", about {_fmt_eta((self.total - offset) / rate)} left"
            return s
        return f"{done} at {rate / 1e6:,.1f} MB/s (total size unknown)"

    def update(self, offset: int) -> None:
        now_t = self.clock()
        if (offset - self.last_offset >= self.every_bytes
                or now_t - self.last_t >= self.every_seconds):
            self.emit("    ... " + self.line(offset))
            self.last_offset, self.last_t = offset, now_t

    def retry(self, offset: int, attempt: int, reason: str) -> None:
        self.emit(f"    ... connection problem at {_fmt_bytes(offset)} ({reason}); "
                  f"resuming, attempt {attempt}")

    def finish(self, offset: int) -> None:
        elapsed = self.clock() - self.t0
        rate = (offset - self.start_offset) / max(elapsed, 1e-9)
        self.emit(f"    ... done: {_fmt_bytes(offset)} in {_fmt_eta(elapsed)} ({rate / 1e6:,.1f} MB/s)")


class NullSink:
    """Counts bytes and discards them (used to fingerprint big files)."""

    def __init__(self):
        self.bytes = 0

    def write(self, data: bytes) -> None:
        self.bytes += len(data)

    def reset(self) -> None:
        self.bytes = 0


# ------------------------------------------------------------------ opening downloaded files

@contextlib.contextmanager
def open_price_file(path: str | Path) -> Iterator[IO[bytes]]:
    """Open a downloaded price file as a binary stream, looking inside zip / gzip wrappers.
    For a zip, the largest .csv/.json member is used."""
    path = Path(path)
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(path) as z:
            members = [i for i in z.infolist() if not i.is_dir()]
            data = [i for i in members if i.filename.lower().endswith((".csv", ".json", ".txt"))] or members
            member = max(data, key=lambda i: i.file_size)
            with z.open(member) as inner:
                yield inner
    elif magic[:2] == b"\x1f\x8b":
        with gzip.open(path, "rb") as inner:
            yield inner
    else:
        with open(path, "rb") as inner:
            yield inner


# ------------------------------------------------------------------ CLI

def main() -> None:
    from hpc.registry import load_registry

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ccn", nargs="*", help="only these hospitals")
    ap.add_argument("--max-gb", type=float, default=2.0, help="largest file to store on disk")
    ap.add_argument("--stream-large", action="store_true",
                    help="fingerprint files over --max-gb by streaming them (no disk use)")
    ap.add_argument("--force", action="store_true", help="download even if unchanged")
    ap.add_argument("--trust-state", action="store_true",
                    help="skip files whose server validators match the saved state even if not on disk (CI)")
    args = ap.parse_args()

    http = metro_config()["http"]
    dl = Downloader(http["user_agent"], delay_seconds=http["delay_seconds"],
                    progress=lambda s: print(s, flush=True))
    reg = load_registry()
    reg = reg[reg["price_file_status"] == "usable"]
    if args.ccn:
        reg = reg[reg["ccn"].isin(args.ccn)]
    files = reg.groupby("mrf_url")["ccn"].apply(list)
    max_bytes = int(args.max_gb * 1e9)

    results = []
    for i, (url, ccns) in enumerate(files.items(), 1):
        print(f"[{i}/{len(files)}] {','.join(ccns)} {url}", flush=True)
        r = dl.fetch(url, max_bytes=max_bytes, force=args.force, keys=ccns, trust_state=args.trust_state)
        if r.status == "too_large" and args.stream_large:
            r = dl.stream(url, NullSink(), force=args.force, keys=ccns)
        size = f"{r.size / 1e6:,.1f} MB" if r.size else "?"
        print(f"    -> {r.status} {size} {r.duration_s}s {r.reason}", flush=True)
        results.append(r)

    write_run_summary(results)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(json.dumps(counts, indent=1))


def write_run_summary(results: list[Result], path: Path = LOG_FILE.parent / "last_run.csv") -> None:
    """One row per file for the latest run (the full history stays in downloads.jsonl)."""
    import csv
    cols = ["keys", "status", "http_status", "size", "sha256", "etag", "last_modified",
            "bytes_transferred", "resumed_from", "retries", "duration_s", "reason", "url", "final_url"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["time"] + cols)
        w.writeheader()
        for r in results:
            row = {c: getattr(r, c) for c in cols}
            row["keys"] = " ".join(r.keys)
            w.writerow({"time": now(), **row})


if __name__ == "__main__":
    main()
