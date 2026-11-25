"""Export compact JSON for the static website (site/data/).

Run:  python -m hpc.site.build
Then preview:  python -m http.server 8000 -d site   ->  http://localhost:8000

Only aggregated, published-price data goes to the site (no raw files, no API keys).
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from hpc.config import DATA_DIR, ROOT, load_toml
from hpc.language.guard import SAFETY_NOTICE
from hpc.normalize.payers import PLAN_TYPES
from hpc.quality.rows import RULES
from hpc.registry import load_registry

SITE = ROOT / "site"
OUT = SITE / "data"
STATS = DATA_DIR / "stats"
TOP = ["uhc", "bcbs_az", "aetna", "cigna", "humana"]
# Phoenix metro bounding box (generous) for the ZIP lookup
BBOX = (32.4, 34.4, -113.6, -110.6)


def nice(name: str) -> str:
    return re.sub(r"'S\b", "'s", str(name).title())


def clean(o):
    """JSON-safe: NaN/inf -> None, numpy -> python, round floats."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if o is pd.NA or o is pd.NaT:
        return None
    return o


def write(name: str, obj) -> int:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean(obj), separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode())


def records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    return [dict(zip(cols, row)) for row in df[cols].itertuples(index=False, name=None)]


def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    procs = load_toml("procedures.toml")["procedure"]
    payer_cfg = {p["id"]: p for p in load_toml("payers.toml")["payer"]}
    reg = load_registry()
    reg["in_scope"] = reg["in_scope"].astype(str).str.lower().eq("true")
    scores = pd.DataFrame(json.loads((DATA_DIR / "quality" / "hospital_scores.json").read_text()))
    stats_summary = json.loads((STATS / "summary.json").read_text())
    hp = pd.read_parquet(STATS / "hospital_prices.parquet")
    cash = pd.read_csv(STATS / "cash_prices.csv", dtype={"ccn": str})
    spread = pd.read_csv(STATS / "spread.csv")
    hidx = pd.read_csv(STATS / "hospital_index.csv", dtype={"ccn": str})
    summaries = json.loads((DATA_DIR / "summaries" / "summaries.json").read_text(encoding="utf-8"))
    labeled = pd.read_parquet(DATA_DIR / "quality" / "prices_labeled.parquet",
                              columns=["ccn", "payer", "is_canonical", "comparable", "quality_label", "quality_flags",
                                       "file_date", "procedure_id"])
    as_of = stats_summary["run_date"]
    sizes = {}

    # ---------------------------------------------------------------- hospitals
    sc = scores.set_index("ccn")
    hi = hidx.set_index("ccn")
    hospitals = []
    for r in reg.itertuples():
        s = sc.loc[r.ccn] if r.ccn in sc.index else None
        pts = {k[4:]: s[k] for k in sc.columns if k.startswith("pts_")} if s is not None else {}
        hospitals.append({
            "ccn": r.ccn, "name": nice(r.name), "system": r.health_system, "city": str(r.city).title(),
            "zip": r.zip, "address": str(r.address).title(),
            "lat": r.lat if pd.notna(r.lat) else None, "lon": r.lon if pd.notna(r.lon) else None,
            "location_approx": r.geocode_source != "census_address",
            "type": r.hospital_type, "ownership": r.ownership,
            "star": int(r.star_rating) if pd.notna(pd.to_numeric(r.star_rating, errors="coerce")) else None,
            "emergency": str(r.emergency_services) == "Yes",
            "in_scope": bool(r.in_scope), "exclusion": r.exclusion_reason,
            "status": r.price_file_status,
            "score": s["score"] if s is not None else None, "grade": s["grade"] if s is not None else "",
            "points": pts, "findings": list(s["findings"]) if s is not None else [],
            "procedures_with_good_price": s.get("procedures_with_good_price") if s is not None else None,
            "price_index": hi.loc[r.ccn, "price_index"] if r.ccn in hi.index else None,
            "price_index_n": hi.loc[r.ccn, "n_prices"] if r.ccn in hi.index else None,
            "file": {"format": r.file_format, "version": r.cms_version,
                     "date": s["file_date"] if s is not None else "",
                     "url": r.mrf_url, "url_source": r.mrf_url_source, "page": r.source_page_url,
                     "index_status": r.hpt_status},
        })
    sizes["hospitals.json"] = write("hospitals.json", hospitals)

    # ---------------------------------------------------------------- meta
    insurer_counts = hp.groupby("payer_id")["ccn"].nunique()
    insurers = []
    for pid, n in insurer_counts.sort_values(ascending=False).items():
        if n < 3 or str(pid).startswith("other"):
            continue
        cfg = payer_cfg.get(pid, {})
        insurers.append({"id": pid, "name": cfg.get("name", pid), "parent": cfg.get("parent", ""),
                         "hospitals": int(n), "top": pid in TOP,
                         "rental_network": cfg.get("default_plan_type") == "rental_network"})
    n_good = labeled[labeled["is_canonical"] & labeled["comparable"] & labeled["payer"].ne("")]
    meta = {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metro": "Phoenix-Mesa-Chandler, AZ",
        "file_dates": [labeled["file_date"].replace("", np.nan).min(), labeled["file_date"].replace("", np.nan).max()],
        "counts": {
            "hospitals_in_scope": int(reg["in_scope"].sum()),
            "hospitals_with_prices": int(hp["ccn"].nunique()),
            "systems": stats_summary.get("systems"),
            "comparable_prices": int(len(n_good)),
            "good_prices": int((n_good["quality_label"] == "good").sum()),
            "suspect_prices": int((n_good["quality_label"] == "suspect").sum()),
            "rejected_prices": int((n_good["quality_label"] == "rejected").sum()),
        },
        "procedures": [{"id": p["id"], "name": p["name"], "category": p["category"], "family": p["family"],
                        "code": p["code"], "code_type": "MS-DRG" if p["family"] == "drg" else "CPT"} for p in procs],
        "insurers": insurers, "top_insurers": TOP,
        "plan_types": PLAN_TYPES,
        "safety_notice": SAFETY_NOTICE,
    }
    sizes["meta.json"] = write("meta.json", meta)

    # ---------------------------------------------------------------- per-procedure
    summ = {}
    for s in summaries:
        summ.setdefault(s["procedure_id"], {})[s["view"]] = {"text": s["text"], "source": s["source"].replace("cached:", "")}
    cvp = pd.read_csv(STATS / "cash_vs_insured_by_procedure.csv").set_index("procedure_id")
    rates = pd.read_csv(DATA_DIR / "reference" / "medicare" / "medicare_rates.csv", dtype={"ccn": str})
    for p in procs:
        pid = p["id"]
        ph = hp[hp["procedure_id"] == pid]
        pc = cash[cash["procedure_id"] == pid]
        mr = rates[(rates["procedure_id"] == pid) & rates["medicare_rate"].notna()]["medicare_rate"]
        obj = {
            "id": pid,
            "prices": records(ph, ["ccn", "payer_id", "plan_type", "price", "plan_min", "plan_max", "n_plans",
                                   "medicare_multiple"]),
            "cash": records(pc, ["ccn", "cash", "gross"]),
            "spread": records(spread[spread["procedure_id"] == pid],
                              ["payer_id", "plan_type", "n_hospitals", "n_systems", "median", "p10", "p90", "min", "max"]),
            "summaries": summ.get(pid, {}),
            "medicare_rate": float(mr.median()) if len(mr) else None,
            "cash_vs_insured": clean(cvp.loc[pid].to_dict()) if pid in cvp.index else None,
        }
        sizes[f"procedure/{pid}.json"] = write(f"procedure/{pid}.json", obj)

    # ---------------------------------------------------------------- per-hospital prices
    comm_med = hp[hp["plan_type"] == "commercial"].groupby("procedure_id")["price"].median()
    for ccn, g in hp.groupby("ccn"):
        rows = []
        for pid, gp in g.groupby("procedure_id"):
            c = gp[gp["plan_type"] == "commercial"]
            cash_row = cash[(cash["ccn"] == ccn) & (cash["procedure_id"] == pid)]
            rows.append({
                "procedure_id": pid,
                "commercial_median": c["price"].median() if len(c) else None,
                "commercial_insurers": int(c["payer_id"].nunique()),
                "phoenix_commercial_median": comm_med.get(pid),
                "cash": float(cash_row["cash"].iloc[0]) if len(cash_row) else None,
                "by_insurer": records(gp[gp["payer_id"].isin(TOP) | gp["plan_type"].eq("commercial")],
                                      ["payer_id", "plan_type", "price", "n_plans", "medicare_multiple"]),
            })
        sizes[f"hospital/{ccn}.json"] = write(f"hospital/{ccn}.json", {"ccn": ccn, "procedures": rows})

    # ---------------------------------------------------------------- insurers
    ii_c = pd.read_csv(STATS / "insurer_index_commercial.csv")
    ii_m = pd.read_csv(STATS / "insurer_index_medicare_advantage.csv")
    comm = hp[hp["plan_type"] == "commercial"]
    allmed = comm.groupby("procedure_id")["price"].median()
    by_insurer = {}
    for ins in insurers:
        sub = hp[hp["payer_id"] == ins["id"]]
        c = comm[comm["payer_id"] == ins["id"]].groupby("procedure_id")["price"].agg(["median", "size"])
        rel = [{"procedure_id": k, "median": v["median"], "hospitals": int(v["size"]),
                "phoenix_median": allmed.get(k), "ratio": v["median"] / allmed.get(k) if allmed.get(k) else None}
               for k, v in c.iterrows() if v["size"] >= 3]
        by_insurer[ins["id"]] = {
            "plan_types": sub.groupby("plan_type")["ccn"].nunique().to_dict(),
            "hospitals": sorted(sub["ccn"].unique().tolist()),
            "commercial_vs_phoenix": sorted(rel, key=lambda x: x["ratio"] or 0),
        }
    cols = ["payer_id", "index", "ci_low", "ci_high", "ci_low_system", "ci_high_system", "n_prices", "n_hospitals"]
    sizes["insurers.json"] = write("insurers.json", {
        "index": {"commercial": records(ii_c, cols), "medicare_advantage": records(ii_m, cols)},
        "attrs": stats_summary.get("insurer_index_attrs"),
        "by_insurer": by_insurer,
        "names": {k: v.get("name", k) for k, v in payer_cfg.items()},
    })

    # ---------------------------------------------------------------- quality
    canon = labeled[labeled["is_canonical"] & labeled["payer"].ne("")]
    flags = canon["quality_flags"].str.split(";").explode()
    counts = flags[flags.ne("")].value_counts()
    audit_file = DATA_DIR / "quality" / "audit_summary.json"
    sizes["quality.json"] = write("quality.json", {
        "labels": canon["quality_label"].value_counts().to_dict(),
        "comparable_labels": n_good["quality_label"].value_counts().to_dict(),
        "rules": [{"key": k, "severity": sev, "text": text, "count": int(counts.get(k, 0))} for k, (sev, text) in RULES.items()],
        "audit": json.loads(audit_file.read_text()) if audit_file.exists() else None,
    })

    # ---------------------------------------------------------------- stats
    def csv(name):
        return pd.read_csv(STATS / name)
    drivers = csv("drivers.csv")
    sizes["stats.json"] = write("stats.json", {
        "medicare_by_plan_type": csv("medicare_multiples_by_plan_type.csv").to_dict("records"),
        "medicare_by_insurer": csv("medicare_multiples_by_insurer_commercial.csv").to_dict("records"),
        "cash_vs_insured": csv("cash_vs_insured_overall.csv").iloc[0].to_dict(),
        "cash_vs_insured_by_procedure": csv("cash_vs_insured_by_procedure.csv").to_dict("records"),
        "drivers": drivers[["feature", "description", "test", "gain_loho", "gain_loso", "survives", "detail"]].to_dict("records"),
        "drivers_combined": json.loads((STATS / "drivers.json").read_text()),
        "changes": json.loads((STATS / "changes.json").read_text()),
        "summary": stats_summary,
    })

    # ---------------------------------------------------------------- ZIP centroids (metro only)
    g = DATA_DIR / "reference" / "zcta_gazetteer.txt"
    if g.exists():
        with open(g, encoding="utf-8-sig") as f:
            sep = "|" if "|" in f.readline() else "\t"
        z = pd.read_csv(g, sep=sep, dtype=str)
        z.columns = [c.strip() for c in z.columns]
        z["lat"], z["lon"] = pd.to_numeric(z["INTPTLAT"]), pd.to_numeric(z["INTPTLONG"])
        lo_lat, hi_lat, lo_lon, hi_lon = BBOX
        z = z[z["GEOID"].str.startswith("85") & z["lat"].between(lo_lat, hi_lat) & z["lon"].between(lo_lon, hi_lon)]
        sizes["zips.json"] = write("zips.json", {r.GEOID: [round(r.lat, 4), round(r.lon, 4)] for r in z.itertuples()})
    return sizes


def main() -> None:
    sizes = build()
    total = sum(sizes.values())
    print(f"wrote {len(sizes)} files, {total / 1024:,.0f} KB total; largest:")
    for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])[:5]:
        print(f"  {k}: {v / 1024:,.1f} KB")


if __name__ == "__main__":
    main()
