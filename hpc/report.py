"""Run report: what a refresh did and what went wrong, in plain English.

Writes data/logs/run_report.md (the body of the GitHub issue opened when there are problems) and
data/logs/problems.json ({"has_problems": bool, "problems": [...], ...}).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from hpc.config import DATA_DIR

LOGS = DATA_DIR / "logs"


def _csv(path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False) if path.exists() else pd.DataFrame()


def collect(changes: dict, steps: list) -> tuple[list[str], list[str]]:
    """(problems, notes). Problems open an issue; notes are informational."""
    problems, notes = [], []
    for s in steps:
        if not s["ok"]:
            problems.append(f"Step **{s['step']}** failed (exit code {s['exit_code']}).")
    if changes.get("error"):
        problems.append(changes["error"])
    for h in changes.get("status_changes", []):
        line = f"{h['name']} ({h['ccn']}): price file status changed from **{h['from']}** to **{h['to']}**."
        (problems if h["to"] != "usable" else notes).append(line)
    for h in changes.get("file_moved", []):
        notes.append(f"{h['name']} ({h['ccn']}): price file moved to a new address.")
    for h in changes.get("new_hospitals", []):
        notes.append(f"New hospital in the CMS list: {h['name']} ({h['ccn']}). Check its scope and website in config.")
    for h in changes.get("removed_hospitals", []):
        problems.append(f"Hospital no longer in the CMS list: {h['name']} ({h['ccn']}).")

    d = _csv(LOGS / "last_run.csv")
    if len(d):
        for _, r in d[d["status"] == "failed"].iterrows():
            problems.append(f"Download failed for {r['keys']}: {r['reason'] or 'HTTP ' + r['http_status']}.")
        for _, r in d[d["status"] == "too_large"].iterrows():
            notes.append(f"Skipped (too big to store; streaming not run): {r['keys']} ({r['reason']}).")
        n_new = int(d["status"].isin(["downloaded"]).sum())
        notes.append(f"Price files: {n_new} new or changed, {int(d['status'].isin(['unchanged', 'unchanged_content']).sum())} unchanged.")
    p = _csv(LOGS / "parse_last_run.csv")
    if len(p):
        for _, r in p[p["status"] == "failed"].iterrows():
            problems.append(f"Parsing failed for {r['name']} ({r['ccn']}): {r['error']}.")
        for _, r in p[(p["status"] == "parsed") & (p["rows_out"] == "0")].iterrows():
            notes.append(f"{r['name']} ({r['ccn']}): file parsed but no prices for the chosen procedures.")
    a = LOGS.parent / "quality" / "audit_summary.json"
    if a.exists():
        au = json.loads(a.read_text())
        if au.get("pass_rate", 1) < 1:
            problems.append(f"Source audit: only {au['passed']} of {au['sampled_prices']} sampled prices matched the original files.")
        else:
            notes.append(f"Source audit: {au['passed']} of {au['sampled_prices']} sampled prices match the original files.")
    s = DATA_DIR / "summaries" / "summaries.json"
    if s.exists():
        rs = json.loads(s.read_text(encoding="utf-8"))
        tpl = sum(1 for r in rs if r["source"].endswith("template"))
        notes.append(f"Summaries: {len(rs) - tpl} written by the model, {tpl} from the template.")
    return problems, notes


def write_report(changes: dict, steps: list) -> dict:
    problems, notes = collect(changes, steps)
    now = datetime.now(timezone.utc)
    md = [f"# Refresh report, {now:%Y-%m-%d %H:%M} UTC", ""]
    md += ["## Problems", ""] + ([f"- {x}" for x in problems] or ["- None."]) + [""]
    md += ["## Notes", ""] + [f"- {x}" for x in notes] + [""]
    md += ["## Steps", "", "| Step | Result | Seconds |", "|---|---|---|"]
    md += [f"| {s['step']} | {'ok' if s['ok'] else 'FAILED'} | {s['seconds']} |" for s in steps]
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "run_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    out = {"generated_at": now.isoformat(timespec="seconds"), "has_problems": bool(problems),
           "problems": problems, "notes": notes, "steps": steps}
    (LOGS / "problems.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\n".join(md))
    return out
