"""Phase 8 tests: registry change detection, run report, vault sync (against a local bare repo)."""
import json
import subprocess

import pandas as pd
import pytest

from hpc import report, vault
from hpc.pipeline import registry_changes


def reg(rows):
    cols = ["ccn", "name", "price_file_status", "mrf_url", "hpt_status", "in_scope"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows])


def test_registry_changes():
    old = reg([("1", "A", "usable", "u1", "found", "True"), ("2", "B", "usable", "u2", "found", "True"),
               ("3", "C", "usable", "u3", "found", "True")])
    new = reg([("1", "A", "blocked", "u1", "blocked", "True"), ("2", "B", "usable", "u2-new", "found", "True"),
               ("4", "D", "not_found", "", "not_found", "True")])
    c = registry_changes(old, new)
    assert c["new_hospitals"] == [{"ccn": "4", "name": "D"}]
    assert c["removed_hospitals"] == [{"ccn": "3", "name": "C"}]
    assert c["status_changes"] == [{"ccn": "1", "name": "A", "from": "usable", "to": "blocked"}]
    assert c["file_moved"] == [{"ccn": "2", "name": "B"}]


def test_report_flags_problems_and_notes(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(report, "LOGS", logs)
    monkeypatch.setattr(report, "DATA_DIR", tmp_path)
    pd.DataFrame([{"keys": "030001", "status": "failed", "reason": "HTTP 404", "http_status": "404"},
                  {"keys": "030103", "status": "too_large", "reason": "21 GB", "http_status": "206"},
                  {"keys": "030002", "status": "unchanged", "reason": "", "http_status": "206"}]).to_csv(logs / "last_run.csv", index=False)
    pd.DataFrame([{"ccn": "030003", "name": "X", "status": "failed", "error": "bad CSV", "rows_out": "0"}]).to_csv(logs / "parse_last_run.csv", index=False)
    changes = {"status_changes": [{"ccn": "1", "name": "A", "from": "usable", "to": "blocked"}], "new_hospitals": [],
               "removed_hospitals": [], "file_moved": []}
    out = report.write_report(changes, [{"step": "stats", "ok": False, "seconds": 1, "exit_code": 1}])
    text = " ".join(out["problems"])
    assert out["has_problems"] and "stats" in text and "HTTP 404" in text and "bad CSV" in text and "to **blocked**" in text
    assert any("too big" in n for n in out["notes"])
    assert (logs / "run_report.md").exists() and json.loads((logs / "problems.json").read_text())["has_problems"]


def test_report_without_problems(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(report, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(report, "DATA_DIR", tmp_path)
    out = report.write_report({}, [{"step": "parse", "ok": True, "seconds": 2, "exit_code": 0}])
    assert not out["has_problems"]


def _bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    return remote


def test_vault_roundtrip_with_local_remote(tmp_path, monkeypatch):
    remote = _bare_remote(tmp_path)
    monkeypatch.setattr(vault, "repo_url", lambda repo: str(remote))
    work, v = tmp_path / "work", tmp_path / ".vault"
    (work / "data" / "state").mkdir(parents=True)
    (work / "data" / "state" / "files.json").write_text('{"u": {"sha256": "abc"}}')
    (work / "data" / "extracts").mkdir(parents=True)
    (work / "data" / "extracts" / "030001.parquet").write_bytes(b"PAR1")
    assert vault.pull("x/y", "tok", root=work, vault=v) == 0            # empty vault -> fresh start
    assert vault.push("x/y", "tok", root=work, vault=v) is True
    assert vault.push("x/y", "tok", root=work, vault=v) is False        # nothing changed -> no commit
    fresh = tmp_path / "fresh"
    assert vault.pull("x/y", "tok", root=fresh, vault=tmp_path / ".vault2") == 2
    assert (fresh / "data" / "state" / "files.json").read_text() == '{"u": {"sha256": "abc"}}'


def test_vault_never_prints_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "repo_url", lambda repo: str(tmp_path / "does-not-exist.git"))
    with pytest.raises(vault.VaultError) as e:
        vault.clone("x/y", "SECRET-TOKEN-123", tmp_path / ".v")
    assert "SECRET-TOKEN-123" not in str(e.value)


def test_check_triggers_only_on_real_changes(tmp_path, monkeypatch):
    import hpc.download as D
    import hpc.pipeline as P
    from hpc.download import Probe
    regfile = tmp_path / "hospitals.csv"
    pd.DataFrame([
        {"ccn": "1", "name": "Changed", "price_file_status": "usable", "mrf_url": "u-changed"},
        {"ccn": "2", "name": "Same", "price_file_status": "usable", "mrf_url": "u-same"},
        {"ccn": "3", "name": "NoMarkers", "price_file_status": "usable", "mrf_url": "u-nomarkers"},
        {"ccn": "4", "name": "Huge", "price_file_status": "usable", "mrf_url": "u-huge"},
    ]).to_csv(regfile, index=False)
    probes = {"u-changed": Probe(status=206, etag='"v2"', size=10), "u-same": Probe(status=206, etag='"v1"', size=10),
              "u-nomarkers": Probe(status=200, size=10), "u-huge": Probe(status=206, etag='"x"', size=21_000_000_000)}
    state = {"u-changed": {"sha256": "a", "etag": '"v1"'}, "u-same": {"sha256": "b", "etag": '"v1"'},
             "u-nomarkers": {"sha256": "c"}}

    class FakeDL:
        def __init__(self, *a, **k):
            self.state = state

        def probe(self, url):
            return probes[url]

    monkeypatch.setattr(D, "Downloader", FakeDL)
    monkeypatch.setattr(P, "REGISTRY_CSV", regfile)
    monkeypatch.setattr(P, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(P, "discover_and_register", lambda log: {"new_hospitals": [], "status_changes": []})
    r = P.check()
    assert [x["hospitals"] for x in r["changed_files"]] == ["Changed"]
    assert [x["hospitals"] for x in r["no_change_markers"]] == ["NoMarkers"]
    assert [x["hospitals"] for x in r["too_large_skipped"]] == ["Huge"]
    assert r["changed"] is True
    state["u-changed"]["etag"] = '"v2"'                    # now nothing really changed
    assert P.check()["changed"] is False
