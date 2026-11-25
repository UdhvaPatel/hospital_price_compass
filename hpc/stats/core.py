"""Phase 5 statistics on the labelled prices.

Unit of analysis: one price per hospital x procedure x insurer x plan type ("hospital price"),
the median over that insurer's plans of that type at the hospital, using only prices labelled
good, comparable (same setting and scope) and canonical (duplicates removed).

  spread_table        range across hospitals per procedure x insurer x plan type, and for cash
  medicare_multiples  DRG prices / Medicare base rate
  market_index        price / Phoenix median for the same procedure and plan type
  insurer_index       fixed-effects comparison of insurers at the same hospitals and procedures
  cash_vs_insured     how often the cash price beats the insured price
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_HOSPITALS_FOR_PERCENTILES = 3


# ------------------------------------------------------------------ hospital-level prices

def usable(prices: pd.DataFrame) -> pd.DataFrame:
    return prices[prices["is_canonical"] & prices["comparable"] & prices["quality_label"].eq("good")
                  & prices["payer"].ne("") & prices["amount_usd"].notna() & prices["procedure_id"].ne("")]


def hospital_prices(prices: pd.DataFrame) -> pd.DataFrame:
    u = usable(prices)
    g = u.groupby(["ccn", "procedure_id", "payer_id", "payer_name", "plan_type"], dropna=False)
    out = g.agg(price=("amount_usd", "median"), plan_min=("amount_usd", "min"), plan_max=("amount_usd", "max"),
                n_plans=("plan", "nunique"), medicare_rate=("medicare_rate", "first")).reset_index()
    out["medicare_multiple"] = (out["price"] / out["medicare_rate"]).where(out["medicare_rate"].gt(0))
    return out


def cash_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """One cash price per hospital x procedure: from the item rows (primary codes, facility scope),
    labelled good; median if a hospital lists several."""
    c = prices[prices["is_canonical"] & prices["comparable"] & prices["cash_label"].eq("good")
               & prices["cash_price"].notna() & prices["procedure_id"].ne("")]
    prim = c[c["is_primary_code"].fillna(False).astype(bool)]
    c = prim if len(prim) else c
    items = c.drop_duplicates(["ccn", "procedure_id", "source_item", "cash_price"])
    return items.groupby(["ccn", "procedure_id"]).agg(cash=("cash_price", "median"),
                                                      gross=("gross_charge", "median")).reset_index()


def describe(values: pd.Series) -> dict:
    v = values.dropna().astype(float)
    n = len(v)
    d = {"n_hospitals": n, "median": float(v.median()) if n else None,
         "min": float(v.min()) if n else None, "max": float(v.max()) if n else None}
    if n >= MIN_HOSPITALS_FOR_PERCENTILES:
        d["p10"], d["p90"] = float(v.quantile(0.10)), float(v.quantile(0.90))
    else:
        d["p10"] = d["p90"] = None
    d["max_over_min"] = round(d["max"] / d["min"], 2) if n >= 2 and d["min"] else None
    return d


def spread_table(hp: pd.DataFrame, cash: pd.DataFrame, systems: dict[str, str] | None = None) -> pd.DataFrame:
    rows = []
    for (proc, pid, pname, ptype), g in hp.groupby(["procedure_id", "payer_id", "payer_name", "plan_type"]):
        d = describe(g["price"])
        d.update(procedure_id=proc, payer_id=pid, payer_name=pname, plan_type=ptype)
        if systems:
            d["n_systems"] = g["ccn"].map(systems).nunique()
        rows.append(d)
    for proc, g in cash.groupby("procedure_id"):
        d = describe(g["cash"])
        d.update(procedure_id=proc, payer_id="cash", payer_name="Cash price", plan_type="cash")
        if systems:
            d["n_systems"] = g["ccn"].map(systems).nunique()
        rows.append(d)
    cols = ["procedure_id", "payer_id", "payer_name", "plan_type", "n_hospitals", "n_systems", "median",
            "p10", "p90", "min", "max", "max_over_min"]
    return pd.DataFrame(rows).reindex(columns=[c for c in cols if systems or c != "n_systems"])


# ------------------------------------------------------------------ Medicare and market multiples

def medicare_multiples(hp: pd.DataFrame) -> dict[str, pd.DataFrame]:
    m = hp[hp["medicare_multiple"].notna()]
    by_type = m.groupby("plan_type")["medicare_multiple"].agg(
        n="size", median="median", p10=lambda s: s.quantile(0.1), p90=lambda s: s.quantile(0.9)).reset_index()
    by_payer = m[m["plan_type"].eq("commercial")].groupby(["payer_id", "payer_name"])["medicare_multiple"].agg(
        n="size", median="median").reset_index()
    by_hosp = m[m["plan_type"].eq("commercial")].groupby("ccn")["medicare_multiple"].agg(
        n="size", median="median").reset_index()
    by_proc = m.groupby(["procedure_id", "plan_type"])["medicare_multiple"].agg(n="size", median="median").reset_index()
    return {"by_plan_type": by_type, "by_insurer_commercial": by_payer, "by_hospital_commercial": by_hosp,
            "by_procedure": by_proc}


def market_index(hp: pd.DataFrame) -> pd.DataFrame:
    """Each hospital price / the Phoenix median for the same procedure and plan type (median of the
    hospital prices). A hospital's index is the geometric mean of its multiples."""
    hp = hp.copy()
    med = hp.groupby(["procedure_id", "plan_type"])["price"].transform("median")
    hp["market_multiple"] = hp["price"] / med
    return hp


def hospital_index(mi: pd.DataFrame, plan_type: str = "commercial", min_prices: int = 10) -> pd.DataFrame:
    s = mi[mi["plan_type"].eq(plan_type) & mi["market_multiple"].gt(0)]
    g = s.groupby("ccn")["market_multiple"]
    out = pd.DataFrame({"n_prices": g.size(), "price_index": np.exp(g.apply(lambda x: np.log(x).mean()))})
    return out[out["n_prices"] >= min_prices].reset_index()


# ------------------------------------------------------------------ insurer index

def insurer_index(hp: pd.DataFrame, plan_type: str = "commercial", min_hospitals: int = 5,
                  exclude: set[str] | None = None, systems: dict[str, str] | None = None) -> pd.DataFrame:
    """log(price) = cell[hospital x procedure] + insurer + error, cells with >= 2 insurers.

    The insurer effects are re-centred on their average, so index 1.00 = the average insurer and
    1.10 = 10% above average *at the same hospitals for the same procedures*: a cheap hospital
    can't make its insurers look good. `exclude`: payer ids left out (rental networks rent a network,
    they don't insure). Standard errors are clustered by hospital; if `systems` is given, a second,
    more conservative interval clustered by health system is added (hospitals in one system often
    share one price list, so they aren't independent)."""
    import statsmodels.formula.api as smf

    d = hp[hp["plan_type"].eq(plan_type) & hp["price"].gt(0) & ~hp["payer_id"].str.startswith("other")
           & ~hp["payer_id"].isin(exclude or set())].copy()
    keep = d.groupby("payer_id")["ccn"].nunique()
    d = d[d["payer_id"].isin(keep[keep >= min_hospitals].index)]
    d["cell"] = d["ccn"] + "|" + d["procedure_id"]
    multi = d.groupby("cell")["payer_id"].transform("nunique") >= 2
    d = d[multi].copy()
    if d["payer_id"].nunique() < 2:
        return pd.DataFrame()
    d["y"] = np.log(d["price"])
    # within-cell demeaning removes the hospital x procedure effects (same estimates as cell dummies)
    d["y_dm"] = d["y"] - d.groupby("cell")["y"].transform("mean")
    payers = sorted(d["payer_id"].unique())
    for p in payers:
        x = (d["payer_id"] == p).astype(float)
        d[f"x_{p}"] = x - x.groupby(d["cell"]).transform("mean")
    ref = payers[0]
    formula = "y_dm ~ 0 + " + " + ".join(f"x_{p}" for p in payers if p != ref)
    model = smf.ols(formula, data=d)
    fit = model.fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(d["ccn"])[0]})
    fit_sys = None
    if systems:
        fit_sys = model.fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(d["ccn"].map(systems).fillna(d["ccn"]))[0]})
    k = len(payers)
    # effect of each payer relative to the reference (0 for ref), then centred on the mean of all k
    names = [f"x_{p}" for p in payers if p != ref]
    rows = []
    for p in payers:
        L = np.zeros(len(names))
        for j, n in enumerate(names):
            L[j] -= 1.0 / k                     # minus the average effect
            if n == f"x_{p}":
                L[j] += 1.0
        t = fit.t_test(L)
        est, se = float(np.squeeze(t.effect)), float(np.squeeze(t.sd))
        row = {"payer_id": p, "index": np.exp(est), "ci_low": np.exp(est - 1.96 * se),
               "ci_high": np.exp(est + 1.96 * se), "log_effect": est, "se": se}
        if fit_sys is not None:
            se_s = float(np.squeeze(fit_sys.t_test(L).sd))
            row.update(ci_low_system=np.exp(est - 1.96 * se_s), ci_high_system=np.exp(est + 1.96 * se_s), se_system=se_s)
        rows.append({**row,
                     "n_prices": int((d["payer_id"] == p).sum()),
                     "n_hospitals": int(d.loc[d["payer_id"] == p, "ccn"].nunique())})
    out = pd.DataFrame(rows).sort_values("index").reset_index(drop=True)
    out.attrs.update(n_obs=int(fit.nobs), n_cells=int(d["cell"].nunique()), n_hospitals=int(d["ccn"].nunique()),
                     n_systems=int(d["ccn"].map(systems).nunique()) if systems else None, plan_type=plan_type)
    return out


# ------------------------------------------------------------------ cash vs insured

def cash_vs_insured(hp: pd.DataFrame, cash: pd.DataFrame, plan_type: str = "commercial") -> dict[str, pd.DataFrame]:
    ins = hp[hp["plan_type"].eq(plan_type)][["ccn", "procedure_id", "payer_id", "payer_name", "price"]]
    m = ins.merge(cash, on=["ccn", "procedure_id"], how="inner")
    m["cash_lower"] = m["cash"] < m["price"]
    cells = m.groupby(["ccn", "procedure_id"]).agg(cash=("cash", "first"), median_insured=("price", "median"),
                                                  share_insurers_above_cash=("cash_lower", "mean")).reset_index()
    cells["cash_below_median_insured"] = cells["cash"] < cells["median_insured"]
    overall = pd.DataFrame([{
        "pairs_hospital_procedure": len(cells),
        "share_cash_below_median_insured": float(cells["cash_below_median_insured"].mean()) if len(cells) else None,
        "insurer_prices_compared": len(m),
        "share_insurer_prices_above_cash": float(m["cash_lower"].mean()) if len(m) else None,
    }])
    by_proc = cells.groupby("procedure_id").agg(pairs=("ccn", "size"),
                                                share_cash_below_median_insured=("cash_below_median_insured", "mean"),
                                                median_cash=("cash", "median"),
                                                median_insured=("median_insured", "median")).reset_index()
    by_payer = m.groupby(["payer_id", "payer_name"]).agg(prices=("price", "size"),
                                                         share_above_cash=("cash_lower", "mean")).reset_index()
    by_hosp = cells.groupby("ccn").agg(pairs=("procedure_id", "size"),
                                       share_cash_below_median_insured=("cash_below_median_insured", "mean")).reset_index()
    return {"overall": overall, "by_procedure": by_proc, "by_insurer": by_payer, "by_hospital": by_hosp,
            "cells": cells}
