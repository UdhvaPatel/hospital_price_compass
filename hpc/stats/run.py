"""Phase 5 runner: all statistics, written for the website and the summaries.

Run:  python -m hpc.stats.run
Out:  data/stats/
        hospital_prices.parquet      one price per hospital x procedure x insurer x plan type
        cash_prices.csv              one cash price per hospital x procedure
        spread.csv                   range across hospitals (and systems) per procedure x insurer x plan type
        medicare_multiples_*.csv     DRG prices / Medicare base rate
        hospital_index.csv           each hospital's commercial price level vs the Phoenix median
        insurer_index_*.csv          fixed-effects insurer comparison (commercial, Medicare Advantage)
        cash_vs_insured_*.csv        how often cash beats insured
        drivers.csv, drivers.json    what explains price differences (held-out tests)
        changes.json                 change over time (baseline on the first run)
        summary.json                 headline numbers
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from hpc.config import DATA_DIR, load_toml
from hpc.registry import load_registry
from hpc.stats import core, drivers, history

OUT = DATA_DIR / "stats"


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else round(float(o), 4)
    if isinstance(o, np.integer):
        return int(o)
    return o


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = pd.read_parquet(DATA_DIR / "quality" / "prices_labeled.parquet")
    p = p.fillna({c: "" for c in p.columns if str(p[c].dtype) in ("string", "object")})
    reg = load_registry()
    systems = dict(zip(reg["ccn"], np.where(reg["health_system"].eq("Independent"), reg["name"], reg["health_system"])))
    names = dict(zip(reg["ccn"], reg["name"]))
    rental = {x["id"] for x in load_toml("payers.toml")["payer"] if x.get("default_plan_type") == "rental_network"}

    hp = core.hospital_prices(p)
    cash = core.cash_prices(p)
    hp.to_parquet(OUT / "hospital_prices.parquet", index=False)
    cash.to_csv(OUT / "cash_prices.csv", index=False)

    spread = core.spread_table(hp, cash, systems)
    spread.to_csv(OUT / "spread.csv", index=False)

    mm = core.medicare_multiples(hp)
    for k, v in mm.items():
        v.to_csv(OUT / f"medicare_multiples_{k}.csv", index=False)

    mi = core.market_index(hp)
    hi = core.hospital_index(mi)
    hi["name"] = hi["ccn"].map(names)
    hi["system"] = hi["ccn"].map(systems)
    hi.sort_values("price_index").to_csv(OUT / "hospital_index.csv", index=False)

    ii = {}
    for pt in ("commercial", "medicare_advantage"):
        t = core.insurer_index(hp, pt, exclude=rental, systems=systems)
        t.to_csv(OUT / f"insurer_index_{pt}.csv", index=False)
        ii[pt] = {"table": t, "attrs": dict(t.attrs)}

    cv = core.cash_vs_insured(hp, cash)
    for k in ("overall", "by_procedure", "by_insurer", "by_hospital"):
        cv[k].to_csv(OUT / f"cash_vs_insured_{k}.csv", index=False)

    feats = drivers.hospital_features(reg, drivers.load_impact())
    dr = drivers.explain(hi[["ccn", "price_index"]], feats)
    dr.table.to_csv(OUT / "drivers.csv", index=False)
    (OUT / "drivers.json").write_text(json.dumps(_clean(dr.combined), indent=2), encoding="utf-8")

    file_dates = p.groupby("ccn")["file_date"].max().to_dict()
    history.save_snapshot(hp, file_dates, date.today().isoformat())
    ch = history.compare_latest()
    ch_out = {k: v for k, v in ch.items() if not isinstance(v, pd.DataFrame)}
    (OUT / "changes.json").write_text(json.dumps(_clean(ch_out), indent=2), encoding="utf-8")

    comm = ii["commercial"]["table"].set_index("payer_id")
    summary = {
        "run_date": date.today().isoformat(),
        "hospital_prices": len(hp), "hospitals": int(hp["ccn"].nunique()),
        "systems": int(hp["ccn"].map(systems).nunique()),
        "medicare_multiple_median": mm["by_plan_type"].set_index("plan_type")["median"].to_dict(),
        "insurer_index_commercial": comm["index"].to_dict(),
        "insurer_index_commercial_ci_system": {k: [r["ci_low_system"], r["ci_high_system"]] for k, r in comm.iterrows()},
        "insurer_index_attrs": ii["commercial"]["attrs"],
        "hospital_index_range": [float(hi["price_index"].min()), float(hi["price_index"].max())],
        "cash_vs_insured": cv["overall"].iloc[0].to_dict(),
        "drivers_surviving": dr.table.loc[dr.table["survives"], "feature"].tolist(),
        "drivers_combined": dr.combined,
        "changes_status": ch["status"],
    }
    (OUT / "summary.json").write_text(json.dumps(_clean(summary), indent=2), encoding="utf-8")
    print(json.dumps(_clean(summary), indent=2))


if __name__ == "__main__":
    main()
