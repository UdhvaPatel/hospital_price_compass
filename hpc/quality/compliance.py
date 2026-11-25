"""Hospital-level compliance score (0-100) and plain-English findings.

Points (weights sum to 100):
  index_file          10  cms-hpt.txt readable by a program (5) and lists this hospital (5)
  index_fields         5  all five required cms-hpt.txt fields present
  machine_access      15  price file downloadable by a program (no browser, no block)
  cms_format          10  CMS template (7) at the current version 3.x (3)
  freshness           10  updated within 12 months (10), 12-18 months (5)
  data_elements       15  insurer-specific dollar rates (5), gross charges (4), cash prices (3),
                          setting / billing class stated (3)
  procedure_coverage  20  share of the chosen procedures with at least one good comparable price
  data_validity       15  15 x (1 - invalid share - 0.5 x suspect share) over insurer prices, where
                          "invalid" = placeholder, same amount for every code, absurd vs Medicare.
                          (Formula-only rates are allowed by the rule: they lower data_elements
                          instead, through the share of rates with a dollar amount.)

Missing a core requirement of the rule (a machine-readable file, or any insurer-specific
negotiated rates for the chosen procedures) caps the score at 50.

A hospital whose usable file was not processed (e.g. skipped for size) gets no score:
we only score what we actually checked.
"""
from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

INVALID_FLAGS = ("placeholder_amount", "same_amount_every_code", "absurd_vs_medicare", "percent_without_gross")
CORE_CAP = 50.0
WEIGHTS = {"index_file": 10, "index_fields": 5, "machine_access": 15, "cms_format": 10, "freshness": 10,
           "data_elements": 15, "procedure_coverage": 20, "data_validity": 15}
assert sum(WEIGHTS.values()) == 100


def _months_between(a: date, b: date) -> float:
    return (b - a).days / 30.44


def score_hospital(h: dict, prices: pd.DataFrame | None, n_procedures: int, today: date) -> dict:
    """h: registry/discovery row as a dict. prices: this hospital's labelled canonical rows (or None)."""
    pts: dict[str, float] = {}
    hpt = h.get("hpt_status", "")
    pts["index_file"] = (5 if hpt in ("found", "location_not_listed") else 0) + (5 if hpt == "found" else 0)
    missing = str(h.get("hpt_missing_fields") or "").strip()
    pts["index_fields"] = 5 if hpt == "found" and not missing else (2 if hpt == "found" else 0)
    status = h.get("price_file_status", "")
    pts["machine_access"] = 15 if status == "usable" else 0
    fmt, ver = str(h.get("file_format") or ""), str(h.get("cms_version") or "")
    pts["cms_format"] = (7 if fmt.startswith("cms_") else 0) + (3 if fmt.startswith("cms_") and ver.startswith("3") else 0)
    upd = pd.to_datetime(str(h.get("file_date") or h.get("last_updated_on") or ""), errors="coerce")
    if pd.isna(upd) or status != "usable":     # only a file we could actually read earns freshness points
        pts["freshness"] = 0
    else:
        m = _months_between(upd.date(), today)
        pts["freshness"] = 10 if m <= 12 else (5 if m <= 18 else 0)

    if status == "usable" and prices is None:
        return {"score": None, "score_status": "not processed", "points": pts}

    if prices is None or prices.empty:
        pts.update(data_elements=0, procedure_coverage=0, data_validity=0)
        stats = {}
    else:
        payer = prices[prices["payer"] != ""]
        items = prices.drop_duplicates(["procedure_id", "source_item"])
        dollar_share = float(payer["amount_usd"].notna().mean()) if len(payer) else 0.0
        gross_share = float(items["gross_charge"].notna().mean()) if len(items) else 0.0
        cash_share = float(items["cash_price"].notna().mean()) if len(items) else 0.0
        setting_share = float((items["setting"] != "").mean()) if len(items) else 0.0
        pts["data_elements"] = round(5 * dollar_share * (len(payer) > 0) + 4 * gross_share + 3 * cash_share
                                     + 3 * setting_share, 2)
        good = prices[(prices["quality_label"] == "good") & prices["comparable"] & (prices["procedure_id"] != "")]
        covered = good["procedure_id"].nunique()
        pts["procedure_coverage"] = round(20 * covered / n_procedures, 2)
        if len(payer):
            rej = float((payer["quality_label"] == "rejected").mean())
            invalid = float(payer["quality_flags"].str.contains("|".join(INVALID_FLAGS)).mean())
            sus = float((payer["quality_label"] == "suspect").mean())
            pts["data_validity"] = round(max(0.0, 15 * (1 - invalid - 0.5 * sus)), 2)
        else:
            rej = sus = 0.0
            pts["data_validity"] = 0
        stats = {"procedures_with_good_price": int(covered), "insurer_prices": int(len(payer)),
                 "invalid_share": round(invalid, 3) if len(payer) else 0.0,
                 "rejected_share": round(rej, 3), "suspect_share": round(sus, 3),
                 "gross_share": round(gross_share, 3), "cash_share": round(cash_share, 3)}
    total = round(sum(pts.values()), 1)
    core_missing = status != "usable" or not stats.get("insurer_prices")
    capped = core_missing and total > CORE_CAP
    return {"score": min(total, CORE_CAP) if core_missing else total, "score_status": "scored",
            "points": pts, "capped": capped, **stats}


def price_list_signature(prices: pd.DataFrame) -> str | None:
    """Fingerprint of a hospital's extracted price list (procedure, insurer, plan, amounts)."""
    if prices is None or prices.empty:
        return None
    cols = ["procedure_id", "payer", "plan", "amount_usd", "gross_charge", "cash_price", "description"]
    blob = prices[cols].astype(str).sort_values(cols).to_csv(index=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def findings(h: dict, prices: pd.DataFrame | None, parse_log: dict, overrides: dict, twins: list[str],
             score: dict) -> list[str]:
    """Plain-English findings for the hospital page and the data-quality report."""
    out = []
    hpt, status = h.get("hpt_status", ""), h.get("price_file_status", "")
    if status == "out_of_scope":
        return [h.get("exclusion_reason", "out of scope")]
    if hpt == "blocked":
        out.append("Its cms-hpt.txt index file refuses automated access (HTTP 403).")
    if status == "blocked":
        out.append("The price file itself refuses automated access, so it isn't machine-readable in practice.")
    if hpt == "location_not_listed":
        out.append("Its cms-hpt.txt index file doesn't list this hospital.")
    if status == "not_found":
        out.append("No price file could be found.")
    if h.get("mrf_url_source") == "manual":
        out.append("The price file address was taken by hand because the index file couldn't be read.")
    if score.get("score_status") == "not processed":
        out.append("The price file is available but wasn't processed in this run (skipped for size).")
    pl = parse_log or {}
    if status == "usable" and pl.get("status") == "parsed" and int(pl.get("items_matched") or 0) == 0 \
            and (prices is None or (prices["match_method"] == "code").sum() == 0):
        out.append(f"The file lists {int(pl.get('items_scanned') or 0):,} items but none of the chosen procedures "
                   "by code, and no insurer rates for them.")
    if status == "usable" and prices is not None and not prices.empty and (prices["payer"] != "").sum() == 0:
        out.append("The file has no insurer-specific negotiated rates for the chosen procedures (a core requirement); "
                   "only gross and cash prices. Score capped at 50.")
    if overrides.get("code_type_aliases"):
        pairs = ", ".join(f"{k} as {v}" for k, v in overrides["code_type_aliases"].items())
        out.append(f"The file mislabels code types ({pairs}); corrected for this site.")
    if twins:
        out.append("Its extracted price list is identical to: " + ", ".join(twins) + ".")
    if score.get("points", {}).get("freshness", 10) < 10 and status == "usable":
        out.append("The file is more than 12 months old (the rule requires an annual update).")
    if prices is not None and not prices.empty:
        payer = prices[prices["payer"] != ""]
        flags = payer["quality_flags"].str.split(";").explode()
        n_place = int((flags == "placeholder_amount").sum())
        if n_place:
            out.append(f"{n_place} placeholder amounts ($0, $1 or 9,999,999-style).")
        n_same = int(payer.loc[payer["quality_flags"].str.contains("same_amount_every_code"), ["payer", "plan"]]
                     .drop_duplicates().shape[0])
        if n_same:
            out.append(f"{n_same} insurer plan(s) list the same amount for every procedure.")
        pct = payer[payer["amount_basis"].str.startswith("percent")]
        if len(pct) >= 20:
            bad = pct["quality_flags"].str.contains("outside_min_max").mean()
            if bad > 0.5:
                out.append(f"{bad:.0%} of its percentage-of-charges rates, converted to dollars, fall outside the "
                           "file's own range of real payments; those conversions are unreliable.")
        formula = int((payer["quality_flags"].str.contains("formula_only")).sum())
        if formula and formula / max(len(payer), 1) > 0.1:
            out.append(f"{formula / len(payer):.0%} of insurer rates are formulas with no dollar amount.")
    fname = str(h.get("file_hospital_name") or "")
    if fname and status == "usable" and fname.upper() not in str(h.get("name", "")).upper() \
            and not any(w in fname.upper() for w in str(h.get("name", "")).upper().split()[:2]):
        out.append(f'The file names the hospital by its legal entity ("{fname}").')
    return out


def find_twins(signatures: dict[str, str | None], names: dict[str, str]) -> dict[str, list[str]]:
    by_sig: dict[str, list[str]] = {}
    for ccn, sig in signatures.items():
        if sig:
            by_sig.setdefault(sig, []).append(ccn)
    out = {}
    for group in by_sig.values():
        if len(group) > 1:
            for c in group:
                out[c] = [names[o].title() for o in group if o != c]
    return out


def grade(score: float | None) -> str:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "not scored"
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"
