"""Change over time: keep a snapshot of hospital prices per run and compare the latest two.

A price only counts as changed if the hospital's file date changed too (otherwise any difference
comes from our own processing, not the hospital).
"""
from __future__ import annotations

import pandas as pd

from hpc.config import DATA_DIR

HISTORY_DIR = DATA_DIR / "history"
KEY = ["ccn", "procedure_id", "payer_id", "plan_type"]


def save_snapshot(hp: pd.DataFrame, file_dates: dict[str, str], run_date: str, directory=HISTORY_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    s = hp[KEY + ["price"]].copy()
    s["file_date"] = s["ccn"].map(file_dates).fillna("")
    s.to_parquet(directory / f"hospital_prices_{run_date}.parquet", index=False)


def snapshots(directory=HISTORY_DIR) -> list:
    return sorted(directory.glob("hospital_prices_*.parquet")) if directory.exists() else []


def compare(old: pd.DataFrame, new: pd.DataFrame, threshold: float = 0.005) -> dict:
    m = old.merge(new, on=KEY, suffixes=("_old", "_new"))
    m = m[m["file_date_old"].ne(m["file_date_new"])]            # only hospitals that published a new file
    m["change"] = m["price_new"] / m["price_old"] - 1
    m["direction"] = pd.cut(m["change"], [-float("inf"), -threshold, threshold, float("inf")],
                            labels=["down", "unchanged", "up"])
    by_hosp = m.groupby("ccn").agg(prices=("change", "size"), median_change=("change", "median"),
                                   share_up=("direction", lambda d: (d == "up").mean())).reset_index()
    return {
        "compared": int(len(m)),
        "hospitals_with_new_file": int(m["ccn"].nunique()),
        "share_up": float((m["direction"] == "up").mean()) if len(m) else None,
        "share_down": float((m["direction"] == "down").mean()) if len(m) else None,
        "median_change": float(m["change"].median()) if len(m) else None,
        "by_hospital": by_hosp,
        "largest": m.reindex(m["change"].abs().sort_values(ascending=False).index).head(50),
    }


def compare_latest(directory=HISTORY_DIR) -> dict:
    snaps = snapshots(directory)
    if len(snaps) < 2:
        return {"status": "baseline", "snapshots": [s.stem.split("_")[-1] for s in snaps],
                "note": "First snapshot saved; changes appear after the next refresh with a new file."}
    old, new = pd.read_parquet(snaps[-2]), pd.read_parquet(snaps[-1])
    out = compare(old, new)
    out.update(status="compared", old=snaps[-2].stem.split("_")[-1], new=snaps[-1].stem.split("_")[-1])
    return out
