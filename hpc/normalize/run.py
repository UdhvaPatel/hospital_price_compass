"""Phase 3 runner: normalise procedures, payers, plans and rate types.

Run:  python -m hpc.normalize.run [--no-embeddings]

Input   data/extracts/all.parquet (Phase 2)
Output  data/normalized/prices.parquet       every row, with normalised fields and flags
        data/normalized/summary.json
        data/review/payer_review.csv          borderline insurer matches  -> config/payer_decisions.csv
        data/review/description_review.csv    borderline description matches
        data/review/payer_threshold_check.csv how reliable embeddings were on known names
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import pandas as pd

from hpc.config import DATA_DIR, load_toml
from hpc.normalize.descriptions import DescriptionMatcher, calibrate
from hpc.normalize.payers import (
    PLAN_TYPES, REVIEW_DIR, PayerConfig, classify_plan, match_payers, threshold_check, write_review,
)
from hpc.normalize.rates import dedupe, settings_and_scope, to_dollars

EXTRACTS = DATA_DIR / "extracts" / "all.parquet"
OUT_DIR = DATA_DIR / "normalized"


def normalise_payers(df: pd.DataFrame, cfg: PayerConfig, embedder) -> tuple[pd.DataFrame, pd.DataFrame]:
    payers = match_payers(sorted(set(df["payer"]) - {""}), cfg, embedder)
    cols = ["payer_raw", "payer_id", "payer_name", "payer_parent", "method", "score", "contract_setting", "payer_clean"]
    m = payers[cols].rename(columns={"payer_raw": "payer", "method": "payer_match_method",
                                     "score": "payer_match_score"})
    df = df.merge(m, on="payer", how="left")
    no_payer = df["payer"].eq("")
    df.loc[no_payer, ["payer_id", "payer_name", "payer_match_method"]] = ["", "", "none"]
    df[["contract_setting", "payer_clean", "payer_parent"]] = df[["contract_setting", "payer_clean", "payer_parent"]].fillna("")

    by_id = cfg.by_id()
    pairs = df.loc[~no_payer, ["payer_id", "payer_clean", "plan"]].drop_duplicates()
    info = {
        (r.payer_id, r.payer_clean, r.plan): classify_plan(
            r.payer_id, r.payer_clean, r.plan, by_id.get(r.payer_id, {}).get("default_plan_type", "unknown"))
        for r in pairs.itertuples()
    }
    keys = list(zip(df["payer_id"], df["payer_clean"], df["plan"]))
    df["plan_type"] = [info[k].plan_type if k in info else "" for k in keys]
    df["plan_network"] = [info[k].network if k in info else "" for k in keys]
    df["plan_professional_hint"] = [info[k].professional_hint if k in info else False for k in keys]
    return df, payers


def normalise_descriptions(df: pd.DataFrame, embedder) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cand = df["match_method"].eq("description_candidate")
    df["description_score"] = pd.NA
    if not cand.any() or embedder is None:
        df.loc[cand, "match_method"] = "description_unmatched"
        return df, pd.DataFrame(), pd.DataFrame()
    matcher = DescriptionMatcher(embedder)
    calib, _ = calibrate(matcher, df[df["match_method"].eq("code") & df["is_primary_code"].fillna(False)])
    results = {m.description: m for m in matcher.match(df.loc[cand, "description"].tolist())}
    res = df.loc[cand, "description"].map(results)
    decision = res.map(lambda m: m.decision if m else "rejected")
    df.loc[cand, "procedure_id"] = res.map(lambda m: m.procedure_id if m and m.decision == "accepted" else "")
    df.loc[cand, "description_score"] = res.map(lambda m: round(m.score, 3) if m else None)
    df.loc[cand, "match_method"] = "description_" + decision
    df.loc[cand & df["match_method"].eq("description_accepted"), "matched_code_type"] = "DESCRIPTION"

    # Description matches only stand in where the hospital has no coded rows for that procedure
    coded = set(zip(df.loc[df["match_method"].eq("code"), "ccn"], df.loc[df["match_method"].eq("code"), "procedure_id"]))
    acc = df["match_method"].eq("description_accepted")
    shadowed = acc & pd.Series(list(zip(df["ccn"], df["procedure_id"])), index=df.index).isin(coded)
    df.loc[shadowed, "match_method"] = "description_shadowed"

    review = pd.DataFrame([
        {"description": m.description, "suggested_procedure": m.procedure_id, "score": round(m.score, 3),
         "reject_score": round(m.reject_score, 3), "decision": m.decision, "reason": m.reason}
        for m in results.values() if m.decision in ("review", "accepted")
    ]).sort_values("score") if results else pd.DataFrame()
    return df, review, calib


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-embeddings", action="store_true", help="aliases / rules only (no NIM calls)")
    args = ap.parse_args()

    embedder = None
    if not args.no_embeddings:
        from hpc.nlp.embeddings import Embedder
        embedder = Embedder()

    df = pd.read_parquet(EXTRACTS)
    df = df.fillna({c: "" for c in df.columns if str(df[c].dtype) == "string"})
    n_in = len(df)
    cfg = PayerConfig.load()
    families = {p["id"]: p["family"] for p in load_toml("procedures.toml")["procedure"]}

    df, payers = normalise_payers(df, cfg, embedder)
    df, desc_review, calib = normalise_descriptions(df, embedder)
    df = df[~df["match_method"].isin(["description_rejected"])].copy()
    df = to_dollars(df)
    df = settings_and_scope(df, families)
    df.loc[df["match_method"].isin(["description_review", "description_shadowed"]), "comparable"] = False
    df.loc[df["match_method"].eq("description_review"), "comparable_reason"] = "description match needs review"
    df.loc[df["match_method"].eq("description_shadowed"), "comparable_reason"] = "hospital lists this procedure by code"
    df = dedupe(df)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / "prices.parquet", index=False)
    write_review(payers)
    if len(desc_review):
        desc_review.to_csv(REVIEW_DIR / "description_review.csv", index=False)
    if embedder is not None:
        tc = threshold_check(payers, cfg, embedder)
        tc.to_csv(REVIEW_DIR / "payer_threshold_check.csv", index=False)
    if len(calib):
        calib.to_csv(REVIEW_DIR / "description_calibration.csv", index=False)

    canon = df[df["is_canonical"]]
    comp = canon[canon["comparable"]]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows_in": n_in,
        "rows_out": len(df),
        "canonical_rows": len(canon),
        "comparable_canonical_rows": len(comp),
        "hospitals_with_comparable_prices": int(comp["ccn"].nunique()),
        "payer_names_raw": int(payers["payer_raw"].nunique()),
        "payer_ids": int(payers["payer_id"].nunique()),
        "payer_match_methods": payers["method"].value_counts().to_dict(),
        "plan_types": canon.loc[canon["payer"] != "", "plan_type"].value_counts().to_dict(),
        "amount_basis": canon["amount_basis"].value_counts().to_dict(),
        "match_methods": df["match_method"].value_counts().to_dict(),
        "not_comparable_reasons": canon.loc[~canon["comparable"], "comparable_reason"].value_counts().to_dict(),
        "plan_type_labels": PLAN_TYPES,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "plan_type_labels"}, indent=2))
    if len(calib):
        print("\nDescription matcher calibration (on coded rows):")
        print(calib.to_string(index=False))


if __name__ == "__main__":
    main()
