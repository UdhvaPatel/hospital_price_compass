"""Phase 2 runner: parse each hospital's price file into the standard table, keeping only
the chosen procedures.

Run:  python -m hpc.parse.run [--ccn 030022 ...] [--skip-streaming] [--force]

Outputs
  data/extracts/<ccn>.parquet    filtered rows for one hospital (these go to the private vault)
  data/extracts/all.parquet      every hospital combined
  data/logs/parse_last_run.csv   per hospital: format, items scanned/matched, rows, time, errors
  data/state/parsed.json         which file version (sha256) each extract came from

Files stored on disk are parsed from disk. Files too big for disk (Mayo, 21.5 GB) are parsed
while they stream: a background thread downloads into a bounded queue that the parser reads.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from hpc.config import DATA_DIR, metro_config, sites_config
from hpc.discovery.sniff import CMS_CSV_TALL, CMS_CSV_WIDE, CMS_JSON, LEGACY_CSV, sniff
from hpc.download import Downloader, abs_path, open_price_file, same_version
from hpc.parse.cms_csv import ParseStats, parse_cms_csv
from hpc.parse.cms_json import parse_cms_json
from hpc.parse.legacy import parse_legacy_csv
from hpc.parse.schema import COLUMNS, CodeMatcher

PARSER_VERSION = "2.1"  # 2.1: description-only candidates
EXTRACT_DIR = DATA_DIR / "extracts"
PARSED_STATE = DATA_DIR / "state" / "parsed.json"
PARSE_LOG = DATA_DIR / "logs" / "parse_last_run.csv"


def to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(COLUMNS))
    for c, t in COLUMNS.items():
        if t == "float64":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        elif t == "int64":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        elif t == "bool":
            df[c] = df[c].astype("boolean")
        else:
            df[c] = df[c].fillna("").astype("string")
    return df


# ------------------------------------------------------------------ streaming bridge

class QueueReader(io.RawIOBase):
    """A readable file object fed by a producer thread through a bounded queue."""

    def __init__(self, maxsize: int = 64):
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self.buf = b""
        self.done = False
        self.error: BaseException | None = None
        self.cancelled = False

    # producer side (used as the Downloader sink)
    def write(self, data: bytes) -> None:
        if self.cancelled:
            raise RuntimeError("parser stopped reading")
        self.q.put(bytes(data))

    def close_producer(self, error: BaseException | None = None) -> None:
        self.error = error
        self.q.put(None)

    # consumer side
    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while not self.buf and not self.done:
            item = self.q.get()
            if item is None:
                self.done = True
                if self.error:
                    raise IOError(f"download failed: {self.error}")
            else:
                self.buf = item
        n = min(len(b), len(self.buf))
        b[:n] = self.buf[:n]
        self.buf = self.buf[n:]
        return n


def stream_and_parse(dl: Downloader, url: str, ccn: str, matcher: CodeMatcher, stats: ParseStats,
                     force: bool) -> tuple[list[dict], dict]:
    reader = QueueReader()
    outcome: dict = {}

    def produce():
        try:
            outcome["result"] = dl.stream(url, reader, force=force, keys=[ccn])
            err = None if outcome["result"].status in ("streamed", "unchanged_content") else \
                RuntimeError(f"{outcome['result'].status}: {outcome['result'].reason}")
            reader.close_producer(err)
        except BaseException as exc:  # make sure the consumer never waits forever
            reader.close_producer(exc)

    t = threading.Thread(target=produce, daemon=True)
    t.start()
    try:
        buffered = io.BufferedReader(reader, buffer_size=1 << 20)
        head = buffered.peek(65536)[:65536]
        fmt = sniff(head).format
        if fmt not in (CMS_CSV_TALL, CMS_CSV_WIDE):
            raise ValueError(f"streaming supports CMS CSV only, got {fmt}")
        rows = list(parse_cms_csv(buffered, matcher, ccn, stats))
        # the parser reads to the end; drain any remainder so the producer can finish and
        # record the fingerprint (the text wrapper may already have closed the stream)
        while not buffered.closed and buffered.read(1 << 20):
            pass
    except BaseException:
        reader.cancelled = True
        while t.is_alive():
            try:
                reader.q.get_nowait()
            except queue.Empty:
                time.sleep(0.05)
        raise
    t.join()
    return rows, {"sha256": outcome["result"].sha256, "format": fmt}


# ------------------------------------------------------------------ per-hospital parse

@dataclass
class ParseLog:
    ccn: str
    name: str
    status: str = "failed"  # parsed / skipped_unchanged / failed / no_file
    format: str = ""
    mode: str = ""          # disk / stream
    items_scanned: int = 0
    items_matched: int = 0
    rows_out: int = 0
    description_candidates: int = 0
    procedures_found: int = 0
    payers_found: int = 0
    bad_rows: int = 0
    duration_s: float = 0.0
    error: str = ""


def parse_file(path: Path, ccn: str, matcher: CodeMatcher, stats: ParseStats, nim_client=None) -> tuple[list[dict], str]:
    with open_price_file(path) as f:
        head = f.read(65536)
    s = sniff(head)
    if s.format in (CMS_CSV_TALL, CMS_CSV_WIDE):
        with open_price_file(path) as f:
            return list(parse_cms_csv(f, matcher, ccn, stats)), s.format
    if s.format == CMS_JSON:
        return list(parse_cms_json(lambda: open_price_file(path), matcher, ccn, stats)), s.format
    if s.format == LEGACY_CSV:
        with open_price_file(path) as f:
            return list(parse_legacy_csv(f, matcher, ccn, nim_client, stats)), s.format
    raise ValueError(f"unsupported format: {s.format} {s.note}")


def config_fingerprint(aliases: dict | None) -> str:
    """Changes when the procedure list or this hospital's parse settings change."""
    import hashlib
    from hpc.config import CONFIG_DIR
    blob = (CONFIG_DIR / "procedures.toml").read_bytes() + json.dumps(aliases or {}, sort_keys=True).encode()
    return f"{PARSER_VERSION}:{hashlib.sha256(blob).hexdigest()[:12]}"


def nim_client_or_none():
    try:
        from hpc.parse.nim import NimClient
        return NimClient()
    except Exception:
        return None


def main() -> None:
    from hpc.registry import load_registry

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ccn", nargs="*")
    ap.add_argument("--skip-streaming", action="store_true", help="don't stream files too big for disk")
    ap.add_argument("--force", action="store_true", help="re-parse even if the file is unchanged")
    args = ap.parse_args()

    http = metro_config()["http"]
    dl = Downloader(http["user_agent"], delay_seconds=http["delay_seconds"],
                    progress=lambda s: print(s, flush=True))
    files_state = dl.state
    parsed = json.loads(PARSED_STATE.read_text()) if PARSED_STATE.exists() else {}
    base_matcher = CodeMatcher()
    overrides = sites_config().get("overrides", {})
    nim = nim_client_or_none()
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    reg = load_registry()
    reg = reg[reg["price_file_status"] == "usable"]
    if args.ccn:
        reg = reg[reg["ccn"].isin(args.ccn)]

    logs = []
    for i, h in enumerate(reg.itertuples(), 1):
        log = ParseLog(ccn=h.ccn, name=h.name)
        t0 = time.monotonic()
        out = EXTRACT_DIR / f"{h.ccn}.parquet"
        fs = files_state.get(h.mrf_url, {})
        prev = parsed.get(h.ccn, {})
        print(f"[{i}/{len(reg)}] {h.ccn} {h.name}", flush=True)
        stats = ParseStats()
        aliases = overrides.get(h.ccn, {}).get("code_type_aliases")
        matcher = CodeMatcher(type_aliases=aliases) if aliases else base_matcher
        cfg_fp = config_fingerprint(aliases)
        try:
            if fs.get("path"):
                log.mode = "disk"
                if (not args.force and out.exists() and prev.get("sha256") == fs["sha256"]
                        and prev.get("config") == cfg_fp):
                    log.status = "skipped_unchanged"
                    logs.append(log)
                    print("    -> unchanged, skipped", flush=True)
                    continue
                if not abs_path(fs["path"]).exists():          # CI: fingerprint known, file not on this disk
                    r = dl.fetch(h.mrf_url, max_bytes=int(2e9), force=True, keys=[h.ccn])
                    if r.status not in ("downloaded", "unchanged_content"):
                        raise RuntimeError(f"re-download failed: {r.status} {r.reason}")
                    fs = dl.state.get(h.mrf_url, fs)
                rows, log.format = parse_file(abs_path(fs["path"]), h.ccn, matcher, stats, nim)
                sha = fs["sha256"]
            else:
                log.mode = "stream"
                if args.skip_streaming:
                    log.status, log.error = "no_file", "too big for disk; streaming skipped"
                    logs.append(log)
                    print("    -> streaming skipped", flush=True)
                    continue
                force = args.force or not out.exists() or prev.get("config") != cfg_fp
                if not force and fs.get("sha256") and prev.get("sha256") == fs["sha256"]:
                    if same_version(fs, dl.probe(h.mrf_url)):
                        log.status = "skipped_unchanged"
                        logs.append(log)
                        print("    -> unchanged, skipped", flush=True)
                        continue
                rows, info = stream_and_parse(dl, h.mrf_url, h.ccn, matcher, stats, force=True)
                log.format, sha = info["format"], info["sha256"]

            df = to_frame(rows)
            df.to_parquet(out, index=False)
            parsed[h.ccn] = {"sha256": sha, "config": cfg_fp, "rows": len(df),
                             "parsed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            PARSED_STATE.parent.mkdir(parents=True, exist_ok=True)
            PARSED_STATE.write_text(json.dumps(parsed, indent=1, sort_keys=True))
            log.status = "parsed"
            log.procedures_found = int(df.loc[df["procedure_id"] != "", "procedure_id"].nunique())
            log.payers_found = int(df.loc[df["payer"] != "", "payer"].nunique())
        except Exception as exc:
            log.error = f"{type(exc).__name__}: {exc}"[:400]
        log.items_scanned, log.items_matched = stats.items_scanned, stats.items_matched
        log.rows_out, log.bad_rows = stats.rows_out, stats.bad_rows
        log.description_candidates = stats.description_candidates
        log.duration_s = round(time.monotonic() - t0, 1)
        logs.append(log)
        print(f"    -> {log.status} {log.format} items={log.items_scanned:,} matched={log.items_matched:,} "
              f"desc_candidates={log.description_candidates:,} "
              f"rows={log.rows_out:,} procs={log.procedures_found} payers={log.payers_found} "
              f"{log.duration_s}s {log.error}", flush=True)

    PARSE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PARSE_LOG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(logs[0]).keys()) if logs else ["ccn"])
        w.writeheader()
        for l in logs:
            w.writerow(asdict(l))

    parts = sorted(EXTRACT_DIR.glob("0*.parquet"))
    if parts:
        combined = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        combined.to_parquet(EXTRACT_DIR / "all.parquet", index=False)
        print(f"all.parquet: {len(combined):,} rows from {len(parts)} hospitals")
    counts: dict[str, int] = {}
    for l in logs:
        counts[l.status] = counts.get(l.status, 0) + 1
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
