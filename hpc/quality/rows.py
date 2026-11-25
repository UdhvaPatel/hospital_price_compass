"""Quality label for every price row: good / suspect / rejected, with the reasons.

rejected  the number can't be a real price, or there is no number:
          placeholder ($0, $0.01, $1, 9,999,999...), formula with no dollar amount,
          the same amount for every procedure in a payer/plan's list,
          or an absurd multiple of Medicare (< 0.1x or > 25x)
suspect   possible but needs care:
          negotiated rate above the gross charge, cash price above the gross charge,
          min > max, rate outside the file's own min-max, far from Medicare (< 0.4x or > 8x),
          or 10x away from other Phoenix prices for the same procedure (used where no
          Medicare rate is available)
good      none of the above

The cash price gets its own label (cash_label), because patients compare it separately.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RULES = {
    "placeholder_amount": ("rejected", "placeholder amount ($0, $0.01, $1 or 9,999,999-style)"),
    "formula_only": ("rejected", "formula only, no dollar amount"),
    "percent_no_gross": ("rejected", "percentage of charges, but no gross charge to convert it to dollars"),
    "same_amount_every_code": ("rejected", "same amount for every procedure in this plan's list"),
    "absurd_vs_medicare": ("rejected", "absurd multiple of Medicare (below 0.1x or above 25x)"),
    "percent_without_gross": ("rejected", "percentage rate but the gross charge is a placeholder"),
    "above_gross": ("suspect", "negotiated rate above the hospital's own gross charge"),
    "min_gt_max": ("suspect", "file's minimum is greater than its maximum"),
    "outside_min_max": ("suspect", "rate outside the file's own minimum-maximum range"),
    "far_from_medicare": ("suspect", "far from Medicare (below 0.4x or above 8x)"),
    "market_outlier": ("suspect", "more than 10x away from other Phoenix prices for this procedure and plan type"),
    "component_line": ("suspect", "the item's gross charge is under a tenth of other Phoenix hospitals' gross charge "
                                  "for this procedure: likely a partial (component) line, not the full service"),
}
CASH_RULES = {
    "cash_placeholder": ("rejected", "placeholder cash price"),
    "cash_above_gross": ("suspect", "cash price above the gross charge"),
}

THRESHOLDS = {
    "medicare_reject_low": 0.1, "medicare_reject_high": 25.0,
    "medicare_suspect_low": 0.4, "medicare_suspect_high": 8.0,
    "market_ratio": 10.0, "component_price_ratio": 0.25, "same_amount_min_procedures": 5, "tolerance": 1.01,
}


def is_placeholder(x: pd.Series) -> pd.Series:
    v = x.astype("float64")
    nines = v.round(0).astype("Int64").astype(str).str.fullmatch(r"9{5,}").fillna(False)
    return v.notna() & ((v <= 1.0) | nines | (v >= 9_999_999))


def _same_amount_every_code(df: pd.DataFrame, min_procs: int) -> pd.Series:
    """True for rows in a hospital x payer x plan list where >= min_procs different procedures
    (including both cheap labs and expensive surgery or stays) all have exactly one amount."""
    priced = df[df["amount_usd"].notna() & df["payer"].ne("")]
    g = priced.groupby(["ccn", "payer", "plan"])
    stats = g.agg(n_proc=("procedure_id", "nunique"), n_amt=("amount_usd", "nunique"))
    bad = stats[(stats["n_proc"] >= min_procs) & (stats["n_amt"] == 1)].index
    key = pd.MultiIndex.from_frame(df[["ccn", "payer", "plan"]])
    return pd.Series(key.isin(bad), index=df.index) & df["amount_usd"].notna()


def label_rows(df: pd.DataFrame, medicare: pd.DataFrame | None = None, t: dict | None = None) -> pd.DataFrame:
    t = {**THRESHOLDS, **(t or {})}
    df = df.copy()
    amt = df["amount_usd"].astype("float64")
    gross = df["gross_charge"].astype("float64")
    cash = df["cash_price"].astype("float64")
    mn, mx = df["min_charge"].astype("float64"), df["max_charge"].astype("float64")
    has_payer = df["payer"].fillna("").ne("")

    if medicare is not None and len(medicare):
        m = medicare[["ccn", "procedure_id", "medicare_rate", "source"]].rename(columns={"source": "medicare_source"})
        df = df.drop(columns=[c for c in ("medicare_rate", "medicare_source") if c in df.columns])
        df = df.merge(m, on=["ccn", "procedure_id"], how="left")
        df.index = amt.index
    else:
        df["medicare_rate"], df["medicare_source"] = np.nan, ""
    med = df["medicare_rate"].astype("float64")
    # per-diem rates aren't comparable with a whole-stay Medicare rate
    ratio = (amt / med).where(med.gt(0) & ~df["is_per_diem"].fillna(False).astype(bool))
    df["medicare_multiple"] = ratio.round(3)

    flags = {
        "placeholder_amount": has_payer & is_placeholder(amt),
        "formula_only": has_payer & amt.isna() & ~(df["rate_kind"].eq("percentage") & gross.isna()),
        "percent_no_gross": has_payer & amt.isna() & df["rate_kind"].eq("percentage") & gross.isna(),
        "same_amount_every_code": _same_amount_every_code(df, t["same_amount_min_procedures"]),
        "absurd_vs_medicare": ratio.lt(t["medicare_reject_low"]) | ratio.gt(t["medicare_reject_high"]),
        "percent_without_gross": df["amount_basis"].str.startswith("percent") & is_placeholder(gross),
        "above_gross": has_payer & gross.gt(0) & amt.gt(gross * t["tolerance"]) & ~is_placeholder(gross),
        "min_gt_max": mn.notna() & mx.notna() & mn.gt(mx),
        "outside_min_max": has_payer & mn.notna() & mx.notna() & mn.le(mx)
                           & (amt.lt(mn / t["tolerance"]) | amt.gt(mx * t["tolerance"])),
        "far_from_medicare": ratio.lt(t["medicare_suspect_low"]) | ratio.gt(t["medicare_suspect_high"]),
    }
    # market outlier: only where there is no Medicare rate to judge by
    ok = has_payer & amt.notna() & ~flags["placeholder_amount"] & df["comparable"].fillna(False).astype(bool)
    # compare like with like: same procedure AND same plan type (Medicaid / Medicare Advantage prices
    # are naturally lower and would drag an all-insurer median down, hiding low commercial outliers)
    keys = [df["procedure_id"], df["plan_type"].fillna("")] if "plan_type" in df.columns else [df["procedure_id"]]
    proc_median = amt.where(ok).groupby(keys).transform("median")
    mkt = amt / proc_median
    flags["market_outlier"] = ratio.isna() & ok & (mkt.gt(t["market_ratio"]) | mkt.lt(1 / t["market_ratio"]))
    df["market_multiple"] = mkt.round(3)
    # component lines need BOTH signals: the item's gross charge is under a tenth of other hospitals'
    # gross for the procedure, AND the price is under a quarter of other hospitals' prices for the
    # same plan type. (Gross charges alone vary legitimately, e.g. low lab chargemasters.)
    item_cols = [c for c in ("ccn", "procedure_id", "source_item") if c in df.columns]
    items = df[gross.gt(0) & ~is_placeholder(gross)].drop_duplicates(item_cols)
    gross_median = items.groupby("procedure_id")["gross_charge"].median()
    tiny_gross = gross.gt(0) & gross.lt(df["procedure_id"].map(gross_median) / t["market_ratio"])
    flags["component_line"] = tiny_gross & mkt.lt(t["component_price_ratio"])
    # an absurd Medicare multiple is reported once, as the stronger reason
    flags["far_from_medicare"] &= ~flags["absurd_vs_medicare"]

    # a percentage-of-charges conversion that overshoots the hospital's own range says more about
    # the conversion than about the hospital: say so in the reason
    pct = df["amount_basis"].fillna("").str.startswith("percent")
    texts = {k: v[1] for k, v in RULES.items()}
    df["quality_flags"], df["quality_reasons"], level = _combine(flags, RULES, df.index)
    conv = flags["outside_min_max"].fillna(False) & pct
    df.loc[conv, "quality_reasons"] = df.loc[conv, "quality_reasons"].str.replace(
        texts["outside_min_max"], "converted from % of charges; outside the file's own minimum-maximum "
        "of real negotiated rates (the percentage likely applies to the whole claim)", regex=False)
    df["quality_label"] = level.map({0: "good", 1: "suspect", 2: "rejected"})

    cash_flags = {
        "cash_placeholder": is_placeholder(cash),
        "cash_above_gross": cash.gt(gross * t["tolerance"]) & gross.gt(0) & ~is_placeholder(gross),
    }
    _, df["cash_reasons"], cl = _combine(cash_flags, CASH_RULES, df.index)
    df["cash_label"] = np.where(cash.isna(), "", cl.map({0: "good", 1: "suspect", 2: "rejected"}))
    return df


def _combine(flags: dict, rules: dict, index) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Vectorised: (flag keys joined by ';', reason texts joined by '; ', severity 0/1/2)."""
    keys = np.full(len(index), "", dtype=object)
    texts = np.full(len(index), "", dtype=object)
    level = np.zeros(len(index), dtype=int)
    for key, (sev, text) in rules.items():
        f = flags[key].fillna(False).astype(bool).to_numpy()
        keys = np.where(f, np.where(keys == "", key, keys + ";" + key), keys)
        texts = np.where(f, np.where(texts == "", text, texts + "; " + text), texts)
        level = np.where(f, np.maximum(level, 2 if sev == "rejected" else 1), level)
    return pd.Series(keys, index=index), pd.Series(texts, index=index), pd.Series(level, index=index)
