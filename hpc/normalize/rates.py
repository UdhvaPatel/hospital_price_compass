"""Rate types, settings, package vs line item, and duplicate removal.

Adds to each price row:
  amount_usd        dollars: the negotiated dollar amount, or percentage x gross charge
  amount_basis      negotiated_dollar / percent_of_gross / none
  is_per_diem       per-day rate (never compared with whole-stay or per-service prices)
  price_setting     inpatient / outpatient / both / professional / ... (contract suffix wins)
  price_scope       facility / professional / facility_stay (DRG: the hospital's part of a stay)
  is_package        case rate covering a whole surgical case rather than one line item
  comparable        usable in like-for-like comparisons (+ comparable_reason when not)
  is_canonical      the one row kept per hospital x procedure x payer x plan x scope
  n_variants        how many rows were collapsed into it; variant_min / variant_max their range
"""
from __future__ import annotations

import numpy as np
import pandas as pd

INPATIENT_FAMILIES = {"drg"}
# Contract suffixes (Wickenburg) that are not the hospital-outpatient price; "clinic" is the
# provider-based clinic contract, kept out so each insurer has one outpatient price.
NOT_OUTPATIENT_CONTRACTS = {"inpatient", "professional", "recurring", "swing_bed", "clinic"}


def to_dollars(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    pct = df["negotiated_percentage"].astype("float64")
    # a few files write 53% as 0.53: treat 0 < p <= 1 as a fraction (flagged in amount_basis)
    frac = pct.gt(0) & pct.le(1)
    pct_scaled = pct.where(~frac, pct * 100)
    gross = df["gross_charge"].astype("float64")
    from_pct = (pct_scaled / 100 * gross).where(gross.gt(0))
    has_dollar = df["amount"].notna()
    df["amount_usd"] = df["amount"].where(has_dollar, from_pct)
    df["amount_basis"] = np.select(
        [has_dollar, from_pct.notna() & frac, from_pct.notna()],
        ["negotiated_dollar", "percent_of_gross_scaled", "percent_of_gross"], "none")
    df["is_per_diem"] = df["methodology"].fillna("").str.contains("per diem", case=False)
    return df


def settings_and_scope(df: pd.DataFrame, families: dict[str, str]) -> pd.DataFrame:
    """families: procedure_id -> 'cpt' / 'drg'."""
    df = df.copy()
    fam = df["procedure_id"].map(families).fillna("")
    contract = df.get("contract_setting", pd.Series("", index=df.index)).fillna("")
    setting = df["setting"].fillna("").str.lower()
    df["price_setting"] = contract.where(contract != "", setting)
    prof = (df["billing_class"].fillna("").str.lower().eq("professional")
            | df.get("plan_professional_hint", pd.Series(False, index=df.index)).fillna(False).astype(bool)
            | contract.eq("professional"))
    df["price_scope"] = np.select([fam.eq("drg"), prof], ["facility_stay", "professional"], "facility")
    df["is_package"] = fam.eq("cpt") & df["methodology"].fillna("").str.contains("case rate", case=False)

    reason = pd.Series("", index=df.index)
    ps = df["price_setting"]
    reason = reason.mask(fam.eq("drg") & ps.isin(["outpatient", "professional", "clinic"]), "DRG priced as outpatient")
    reason = reason.mask(fam.eq("cpt") & (ps.eq("inpatient") | contract.isin(NOT_OUTPATIENT_CONTRACTS)),
                         "outpatient service priced for an inpatient / other contract")
    reason = reason.mask(df["is_per_diem"], "per-day rate")
    reason = reason.mask(df["price_scope"].eq("professional"), "professional (doctor) fee, not hospital fee")
    reason = reason.mask(df["amount_usd"].isna() & df["payer"].fillna("").ne(""), "no dollar amount (formula only)")
    reason = reason.mask(df["procedure_id"].fillna("").eq(""), "description not matched to a procedure")
    df["comparable_reason"] = reason
    df["comparable"] = reason.eq("")
    return df


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one canonical row per hospital x procedure x payer x plan x setting x scope.

    Preference: comparable > primary code > matched by code > no modifiers > has a dollar amount.
    If several rows remain with different amounts (e.g. separate chargemaster lines for the same
    CPT code), the one closest to their median is kept and the range is recorded."""
    df = df.copy()
    df["_mods"] = df["modifiers"].fillna("").str.strip().ne("")
    df["_rank"] = (
        (~df["comparable"]).astype(int) * 16
        + (~df["is_primary_code"].fillna(False).astype(bool)).astype(int) * 8
        + df["match_method"].fillna("code").ne("code").astype(int) * 4
        + df["_mods"].astype(int) * 2
        + df["amount_usd"].isna().astype(int)
    )
    key = ["ccn", "procedure_id", "payer", "plan", "price_setting", "price_scope", "is_per_diem"]
    df = df.sort_values(key + ["_rank", "source_item"], kind="mergesort")
    best_rank = df.groupby(key, dropna=False)["_rank"].transform("min")
    top = df["_rank"].eq(best_rank)

    # value to compare: the price itself, or the cash price on rows without a payer
    value = df["amount_usd"].fillna(df["cash_price"]).fillna(df["gross_charge"])
    df["_value"] = value
    grp = df[top].groupby(key, dropna=False)["_value"]
    med = grp.transform("median")
    df.loc[top, "_dist"] = (df.loc[top, "_value"] - med).abs()
    df["n_variants"] = df.groupby(key, dropna=False)["_value"].transform("size")
    df["variant_min"] = df.groupby(key, dropna=False)["_value"].transform("min")
    df["variant_max"] = df.groupby(key, dropna=False)["_value"].transform("max")
    cand = df[top].sort_values(key + ["_dist", "source_item"], kind="mergesort")
    keep_idx = cand.groupby(key, dropna=False).head(1).index
    df["is_canonical"] = df.index.isin(keep_idx)
    return df.drop(columns=["_mods", "_rank", "_value", "_dist"])
