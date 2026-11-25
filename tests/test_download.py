"""Download tests against a local HTTP server that can drop connections, redirect,
refuse byte ranges and return temporary errors."""
import gzip
import hashlib
import json
import os
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hpc.download import Downloader, NullSink, Probe, abs_path, open_price_file, same_version

PAYLOAD = os.urandom(3 * 1024 * 1024 + 123)  # ~3 MB, odd size


class Server:
    def __init__(self):
        self.files = {}          # path -> dict(body, etag, last_modified, ranges, disposition)
        self.drop_after = {}     # path -> byte counts; each download request (not the probe) pops one and cuts the body there
        self.fail_status = {}    # path -> list of statuses to return first
        self.redirects = {}      # path -> target path
        self.requests = []       # (path, Range header)
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.requests.append((self.path, self.headers.get("Range")))
                if self.path in srv.redirects:
                    self.send_response(302)
                    self.send_header("Location", srv.redirects[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if srv.fail_status.get(self.path):
                    self.send_response(srv.fail_status[self.path].pop(0))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                f = srv.files.get(self.path)
                if not f:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body, start = f["body"], 0
                rng = self.headers.get("Range")
                if_range = self.headers.get("If-Range")
                validator_ok = not if_range or if_range in (f.get("etag"), f.get("last_modified"))
                if rng and f.get("ranges", True) and validator_ok:
                    start = int(re.match(r"bytes=(\d+)-", rng).group(1))
                    end = len(body) - 1
                    m = re.match(r"bytes=\d+-(\d+)", rng)
                    if m:
                        end = min(int(m.group(1)), end)
                    if start >= len(body):
                        self.send_response(416)
                        self.end_headers()
                        return
                    chunk = body[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
                else:
                    chunk = body
                    self.send_response(200)
                self.send_header("Content-Length", str(len(chunk)))
                if f.get("etag"):
                    self.send_header("ETag", f["etag"])
                if f.get("last_modified"):
                    self.send_header("Last-Modified", f["last_modified"])
                if f.get("disposition"):
                    self.send_header("Content-Disposition", f["disposition"])
                self.end_headers()
                cut = None
                drops = srv.drop_after.get(self.path)
                if drops and rng != "bytes=0-0":  # never cut the 1-byte probe
                    cut = drops.pop(0)
                try:
                    self.wfile.write(chunk if cut is None else chunk[:cut])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                if cut is not None:
                    self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path):
        return self.base + path


@pytest.fixture
def server():
    s = Server()
    yield s
    s.httpd.shutdown()


@pytest.fixture
def make_dl(tmp_path):
    def make(**kw):
        return Downloader(
            "test-agent", raw_dir=tmp_path / "raw", state_file=tmp_path / "state.json",
            log_file=tmp_path / "log.jsonl", delay_seconds=0, timeout_seconds=5,
            chunk_size=64 * 1024, sleep=lambda s: None, **kw,
        )
    return make


SHA = hashlib.sha256(PAYLOAD).hexdigest()


def test_download_hash_state_and_log(server, make_dl, tmp_path):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    r = make_dl().fetch(server.url("/a.csv"), keys=["030022"])
    assert r.status == "downloaded" and r.sha256 == SHA and r.size == len(PAYLOAD)
    assert abs_path(r.path).read_bytes() == PAYLOAD
    state = json.loads((tmp_path / "state.json").read_text())
    assert state[server.url("/a.csv")]["sha256"] == SHA
    log = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert log[-1]["status"] == "downloaded" and log[-1]["keys"] == ["030022"]


def test_skip_unchanged_then_redownload_when_changed(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    make_dl().fetch(server.url("/a.csv"))
    r = make_dl().fetch(server.url("/a.csv"))  # fresh instance reads saved state
    assert r.status == "unchanged" and r.bytes_transferred == 0
    server.files["/a.csv"] = {"body": PAYLOAD[::-1], "etag": '"v2"'}
    r = make_dl().fetch(server.url("/a.csv"))
    assert r.status == "downloaded" and r.sha256 == hashlib.sha256(PAYLOAD[::-1]).hexdigest()


def test_no_validators_reports_unchanged_content(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD}
    make_dl().fetch(server.url("/a.csv"))
    r = make_dl().fetch(server.url("/a.csv"))
    assert r.status == "unchanged_content" and r.sha256 == SHA


def test_resume_after_dropped_connection(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    server.drop_after["/a.csv"] = [1_000_000, 500_000]  # two drops
    r = make_dl().fetch(server.url("/a.csv"))
    assert r.status == "downloaded" and r.sha256 == SHA and r.retries == 2
    ranges = [rg for p, rg in server.requests if p == "/a.csv"]
    assert ranges[-1].startswith("bytes=1500000-")


def test_resume_after_interrupted_run(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    server.drop_after["/a.csv"] = [2_000_000]
    first = make_dl(max_retries=0).fetch(server.url("/a.csv"))
    assert first.status == "failed"
    r = make_dl().fetch(server.url("/a.csv"))
    assert r.status == "downloaded" and r.sha256 == SHA
    assert r.resumed_from == 2_000_000 and r.bytes_transferred == len(PAYLOAD) - 2_000_000


def test_part_file_discarded_when_file_changed(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    server.drop_after["/a.csv"] = [2_000_000]
    make_dl(max_retries=0).fetch(server.url("/a.csv"))
    new = PAYLOAD[::-1]
    server.files["/a.csv"] = {"body": new, "etag": '"v2"'}
    r = make_dl().fetch(server.url("/a.csv"))
    assert r.resumed_from == 0 and r.sha256 == hashlib.sha256(new).hexdigest()


def test_server_without_range_support_restarts(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "ranges": False}
    server.drop_after["/a.csv"] = [1_000_000]
    r = make_dl().fetch(server.url("/a.csv"))
    assert r.status == "downloaded" and r.sha256 == SHA


def test_redirect_and_content_disposition_filename(server, make_dl):
    server.files["/MRFDownload/x"] = {
        "body": PAYLOAD, "etag": '"v1"',
        "disposition": "attachment; filename=860422559_phoenix-childrens-hospital_standardcharges.zip",
    }
    server.redirects["/start"] = "/MRFDownload/x"
    r = make_dl().fetch(server.url("/start"))
    assert r.status == "downloaded" and r.final_url.endswith("/MRFDownload/x")
    assert r.path.endswith("860422559_phoenix-childrens-hospital_standardcharges.zip")


def test_retry_on_temporary_error(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    server.fail_status["/a.csv"] = [503, 429]
    r = make_dl().fetch(server.url("/a.csv"))
    assert r.status == "downloaded" and r.sha256 == SHA


def test_permanent_error_and_size_limit(server, make_dl):
    assert make_dl().fetch(server.url("/missing.csv")).status == "failed"
    server.files["/big.csv"] = {"body": PAYLOAD}
    r = make_dl().fetch(server.url("/big.csv"), max_bytes=1_000_000)
    assert r.status == "too_large" and r.bytes_transferred == 0


def test_stream_mode_resumes_without_disk(server, make_dl, tmp_path):
    server.files["/huge.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    server.drop_after["/huge.csv"] = [777_777]
    sink = NullSink()
    r = make_dl().stream(server.url("/huge.csv"), sink)
    assert r.status == "streamed" and r.sha256 == SHA and sink.bytes == len(PAYLOAD)
    assert not (tmp_path / "raw").exists()
    assert make_dl().stream(server.url("/huge.csv"), NullSink()).status == "unchanged"


def test_open_price_file_zip_gzip_plain(tmp_path):
    text = b"hospital_name,last_updated_on,version\nX,2026-01-01,3.0.0\n"
    z = tmp_path / "f.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", b"hi")
        zf.writestr("123_x_standardcharges.csv", text * 10)
    g = tmp_path / "f.csv.gz"
    g.write_bytes(gzip.compress(text))
    p = tmp_path / "f.csv"
    p.write_bytes(text)
    with open_price_file(z) as f:
        assert f.read() == text * 10
    with open_price_file(g) as f:
        assert f.read() == text
    with open_price_file(p) as f:
        assert f.read() == text


def test_same_version_rules():
    old = {"sha256": "x", "etag": '"a"', "last_modified": "Mon", "size": 5}
    assert same_version(old, Probe(etag='"a"'))
    assert not same_version(old, Probe(etag='"b"'))
    assert same_version({"sha256": "x", "last_modified": "Mon", "size": 5}, Probe(last_modified="Mon", size=5))
    assert not same_version({"sha256": "x", "last_modified": "Mon", "size": 5}, Probe(last_modified="Mon", size=6))
    assert not same_version({}, Probe(etag='"a"'))


# ------------------------------------------------------------------ progress lines

def test_progress_reporter_lines_with_fake_clock():
    from hpc.download import ProgressReporter
    t = [0.0]
    lines = []
    r = ProgressReporter(lines.append, total=21_500_000_000, every_bytes=500_000_000,
                         every_seconds=60, clock=lambda: t[0])
    t[0] = 10
    r.update(100_000_000)             # neither 500 MB nor 60 s yet: silent
    assert lines == []
    t[0] = 40
    r.update(500_000_000)             # 500 MB reached
    assert lines[-1] == "    ... 0.5 / 21.5 GB (2.3%) at 12.5 MB/s, about 28 min left"
    t[0] = 101
    r.update(600_000_000)             # 61 s since last line
    assert len(lines) == 2
    r.retry(600_000_000, 1, "ChunkedEncodingError")
    assert "resuming, attempt 1" in lines[-1]
    t[0] = 1720
    r.finish(21_500_000_000)
    assert lines[-1] == "    ... done: 21.5 GB in 29 min (12.5 MB/s)"


def test_progress_reporter_resume_and_unknown_size():
    from hpc.download import ProgressReporter
    t = [0.0]
    lines = []
    r = ProgressReporter(lines.append, total=None, every_bytes=10, every_seconds=60,
                         start_offset=2_000_000_000, clock=lambda: t[0])
    assert lines == ["    ... resuming at 2.0 GB"]
    t[0] = 2
    r.update(2_010_000_000)           # speed counts only this run's bytes: 10 MB / 2 s
    assert lines[-1] == "    ... 2.0 GB at 5.0 MB/s (total size unknown)"


def test_progress_emitted_during_real_transfer(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    server.drop_after["/a.csv"] = [1_000_000]
    lines = []
    dl = make_dl(progress=lines.append, progress_every_bytes=1_000_000, progress_min_size=0)
    r = dl.fetch(server.url("/a.csv"))
    assert r.status == "downloaded" and r.sha256 == SHA
    assert any("connection problem" in l for l in lines)
    assert any("%" in l and "MB/s" in l for l in lines)
    assert lines[-1].startswith("    ... done: 3 MB")


def test_small_files_stay_quiet(server, make_dl):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    lines = []
    make_dl(progress=lines.append).fetch(server.url("/a.csv"))  # 3 MB < 200 MB default minimum
    assert lines == []


def test_trust_state_skips_without_local_file(server, make_dl, tmp_path):
    server.files["/a.csv"] = {"body": PAYLOAD, "etag": '"v1"'}
    r = make_dl().fetch(server.url("/a.csv"))
    abs_path(r.path).unlink()                                  # a fresh CI runner: state yes, file no
    assert make_dl().fetch(server.url("/a.csv")).status == "unchanged_content"     # default: fetch again (same bytes)
    abs_path(make_dl().state[server.url("/a.csv")]["path"]).unlink()
    t = make_dl().fetch(server.url("/a.csv"), trust_state=True)
    assert t.status == "unchanged" and t.bytes_transferred == 0
