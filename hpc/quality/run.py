"""Phase 4 runner: quality labels, compliance scores, data-quality report, M4 spot-check sheet.

Run:  python -m hpc.quality.run

Outputs (data/quality/)
  prices_labeled.parquet   Phase 3 prices + medicare_rate, medicare_multiple, quality_label/reasons, cash_label
  hospital_scores.csv/json compliance score, grade, points per component, findings, per hospital
  report.md                plain-English data-quality report (source for the website page)
  m4_spot_check.csv        ~20 prices for you to check on hospital websites (M4)
  M4_INSTRUCTIONS.md       how to do the spot check
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from hpc.config import DATA_DIR, PHASE0_DIR, load_toml, sites_config
from hpc.quality.compliance import WEIGHTS, find_twins, findings, grade, price_list_signature, score_hospital
from hpc.quality.rows import RULES, label_rows
from hpc.reference.medicare import build_rates
from hpc.registry import load_registry

OUT = DATA_DIR / "quality"


def nice_name(s: pd.Series) -> pd.Series:
    """Title case without Python's "Children'S" quirk."""
    return s.str.title().str.replace(r"'S\b", "'s", regex=True)
TOP = ["uhc", "bcbs_az", "aetna", "cigna", "humana"]


def m4_sample(labeled: pd.DataFrame, reg: pd.DataFrame, n_good: int = 12, n_flagged: int = 8,
              seed: int = 7) -> pd.DataFrame:
    """Spread across hospitals, systems, procedures and the five default insurers."""
    c = labeled[labeled["is_canonical"] & labeled["comparable"] & labeled["payer"].ne("")
                & labeled["amount_usd"].notna()].merge(
        reg[["ccn", "name", "health_system", "source_page_url"]], on="ccn", how="left")
    c = c[c["payer_id"].isin(TOP)]

    def spread(pool: pd.DataFrame, n: int, used_hosp: set, used_proc: set, per_system: dict,
               max_per_system: int = 2) -> list:
        """Each pick: a new hospital, a new procedure, and at most `max_per_system` per health system."""
        picks = []
        pool = pool.sample(frac=1, random_state=seed)
        for _, r in pool.iterrows():
            if len(picks) >= n:
                break
            sys_ = r["health_system"]
            if r["ccn"] in used_hosp or r["procedure_id"] in used_proc or per_system.get(sys_, 0) >= max_per_system:
                continue
            picks.append(r)
            used_hosp.add(r["ccn"])
            used_proc.add(r["procedure_id"])
            per_system[sys_] = per_system.get(sys_, 0) + 1
        return picks

    hosp: set = set()
    flagged = spread(c[c["quality_label"].isin(["suspect", "rejected"])], n_flagged, hosp, set(), {})
    # good rows: procedures may repeat the flagged ones; up to 3 per system so 12 rows fit
    good = spread(c[c["quality_label"] == "good"], n_good, hosp, set(), {}, max_per_system=3)
    rows = pd.DataFrame(flagged + good)
    out = pd.DataFrame({
        "id": range(1, len(rows) + 1),
        "hospital": nice_name(rows["name"]),
        "health_system": rows["health_system"],
        "procedure": rows["procedure_id"],
        "code": rows["matched_code"] + " (" + rows["matched_code_type"] + ")",
        "description_in_file": rows["description"],
        "insurer": rows["payer_name"],
        "plan_in_file": rows["plan"],
        "setting": rows["price_setting"],
        "price_in_our_data": rows["amount_usd"].round(2),
        "how_we_got_it": rows["amount_basis"],
        "gross_charge": rows["gross_charge"].round(2),
        "cash_price": rows["cash_price"].round(2),
        "our_label": rows["quality_label"],
        "our_reason": rows["quality_reasons"],
        "hospital_price_page": rows["source_page_url"],
        "YOUR_RESULT (matches / different / cant_find)": "",
        "YOUR_PRICE_ON_WEBSITE": "",
        "YOUR_NOTES": "",
    })
    return out


M4_TEXT = """# M4: spot-check about 20 prices (about 20 minutes)

Open `m4_spot_check.csv` (Excel or Google Sheets). Each row is one price from our data.
Half look normal ("good"); the others were flagged ("suspect" or "rejected") and show the reason.

For each row:
1. Open the link in **hospital_price_page** (the hospital's price transparency page).
2. Use its price lookup / "shoppable services" search, or open its standard charges file, and find the
   same **code** for the same **insurer** and plan. Prefer the *negotiated rate* or *standard charge*,
   not a personalised out-of-pocket estimate.
3. Fill in the last three columns:
   - **YOUR_RESULT**: `matches` (within a few dollars), `different`, or `cant_find`
   - **YOUR_PRICE_ON_WEBSITE**: the number you saw, if any
   - **YOUR_NOTES**: anything odd (e.g. "site shows a range", "only an estimate tool")

Then tell me it's done. I'll compare: if "good" rows match and flagged rows are really wrong, the rules
work; if not, I'll fix the cleaning or the rules.
"""


def report_md(scores: pd.DataFrame, labeled: pd.DataFrame, summary: dict) -> str:
    canon = labeled[labeled["is_canonical"] & labeled["payer"].ne("")]
    s = scores[scores["price_file_status"] != "out_of_scope"].sort_values("score", ascending=False, na_position="last")
    lines = [
        "# Data quality: who publishes clean price data, and who doesn't",
        "",
        f"Data as of {summary['run_date']}. Hospitals in scope: {summary['hospitals_in_scope']}. "
        f"Hospitals with a usable CMS-format file: {summary['usable_files']}.",
        "",
        "Every insurer price on this site carries a label:",
        "",
        "- **Good**: passed every check.",
        "- **Suspect**: possible, but something looks off (reason shown). Shown with a warning.",
        "- **Rejected**: can't be a real price (placeholder, no dollar amount, or absurd). Not shown in comparisons.",
        "",
        f"Of {len(canon):,} insurer prices: **{(canon['quality_label']=='good').sum():,} good**, "
        f"{(canon['quality_label']=='suspect').sum():,} suspect, {(canon['quality_label']=='rejected').sum():,} rejected.",
        "",
        "## What we check",
        "",
        "| Check | Label | Prices flagged |",
        "|---|---|---|",
    ]
    flags = canon["quality_flags"].str.split(";").explode()
    counts = flags[flags.ne("")].value_counts()
    for k, (sev, text) in RULES.items():
        lines.append(f"| {text[0].upper() + text[1:]} | {sev} | {int(counts.get(k, 0)):,} |")
    lines += [
        "",
        "## Compliance score by hospital",
        "",
        "Out of 100: index file and its fields (15), machine-readable price file (15), CMS format (10), "
        "updated within a year (10), required data elements (15), coverage of the procedures on this site (20), "
        "share of valid prices (15). Missing a core requirement (a machine-readable file, or any insurer rates) "
        "caps the score at 50.",
        "",
        "| Hospital | System | Score | Grade | Procedures with a good price | Findings |",
        "|---|---|---|---|---|---|",
    ]
    for _, r in s.iterrows():
        sc = "-" if pd.isna(r["score"]) else f"{r['score']:.0f}"
        cov = "-" if pd.isna(r.get("procedures_with_good_price")) else f"{int(r['procedures_with_good_price'])} of {summary['n_procedures']}"
        fnd = " ".join(r["findings"]) if isinstance(r["findings"], list) else ""
        lines.append(f"| {nice_name(pd.Series([r['name']]))[0]} | {r['health_system']} | {sc} | {r['grade']} | {cov} | {fnd} |")
    lines += ["", "Out of scope (psychiatric or federal hospitals): "
              + ", ".join(nice_name(scores.loc[scores["price_file_status"] == "out_of_scope", "name"])) + "."]
    return "\n".join(lines) + "\n"


def main() -> None:
    today = date.today()
    reg = load_registry()
    reg["in_scope"] = reg["in_scope"].astype(str).str.lower().eq("true")
    disc = pd.read_csv(PHASE0_DIR / "discovery.csv", dtype=str, keep_default_na=False)[
        ["ccn", "hpt_missing_fields", "file_hospital_name"]]
    reg = reg.merge(disc, on="ccn", how="left")
    plog = pd.read_csv(DATA_DIR / "logs" / "parse_last_run.csv", dtype={"ccn": str}).set_index("ccn").to_dict("index")
    overrides = sites_config().get("overrides", {})
    n_proc = len(load_toml("procedures.toml")["procedure"])

    # rebuilt every run (fast): picks up the OPPS / lab files as soon as they are added
    rates = build_rates(reg.loc[reg["in_scope"], "ccn"].tolist())
    prices = pd.read_parquet(DATA_DIR / "normalized" / "prices.parquet")
    prices = prices.fillna({c: "" for c in prices.columns if str(prices[c].dtype) == "string"})
    labeled = label_rows(prices, rates)
    OUT.mkdir(parents=True, exist_ok=True)
    labeled.to_parquet(OUT / "prices_labeled.parquet", index=False)

    canon = labeled[labeled["is_canonical"]]
    by_ccn = {c: g for c, g in canon.groupby("ccn")}
    file_dates = canon.groupby("ccn")["file_date"].max().to_dict()
    sigs = {c: price_list_signature(g) for c, g in by_ccn.items()}
    names = dict(zip(reg["ccn"], reg["name"]))
    twins = find_twins(sigs, names)

    rows = []
    for h in reg.to_dict("records"):
        h["file_date"] = file_dates.get(h["ccn"], "")
        p = by_ccn.get(h["ccn"])
        if h["price_file_status"] == "out_of_scope":
            sc = {"score": None, "score_status": "out of scope", "points": {}}
        else:
            sc = score_hospital(h, p, n_proc, today)
        f = findings(h, p, plog.get(h["ccn"], {}), overrides.get(h["ccn"], {}), twins.get(h["ccn"], []), sc)
        rows.append({
            "ccn": h["ccn"], "name": h["name"], "health_system": h["health_system"],
            "price_file_status": h["price_file_status"], "score": sc["score"], "grade": grade(sc["score"]),
            "score_status": sc["score_status"],
            **{f"pts_{k}": sc["points"].get(k) for k in WEIGHTS},
            **{k: sc.get(k) for k in ("procedures_with_good_price", "insurer_prices", "rejected_share",
                                      "invalid_share", "suspect_share", "gross_share", "cash_share", "capped")},
            "file_date": h["file_date"], "findings": f,
        })
    scores = pd.DataFrame(rows)
    scores.assign(findings=scores["findings"].map(" | ".join)).to_csv(OUT / "hospital_scores.csv", index=False)
    (OUT / "hospital_scores.json").write_text(scores.to_json(orient="records", indent=1), encoding="utf-8")

    summary = {"run_date": today.isoformat(), "hospitals_in_scope": int(reg["in_scope"].sum()),
               "usable_files": int((reg["price_file_status"] == "usable").sum()), "n_procedures": n_proc}
    (OUT / "report.md").write_text(report_md(scores, labeled, summary), encoding="utf-8")

    sample = m4_sample(labeled, reg)
    sample.to_csv(OUT / "m4_spot_check.csv", index=False)
    (OUT / "M4_INSTRUCTIONS.md").write_text(M4_TEXT, encoding="utf-8")

    c = canon[canon["payer"] != ""]
    print(json.dumps({
        "labels": c["quality_label"].value_counts().to_dict(),
        "comparable_labels": c[c["comparable"]]["quality_label"].value_counts().to_dict(),
        "grades": scores.loc[scores["price_file_status"] != "out_of_scope", "grade"].value_counts().to_dict(),
        "m4_rows": len(sample),
    }, indent=1))
    show = scores[scores["price_file_status"] != "out_of_scope"].sort_values("score", ascending=False, na_position="last")
    with pd.option_context("display.width", 250, "display.max_colwidth", 120, "display.max_rows", 60):
        print(show[["ccn", "name", "score", "grade", "procedures_with_good_price", "invalid_share", "suspect_share"]].to_string(index=False))


if __name__ == "__main__":
    main()
