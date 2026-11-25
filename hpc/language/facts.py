"""Fact sheets: the only numbers a summary may use.

One fact sheet per procedure x view, where a view is one of the five default insurers
(commercial plans) or the cash price. Every number is pre-formatted exactly as it may appear in
the text ("$1,234", "54%", "3.4"), so the number guard can check the summary against them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from hpc.config import DATA_DIR, load_toml
from hpc.registry import load_registry

STATS = DATA_DIR / "stats"
TOP = ["uhc", "bcbs_az", "aetna", "cigna", "humana"]


def money(x: float) -> str:
    """$ with no cents above $100, cents below (lab tests cost $9.71, not $10)."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def times(x: float) -> str:
    return f"{x:.1f}"


def nice(name: str) -> str:
    import re
    return re.sub(r"'S\b", "'s", str(name).title())


def where(pts: pd.DataFrame, price: float, names: dict, systems: dict) -> str:
    """Who has this price: one hospital's name, or "12 Banner Health hospitals" for ties."""
    at = pts[np.isclose(pts["price"], price)]["ccn"].tolist()
    if len(at) == 1:
        return names.get(at[0], at[0])
    sys_ = {systems.get(c, "") for c in at}
    if len(sys_) == 1 and "" not in sys_:
        return f"{len(at)} {sys_.pop()} hospitals"
    return f"{len(at)} hospitals"


@dataclass
class FactSheet:
    id: str
    procedure_id: str
    view: str                     # insurer id, or "cash"
    facts: dict                   # name -> formatted string (or plain text)
    numbers: list[str] = field(default_factory=list)   # every numeric string allowed in the text

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.facts, sort_keys=True).encode()).hexdigest()[:16]


def _numbers(facts: dict) -> list[str]:
    import re
    out = []
    for v in facts.values():
        out += re.findall(r"\d[\d,]*(?:\.\d+)?", str(v))
    return sorted(set(out))


def build_fact_sheets(min_hospitals: int = 2) -> list[FactSheet]:
    procs = {p["id"]: p for p in load_toml("procedures.toml")["procedure"]}
    payers = {p["id"]: p["name"] for p in load_toml("payers.toml")["payer"]}
    reg = load_registry()
    names = {c: nice(n) for c, n in zip(reg["ccn"], reg["name"])}
    systems = {c: s for c, s in zip(reg["ccn"], reg["health_system"]) if s != "Independent"}
    spread = pd.read_csv(STATS / "spread.csv")
    hp = pd.read_parquet(STATS / "hospital_prices.parquet")
    cash = pd.read_csv(STATS / "cash_prices.csv", dtype={"ccn": str})
    cvp = pd.read_csv(STATS / "cash_vs_insured_by_procedure.csv").set_index("procedure_id")

    sheets = []
    for proc_id, proc in procs.items():
        for view in TOP + ["cash"]:
            if view == "cash":
                row = spread[(spread["procedure_id"] == proc_id) & (spread["payer_id"] == "cash")]
                pts = cash[cash["procedure_id"] == proc_id].rename(columns={"cash": "price"})
                label, plan = "the cash (self-pay) price", "cash"
            else:
                row = spread[(spread["procedure_id"] == proc_id) & (spread["payer_id"] == view)
                             & (spread["plan_type"] == "commercial")]
                pts = hp[(hp["procedure_id"] == proc_id) & (hp["payer_id"] == view) & (hp["plan_type"] == "commercial")]
                label, plan = payers.get(view, view), "commercial (employer or individual) plans"
            if row.empty or int(row.iloc[0]["n_hospitals"]) < min_hospitals:
                continue
            r = row.iloc[0]
            f = {
                "procedure": proc["name"],
                "who_pays": label,
                "plan": plan,
                "hospitals_with_usable_data": str(int(r["n_hospitals"])),
                "health_systems": str(int(r["n_systems"])),
                "median_price": money(r["median"]),
                "lowest_price": money(r["min"]),
                "lowest_price_where": where(pts, r["min"], names, systems),
                "highest_price": money(r["max"]),
                "highest_price_where": where(pts, r["max"], names, systems),
            }
            if pd.notna(r.get("p10")) and pd.notna(r.get("p90")):
                f["most_prices_fall_between_low"] = money(r["p10"])     # 10th percentile
                f["most_prices_fall_between_high"] = money(r["p90"])    # 90th percentile
            if pd.notna(r.get("max_over_min")) and r["max_over_min"] >= 1.5:
                f["highest_price_is_this_many_times_the_lowest"] = times(r["max_over_min"])
            if view != "cash" and proc["family"] == "drg":
                mm = pts["medicare_multiple"].dropna()
                if len(mm) >= 2:
                    f["median_price_is_this_many_times_the_medicare_rate"] = times(mm.median())
            if view == "cash" and proc_id in cvp.index:
                c = cvp.loc[proc_id]
                if c["pairs"] >= 3:
                    f["hospitals_compared_cash_vs_insured"] = str(int(c["pairs"]))
                    f["share_where_cash_is_below_typical_insured_price"] = pct(c["share_cash_below_median_insured"])
            sheets.append(FactSheet(id=f"{proc_id}__{view}", procedure_id=proc_id, view=view, facts=f,
                                    numbers=_numbers(f)))
    return sheets
