"""Medicare reference rates for the "multiple of Medicare" baseline.

Inpatient (MS-DRG), FY 2026 IPPS final rule (CMS-1833-F), public files, downloaded automatically:
    base rate = DRG weight x [ operating labor x wage index + operating nonlabor
                               + capital rate x GAF ]
    using each hospital's own FY 2026 wage index (Table 2) and GAF = wage index ^ 0.6848.
    Add-ons (teaching, DSH, outliers) are left out: this is the *base* Medicare rate.

Outpatient (CPT/HCPCS), CY 2026 OPPS Addendum B, and labs, CY 2026 Clinical Lab Fee Schedule:
    these files contain CPT codes and sit behind CMS's AMA CPT licence click-through, so they
    are downloaded by a person (see steps.md) into data/reference/medicare/opps_cy2026/ and
    data/reference/medicare/clfs_2026/. When present:
        OPPS rate = national payment x (0.60 x wage index + 0.40)
        lab rate  = CLFS national limitation amount (not wage-adjusted)
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import requests

from hpc.config import DATA_DIR, load_toml, metro_config

MEDICARE_DIR = DATA_DIR / "reference" / "medicare"
IPPS_DIR = MEDICARE_DIR / "ipps_fy2026"
OPPS_DIR = MEDICARE_DIR / "opps_cy2026"
CLFS_DIR = MEDICARE_DIR / "clfs_2026"
RATES_FILE = MEDICARE_DIR / "medicare_rates.csv"

IPPS_FILES = ["fy2026-ipps-fr-table-5.zip", "fy2026-ipps-fr-table-1a-1e.zip", "fy2026-ipps-fr-tables-2-3-4a-4b.zip",
              "fy2026-ipps-fr-impact-file.zip"]   # impact file: beds, teaching, DSH (used in Phase 5 drivers)
IPPS_BASE_URL = "https://www.cms.gov/files/zip/"
OPPS_LABOR_SHARE = 0.60
CAPITAL_GAF_EXPONENT = 0.6848


def _money(s: str) -> float:
    return float(re.sub(r"[^0-9.]", "", s))


def download_ipps(dest: Path = IPPS_DIR) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    ua = metro_config()["http"]["user_agent"]
    for name in IPPS_FILES:
        if any(dest.glob("CMS-1833-F*")) and (dest / name).exists():
            continue
        r = requests.get(IPPS_BASE_URL + name, headers={"User-Agent": ua}, timeout=120)
        r.raise_for_status()
        (dest / name).write_bytes(r.content)
        zipfile.ZipFile(io.BytesIO(r.content)).extractall(dest)


def _read_tsv(path: Path, *header_tokens: str) -> pd.DataFrame:
    """CMS .txt tables: title line(s), then a tab-separated header row containing all tokens."""
    lines = path.read_text(encoding="latin-1").splitlines()
    start = next(i for i, l in enumerate(lines) if all(t in l for t in header_tokens))
    return pd.read_csv(io.StringIO("\n".join(lines[start:])), sep="\t", dtype=str)


def ipps_inputs(d: Path = IPPS_DIR) -> dict:
    t1 = (d / "CMS-1833-F Tables 1A - 1E.txt").read_text(encoding="latin-1")
    money = re.findall(r"\$([\d,]+\.\d+)", t1)
    # Table 1A (wage index > 1) and 1B (<= 1): first pair = hospitals with quality data + EHR use
    a_lab, a_non = _money(money[0]), _money(money[1])
    b_lab, b_non = _money(money[8]), _money(money[9])
    capital = _money(re.search(r"TABLE 1D.*?National\s+\$([\d,]+\.\d+)", t1, re.S).group(1))

    t5 = _read_tsv(d / "CMS-1833-F Table 5.txt", "MS-DRG", "Weights")
    t5.columns = [c.strip() for c in t5.columns]
    wcol = next(c for c in t5.columns if "10% Cap" in c)
    t5["_w"] = pd.to_numeric(t5[wcol], errors="coerce")       # retired DRGs show "." instead of a weight
    weights = {str(int(r["MS-DRG"])): float(r["_w"]) for _, r in t5.iterrows()
               if str(r["MS-DRG"]).strip().isdigit() and pd.notna(r["_w"])}

    t2 = _read_tsv(d / "CMS-1833-F Tables 2.txt", "1CCN", "Wage Index")
    ccn_col = t2.columns[0]
    wi_col = next(c for c in t2.columns if "2026 Wage Index With Cap" in c)
    t2["_wi"] = pd.to_numeric(t2[wi_col], errors="coerce")
    wage = {str(r[ccn_col]).strip().zfill(6): float(r["_wi"]) for _, r in t2.iterrows() if pd.notna(r["_wi"])}
    t3 = _read_tsv(d / "CMS-1833-F Tables 3.txt", "CBSA\tArea Name")
    t3["_wi"] = pd.to_numeric(t3["Wage Index"], errors="coerce")
    cbsa_wi = {str(r["CBSA"]).strip(): float(r["_wi"]) for _, r in t3.iterrows() if pd.notna(r["_wi"])}
    return {"op_gt1": (a_lab, a_non), "op_le1": (b_lab, b_non), "capital": capital,
            "weights": weights, "wage_by_ccn": wage, "wage_by_cbsa": cbsa_wi}


def ipps_base_rate(weight: float, wage_index: float, inputs: dict) -> float:
    lab, non = inputs["op_gt1"] if wage_index > 1 else inputs["op_le1"]
    gaf = wage_index ** CAPITAL_GAF_EXPONENT
    return weight * (lab * wage_index + non + inputs["capital"] * gaf)


def _load_opps(d: Path = OPPS_DIR) -> pd.DataFrame | None:
    """Addendum B: HCPCS, status indicator, APC, payment rate. Accepts the CMS .xlsx or .csv."""
    files = sorted(list(d.glob("*.xlsx")) + list(d.glob("*.csv"))) if d.exists() else []
    for f in files:
        raw = pd.read_excel(f, header=None, dtype=str) if f.suffix == ".xlsx" else pd.read_csv(f, header=None, dtype=str)
        hdr = next((i for i in range(min(20, len(raw))) if raw.iloc[i].astype(str).str.contains("HCPCS", case=False).any()), None)
        if hdr is None:
            continue
        df = raw.iloc[hdr + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr]]
        code = next(c for c in df.columns if "HCPCS" in c.upper())
        si = next((c for c in df.columns if c.upper() in ("SI", "STATUS INDICATOR")), None)
        rate = next(c for c in df.columns if "PAYMENT RATE" in c.upper())
        out = pd.DataFrame({"code": df[code].astype(str).str.strip().str.upper(),
                            "si": df[si].astype(str).str.strip() if si else "",
                            "rate": pd.to_numeric(df[rate].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")})
        return out.dropna(subset=["rate"])
    return None


def _load_clfs(d: Path = CLFS_DIR) -> pd.DataFrame | None:
    files = sorted(list(d.glob("*.csv")) + list(d.glob("*.xlsx")) + list(d.glob("*.txt"))) if d.exists() else []
    for f in files:
        if f.suffix == ".xlsx":
            raw = pd.read_excel(f, header=None, dtype=str)
        else:
            raw = pd.read_csv(f, header=None, dtype=str, sep=None, engine="python", encoding="latin-1",
                              on_bad_lines="skip")
        hdr = next((i for i in range(min(20, len(raw))) if raw.iloc[i].astype(str).str.upper().str.contains("HCPCS").any()), None)
        if hdr is None:
            continue
        df = raw.iloc[hdr + 1:].copy()
        df.columns = [str(c).strip().upper() for c in raw.iloc[hdr]]
        code = next(c for c in df.columns if "HCPCS" in c)
        rate = next(c for c in df.columns if c in ("RATE", "NLA", "NATIONAL LIMIT") or "RATE" in c)
        mod = next((c for c in df.columns if c.startswith("MOD")), None)
        out = pd.DataFrame({"code": df[code].astype(str).str.strip().str.upper(),
                            "mod": df[mod].astype(str).str.strip() if mod else "",
                            "rate": pd.to_numeric(df[rate].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")})
        out = out[out["mod"].isin(["", "nan", "None"])]
        return out.dropna(subset=["rate"]).drop_duplicates("code")
    return None


def build_rates(ccns: list[str]) -> pd.DataFrame:
    """One row per hospital x procedure with a Medicare base rate (where one can be computed)."""
    procs = load_toml("procedures.toml")["procedure"]
    download_ipps()
    ipps = ipps_inputs()
    phoenix_wi = ipps["wage_by_cbsa"].get("38060", 1.0)
    opps, clfs = _load_opps(), _load_clfs()
    rows = []
    for ccn in ccns:
        wi = ipps["wage_by_ccn"].get(ccn, phoenix_wi)
        wi_src = "table2_ccn" if ccn in ipps["wage_by_ccn"] else "table3_cbsa_38060"
        for p in procs:
            code, rate, source, note = p["code"], None, "", ""
            if p["family"] == "drg":
                w = ipps["weights"].get(str(int(code)))
                if w:
                    rate, source = ipps_base_rate(w, wi, ipps), "ipps_fy2026"
                    note = f"weight {w}, wage index {wi} ({wi_src})"
            else:
                if opps is not None:
                    hit = opps[opps["code"] == code.upper()]
                    if len(hit) and hit.iloc[0]["rate"] > 0:
                        rate = hit.iloc[0]["rate"] * (OPPS_LABOR_SHARE * wi + 1 - OPPS_LABOR_SHARE)
                        source, note = "opps_cy2026", f"SI {hit.iloc[0]['si']}, wage index {wi}"
                if rate is None and clfs is not None:
                    hit = clfs[clfs["code"] == code.upper()]
                    if len(hit):
                        rate, source, note = float(hit.iloc[0]["rate"]), "clfs_2026", "national limitation amount"
            rows.append({"ccn": ccn, "procedure_id": p["id"], "code": code,
                         "medicare_rate": round(rate, 2) if rate else None, "source": source, "note": note})
    df = pd.DataFrame(rows)
    MEDICARE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(RATES_FILE, index=False)
    return df


def load_rates() -> pd.DataFrame:
    return pd.read_csv(RATES_FILE, dtype={"ccn": str})
