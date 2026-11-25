"""What explains price differences between hospitals?

Outcome: each hospital's commercial price index (geometric mean of its prices relative to the
Phoenix median for the same procedure; 1.00 = typical), on a log scale.

Candidate features
  CMS Hospital General Information: ownership, hospital type, star rating, emergency services
  FY 2026 IPPS impact file: beds, resident-to-bed ratio (teaching), share of low-income patients
                            (DSH), case-mix index
  derived: specialty hospital (physician-owned or a specialty name), health system, system size
           and the system's share of the metro's hospitals (market concentration)

Testing on held-out hospitals. With ~30 hospitals in ~10 systems, a feature only "survives" if a
model using it predicts *held-out* hospitals better than a model that just guesses the average:
  - leave-one-hospital-out (LOHO) for every feature
  - leave-one-system-out (LOSO) as well for everything except system membership, because
    hospitals in one system share one price list (holding out one Banner hospital still leaves
    12 identical ones in training, which flatters any feature that tracks Banner)
A feature is shown only if it lowers the held-out error under LOSO (system membership: LOHO,
labelled as such).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer

from hpc.reference.medicare import IPPS_DIR

IMPACT_FILE = IPPS_DIR / "FY 2026 IPPS Final Rule Impact File.txt"
SPECIALTY = re.compile(r"SPECIALTY|SPINE|ORTHO|HEART|CORE INSTITUTE|SURGICAL|CANCER", re.I)

FEATURES = {
    # name: (kind, description)
    "log_beds": ("num", "hospital size (beds, log scale)"),
    "resident_to_bed": ("num", "teaching intensity (residents per bed)"),
    "dsh_pct": ("num", "share of low-income patients (DSH %)"),
    "case_mix": ("num", "case-mix index (how complex its patients are)"),
    "star_rating": ("num", "CMS overall star rating"),
    "system_share": ("num", "system's share of Phoenix hospitals (market concentration)"),
    "ownership": ("cat", "ownership (non-profit / for-profit / government / physician)"),
    "specialty": ("cat", "specialty hospital (heart, orthopedic, spine, cancer, surgical)"),
    "hospital_type": ("cat", "hospital type (acute / children's / critical access)"),
    "system": ("cat", "health system membership"),
}


def load_impact(path=IMPACT_FILE) -> pd.DataFrame:
    if not path.exists():                       # fresh machine / CI runner: fetch the public CMS files
        from hpc.reference.medicare import download_ipps
        download_ipps()
    lines = path.read_text(encoding="latin-1").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Provider Number\t"))
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), sep="\t", dtype=str)
    out = pd.DataFrame({
        "ccn": df["Provider Number"].str.strip().str.zfill(6),
        "beds": pd.to_numeric(df["Beds"], errors="coerce"),
        "resident_to_bed": pd.to_numeric(df["Resident to Bed Ratio"], errors="coerce"),
        "dsh_pct": pd.to_numeric(df["DSHPCT"], errors="coerce"),
        "case_mix": pd.to_numeric(df["CMIV43"], errors="coerce"),
    })
    return out


def ownership_group(s: str) -> str:
    s = (s or "").lower()
    if "physician" in s:
        return "physician"
    if "proprietary" in s:
        return "for-profit"
    if "government" in s:
        return "government"
    return "non-profit"


def hospital_features(reg: pd.DataFrame, impact: pd.DataFrame) -> pd.DataFrame:
    r = reg[reg["in_scope"].astype(str).str.lower().eq("true")].copy()
    f = r[["ccn", "name", "health_system", "ownership", "hospital_type", "star_rating"]].merge(impact, on="ccn", how="left")
    f["log_beds"] = np.log(f["beds"])
    f["star_rating"] = pd.to_numeric(f["star_rating"], errors="coerce")
    f["ownership"] = f["ownership"].map(ownership_group)
    # specialty = what it does (by name), not who owns it: CMS labels some general hospitals "physician"-owned
    f["specialty"] = np.where(f["name"].str.contains(SPECIALTY), "yes", "no")
    f["hospital_type"] = f["hospital_type"].replace({"Acute Care Hospitals": "acute", "Childrens": "children's",
                                                     "Critical Access Hospitals": "critical access"})
    # independents are their own one-hospital "system"
    f["system"] = np.where(f["health_system"].eq("Independent"), f["name"], f["health_system"])
    counts = f["system"].value_counts()
    f["system_size"] = f["system"].map(counts)
    f["system_share"] = f["system_size"] / len(f)
    return f


def _model(features: list[str]):
    num = [c for c in features if FEATURES[c][0] == "num"]
    cat = [c for c in features if FEATURES[c][0] == "cat"]
    pre = ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), num),
        ("cat", OneHotEncoder(handle_unknown="ignore"), cat),
    ])
    return make_pipeline(pre, Ridge(alpha=1.0))


def cv_rmse(df: pd.DataFrame, features: list[str], groups: pd.Series) -> float:
    """Held-out RMSE (log scale). features=[] -> predict the training mean."""
    y = df["y"].to_numpy()
    errs = np.zeros(len(df))
    for tr, te in LeaveOneGroupOut().split(df, y, groups):
        if not features:
            pred = np.full(len(te), y[tr].mean())
        else:
            m = _model(features).fit(df.iloc[tr][features], y[tr])
            pred = m.predict(df.iloc[te][features])
        errs[te] = y[te] - pred
    return float(np.sqrt(np.mean(errs ** 2)))


@dataclass
class DriverResult:
    table: pd.DataFrame          # one row per feature
    combined: dict               # model of the surviving features
    data: pd.DataFrame


def explain(index: pd.DataFrame, feats: pd.DataFrame, min_gain: float = 0.02) -> DriverResult:
    """index: ccn, price_index. A feature survives if it cuts held-out RMSE by >= min_gain (2%)."""
    df = index.merge(feats, on="ccn", how="inner").reset_index(drop=True)
    df["y"] = np.log(df["price_index"])
    by_hosp, by_sys = df["ccn"], df["system"]
    base_h, base_s = cv_rmse(df, [], by_hosp), cv_rmse(df, [], by_sys)
    rows = []
    for name, (kind, desc) in FEATURES.items():
        rh = cv_rmse(df, [name], by_hosp)
        rs = None if name == "system" else cv_rmse(df, [name], by_sys)
        gain_h = 1 - rh / base_h
        gain_s = None if rs is None else 1 - rs / base_s
        survives = (gain_h >= min_gain) if name == "system" else (gain_s is not None and gain_s >= min_gain)
        # direction: in-sample slope / group means for display
        if kind == "num":
            x = df[name]
            ok = x.notna()
            varies = ok.sum() > 2 and x[ok].std() > 0 and df.loc[ok, "y"].std() > 0
            direction = float(np.corrcoef(x[ok], df.loc[ok, "y"])[0, 1]) if varies else np.nan
            detail = "higher prices" if direction > 0 else "lower prices"
        else:
            means = np.exp(df.groupby(name)["y"].mean()).round(2).to_dict()
            direction, detail = np.nan, "; ".join(f"{k}: {v}" for k, v in sorted(means.items(), key=lambda kv: kv[1]))
        rows.append({"feature": name, "description": desc, "rmse_loho": rh, "gain_loho": gain_h,
                     "rmse_loso": rs, "gain_loso": gain_s, "survives": survives,
                     "test": "leave-one-hospital-out" if name == "system" else "leave-one-system-out",
                     "correlation": direction, "detail": detail, "n_missing": int(df[name].isna().sum())})
    table = pd.DataFrame(rows).sort_values(["survives", "gain_loso"], ascending=[False, False])
    survivors = [f for f in table.loc[table["survives"], "feature"] if f != "system"]
    combined = {"features": survivors, "n_hospitals": len(df), "n_systems": int(df["system"].nunique()),
                "baseline_rmse_loso": base_s, "baseline_rmse_loho": base_h}
    if survivors:
        rs = cv_rmse(df, survivors, by_sys)
        combined.update(rmse_loso=rs, gain_loso=1 - rs / base_s)
        resid_var = np.mean((df["y"] - _model(survivors).fit(df[survivors], df["y"]).predict(df[survivors])) ** 2)
        combined["r2_in_sample"] = float(1 - resid_var / df["y"].var(ddof=0))
    return DriverResult(table=table, combined=combined, data=df)
