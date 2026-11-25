"""Run the whole pipeline (used by the GitHub Actions workflows, works locally too).

    python -m hpc.pipeline check      # weekly: new hospitals? changed / broken price files?
    python -m hpc.pipeline refresh    # monthly: discovery -> ... -> site build -> run report

check writes data/logs/check.json and, on GitHub, sets the step output `changed=true|false`.
refresh runs every step even if an earlier one fails where it can, and always writes
data/logs/run_report.md + problems.json (the workflow opens a GitHub issue when there are problems).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from hpc.config import DATA_DIR, metro_config
from hpc.registry import REGISTRY_CSV

LOGS = DATA_DIR / "logs"
KEY_COLS = ["ccn", "name", "price_file_status", "mrf_url", "hpt_status"]


def previous_registry() -> pd.DataFrame:
    if REGISTRY_CSV.exists():
        return pd.read_csv(REGISTRY_CSV, dtype=str, keep_default_na=False)
    return pd.DataFrame(columns=KEY_COLS)


def registry_changes(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    o = old.set_index("ccn") if len(old) else pd.DataFrame(columns=KEY_COLS[1:])
    n = new.set_index("ccn")
    in_scope = new.loc[new["in_scope"].astype(str).str.lower().eq("true"), "ccn"]
    out = {"new_hospitals": [], "removed_hospitals": [], "status_changes": [], "file_moved": []}
    for c in n.index.difference(o.index):
        out["new_hospitals"].append({"ccn": c, "name": n.loc[c, "name"]})
    for c in o.index.difference(n.index):
        out["removed_hospitals"].append({"ccn": c, "name": o.loc[c, "name"]})
    for c in n.index.intersection(o.index):
        if c not in set(in_scope):
            continue
        if n.loc[c, "price_file_status"] != o.loc[c, "price_file_status"]:
            out["status_changes"].append({"ccn": c, "name": n.loc[c, "name"], "from": o.loc[c, "price_file_status"],
                                          "to": n.loc[c, "price_file_status"]})
        if o.loc[c, "mrf_url"] and n.loc[c, "mrf_url"] and n.loc[c, "mrf_url"] != o.loc[c, "mrf_url"]:
            out["file_moved"].append({"ccn": c, "name": n.loc[c, "name"]})
    return out


def run_step(name: str, args: list[str], log: list, allow_fail: bool = True) -> bool:
    print(f"\n=== {name}: python -m {' '.join(args)}", flush=True)
    t0 = time.monotonic()
    res = subprocess.run([sys.executable, "-m", *args], env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    ok = res.returncode == 0
    log.append({"step": name, "ok": ok, "seconds": round(time.monotonic() - t0, 1), "exit_code": res.returncode})
    print(f"=== {name}: {'ok' if ok else f'FAILED (exit {res.returncode})'} in {log[-1]['seconds']}s", flush=True)
    if not ok and not allow_fail:
        raise SystemExit(f"{name} failed")
    return ok


def discover_and_register(log: list) -> dict:
    old = previous_registry()
    ok = run_step("discovery", ["hpc.discovery.run"], log) and run_step("registry", ["hpc.registry"], log)
    if not ok:
        return {"error": "discovery or registry failed"}
    return registry_changes(old, pd.read_csv(REGISTRY_CSV, dtype=str, keep_default_na=False))


def check(max_disk_bytes: int = int(2e9)) -> dict:
    from hpc.download import Downloader, same_version
    log: list = []
    changes = discover_and_register(log)
    reg = pd.read_csv(REGISTRY_CSV, dtype=str, keep_default_na=False)
    http = metro_config()["http"]
    dl = Downloader(http["user_agent"], delay_seconds=http["delay_seconds"])
    changed_files, broken, unknown, no_validators, too_large = [], [], [], [], []
    for url, g in reg[reg["price_file_status"] == "usable"].groupby("mrf_url"):
        p = dl.probe(url)
        names = ", ".join(g["name"])
        old = dl.state.get(url)
        if p.error:
            broken.append({"hospitals": names, "url": url, "error": p.error})
        elif p.size is not None and p.size > max_disk_bytes:
            too_large.append({"hospitals": names, "url": url, "size": p.size})     # skipped by design
        elif not old:
            unknown.append({"hospitals": names, "url": url})
        elif not (p.etag or p.last_modified) and not (old.get("etag") or old.get("last_modified")):
            no_validators.append({"hospitals": names, "url": url})                # can't tell without downloading
        elif not same_version(old, p):
            changed_files.append({"hospitals": names, "url": url})
    result = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **changes,
              "changed_files": changed_files, "broken_files": broken, "never_downloaded": unknown,
              "no_change_markers": no_validators, "too_large_skipped": too_large, "steps": log}
    # Trigger a refresh only for real signals. Files without change markers are re-checked by the monthly
    # refresh (the only way to know is to download them); files over the size limit are skipped by design.
    result["changed"] = bool(changed_files or unknown or changes.get("new_hospitals") or changes.get("status_changes")
                             or changes.get("file_moved") or changes.get("removed_hospitals") or broken)
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "check.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if result['changed'] else 'false'}\n")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in result.items() if k != "steps"}, indent=1))
    return result


def refresh(audit_n: int = 200) -> dict:
    from hpc.parse.nim import api_key_from_env
    log: list = []
    changes = discover_and_register(log)
    run_step("download", ["hpc.download", "--trust-state", "--max-gb", "2"], log)
    if run_step("parse", ["hpc.parse.run", "--skip-streaming"], log):
        has_key = bool(api_key_from_env())
        run_step("normalize", ["hpc.normalize.run"] + ([] if has_key else ["--no-embeddings"]), log)
        run_step("quality", ["hpc.quality.run"], log)
        run_step("audit", ["hpc.quality.audit", "--n", str(audit_n)], log)
        run_step("stats", ["hpc.stats.run"], log)
        run_step("summaries", ["hpc.language.summaries"] + ([] if has_key else ["--no-llm"]), log)
        run_step("site", ["hpc.site.build"], log)
    from hpc.report import write_report
    return write_report(changes, log)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "refresh"])
    ap.add_argument("--audit-n", type=int, default=200)
    args = ap.parse_args()
    if args.action == "check":
        check()
    else:
        r = refresh(args.audit_n)
        print(f"\nproblems: {r['has_problems']} ({len(r['problems'])})")


if __name__ == "__main__":
    main()
