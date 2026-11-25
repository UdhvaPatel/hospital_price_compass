"""Automated source audit (replaces the manual M4 spot check).

Takes a random sample of final prices, spread across hospitals, and re-reads each one from the
hospital's original file with a deliberately simple, independent reader (it does not use the
Phase 2 parsers). For every sampled price it checks that the original record:

  code         contains the matched code
  insurer      has the same payer name
  plan         has the same plan name
  amount       has the same negotiated dollar amount (or, for converted rates, percentage x gross
               reproduces our dollar figure)
  gross, cash  have the same gross charge and cash price
  description  has the same description

Run:  python -m hpc.quality.audit [--n 500]
Out:  data/quality/audit_sample.csv, data/quality/audit_summary.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timezone

import ijson
import numpy as np
import pandas as pd

from hpc.config import DATA_DIR
from hpc.download import abs_path, open_price_file

OUT = DATA_DIR / "quality"
CHECKS = ["code", "insurer", "plan", "amount", "gross", "cash", "description"]
OK = [f"ok_{c}" for c in CHECKS]          # result columns (kept apart from the data columns)
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


# ------------------------------------------------------------------ independent readers

def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if np.isnan(float(v)) else float(v)   # NaN means missing
    s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _same(a, b, tol: float = 0.005) -> bool:
    """Equal numbers (to within half a cent), or both missing."""
    a, b = _num(a), _num(b)
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(tol, 1e-9 * max(abs(a), abs(b)))


def read_csv_records(stream, wanted: set[int]) -> tuple[list[str], dict[int, list[str]]]:
    """Header row (3rd record) and the data records whose number is in `wanted`.
    Record numbering: the first data record (after 2 metadata rows and the header) is 4."""
    reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline=""))
    next(reader), next(reader)
    header = [h.strip() for h in next(reader)]
    found: dict[int, list[str]] = {}
    last = max(wanted) if wanted else 0
    for n, rec in enumerate(reader, start=4):
        if n in wanted:
            found[n] = rec
        if n >= last:
            break
    return header, found


def read_json_items(stream, wanted: set[int]) -> dict[int, dict]:
    found: dict[int, dict] = {}
    last = max(wanted) if wanted else -1
    for n, item in enumerate(ijson.items(stream, "standard_charge_information.item", use_float=True)):
        if n in wanted:
            found[n] = item
        if n >= last:
            break
    return found


# ------------------------------------------------------------------ checks

def _expected_amount_ok(row, raw_dollar, raw_pct, raw_gross) -> bool:
    """Our amount_usd must come straight from the source: the dollar amount, or pct x gross."""
    basis = row["amount_basis"]
    if basis == "negotiated_dollar":
        return _same(raw_dollar, row["amount_usd"])
    if str(basis).startswith("percent"):
        p, g = _num(raw_pct), _num(raw_gross)
        if p is None or g is None:
            return False
        if 0 < p <= 1:
            p *= 100
        return _same(p / 100 * g, row["amount_usd"], tol=0.01)
    return _num(raw_dollar) is None and pd.isna(row["amount_usd"])


def check_csv_record(row, header: list[str], rec: list[str]) -> dict:
    low = [h.lower() for h in header]
    get = lambda name: rec[low.index(name)] if name in low and low.index(name) < len(rec) else None
    codes = {rec[i].strip().upper().lstrip("0") for i, h in enumerate(low)
             if h.startswith("code|") and not h.endswith("|type") and i < len(rec)}
    res = {"code": row["matched_code"] == "" or str(row["matched_code"]).upper().lstrip("0") in codes,
           "description": (get("description") or "").strip() == row["description"],
           "gross": _same(get("standard_charge|gross"), row["gross_charge"]),
           "cash": _same(get("standard_charge|discounted_cash"), row["cash_price"])}
    if "payer_name" in low:                                     # tall
        res["insurer"] = (get("payer_name") or "").strip() == row["payer"]
        res["plan"] = (get("plan_name") or "").strip() == row["plan"]
        res["amount"] = _expected_amount_ok(row, get("standard_charge|negotiated_dollar"),
                                            get("standard_charge|negotiated_percentage"), get("standard_charge|gross"))
    else:                                                        # wide: find this payer/plan's columns
        prefix = f"standard_charge|{row['payer']}|{row['plan']}|".lower()
        cols = {h[len(prefix):]: i for i, h in enumerate(low) if h.startswith(prefix)}
        res["insurer"] = res["plan"] = bool(cols)
        val = lambda k: rec[cols[k]] if k in cols and cols[k] < len(rec) else None
        res["amount"] = bool(cols) and _expected_amount_ok(row, val("negotiated_dollar"), val("negotiated_percentage"),
                                                           get("standard_charge|gross"))
    return res


def check_json_item(row, item: dict) -> dict:
    codes = {str(c.get("code", "")).strip().upper().lstrip("0") for c in item.get("code_information") or []}
    res = {"code": row["matched_code"] == "" or str(row["matched_code"]).upper().lstrip("0") in codes,
           "description": str(item.get("description", "")).strip() == row["description"],
           "insurer": False, "plan": False, "amount": False, "gross": False, "cash": False}
    for sc in item.get("standard_charges") or []:
        if str(sc.get("setting", "")).strip().lower() != row["setting"]:
            continue
        for p in sc.get("payers_information") or []:
            if str(p.get("payer_name", "")).strip() == row["payer"] and str(p.get("plan_name", "")).strip() == row["plan"]:
                res.update(insurer=True, plan=True,
                           amount=_expected_amount_ok(row, p.get("standard_charge_dollar"),
                                                      p.get("standard_charge_percentage"), sc.get("gross_charge")),
                           gross=_same(sc.get("gross_charge"), row["gross_charge"]),
                           cash=_same(sc.get("discounted_cash"), row["cash_price"]))
                if res["amount"]:
                    return res
    return res


# ------------------------------------------------------------------ sampling + run

def draw_sample(prices: pd.DataFrame, n: int, seed: int = 11) -> pd.DataFrame:
    """Final prices (canonical, comparable, with an insurer), spread evenly across hospitals."""
    pool = prices[prices["is_canonical"] & prices["comparable"] & prices["payer"].ne("")
                  & prices["match_method"].isin(["code", "description_accepted"])]
    per = max(1, int(np.ceil(n / max(pool["ccn"].nunique(), 1))))
    s = pool.sample(frac=1, random_state=seed).groupby("ccn").head(per)   # up to `per` random rows each
    return s.sample(min(n, len(s)), random_state=seed).reset_index(drop=True)


def audit(sample: pd.DataFrame, file_for_ccn: dict[str, str]) -> pd.DataFrame:
    out = []
    for ccn, g in sample.groupby("ccn"):
        path = file_for_ccn.get(ccn)
        if not path:
            for _, r in g.iterrows():
                out.append({**r.to_dict(), **{f"ok_{c}": False for c in CHECKS}, "audit_note": "source file not on disk"})
            continue
        wanted = set(int(x) for x in g["source_item"])
        fmt = g["source_format"].iloc[0]
        with open_price_file(abs_path(path)) as f:
            if fmt == "cms_json":
                items = read_json_items(f, wanted)
                header = None
            else:
                header, items = read_csv_records(f, wanted)
        for _, r in g.iterrows():
            src = items.get(int(r["source_item"]))
            if src is None:
                res, note = {c: False for c in CHECKS}, "record not found"
            else:
                res = check_json_item(r, src) if fmt == "cms_json" else check_csv_record(r, header, src)
                note = ""
            out.append({**r.to_dict(), **{f"ok_{k}": v for k, v in res.items()}, "audit_note": note})
    df = pd.DataFrame(out)
    df["passed"] = df[OK].all(axis=1)
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=500)
    args = ap.parse_args()

    from hpc.registry import load_registry
    prices = pd.read_parquet(OUT / "prices_labeled.parquet")
    prices = prices.fillna({c: "" for c in prices.columns if str(prices[c].dtype) in ("string", "object")})
    state = json.loads((DATA_DIR / "state" / "files.json").read_text())
    reg = load_registry()
    file_for_ccn = {r.ccn: state.get(r.mrf_url, {}).get("path", "") for r in reg.itertuples()}

    sample = draw_sample(prices, args.n)
    res = audit(sample, file_for_ccn)
    keep = ["ccn", "procedure_id", "matched_code", "payer", "plan", "setting", "amount_usd", "amount_basis",
            "gross_charge", "cash_price", "source_format", "source_item", *OK, "passed", "audit_note"]
    res[keep].to_csv(OUT / "audit_sample.csv", index=False)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sampled_prices": int(len(res)), "hospitals": int(res["ccn"].nunique()),
        "passed": int(res["passed"].sum()), "pass_rate": round(float(res["passed"].mean()), 4),
        "check_pass_rates": {c: round(float(res[f"ok_{c}"].mean()), 4) for c in CHECKS},
        "by_format": res.groupby("source_format")["passed"].mean().round(4).to_dict(),
        "by_amount_basis": res.groupby("amount_basis")["passed"].mean().round(4).to_dict(),
    }
    (OUT / "audit_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    fails = res[~res["passed"]]
    if len(fails):
        with pd.option_context("display.width", 250, "display.max_colwidth", 40):
            print(f"\n{len(fails)} failed:")
            print(fails[["ccn", "procedure_id", "payer", "plan", "amount_usd", "source_format", *OK, "audit_note"]]
                  .head(30).to_string(index=False))


if __name__ == "__main__":
    main()
