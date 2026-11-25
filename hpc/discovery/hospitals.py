"""Build the metro hospital list from CMS Hospital General Information."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from hpc.config import REFERENCE_DIR, metro_config

CMS_FILE = REFERENCE_DIR / "hospital_general_information.csv"

COLUMNS = {
    "Facility ID": "ccn",
    "Facility Name": "name",
    "Address": "address",
    "City/Town": "city",
    "State": "state",
    "ZIP Code": "zip",
    "County/Parish": "county",
    "Telephone Number": "phone",
    "Hospital Type": "hospital_type",
    "Hospital Ownership": "ownership",
    "Emergency Services": "emergency_services",
    "Hospital overall rating": "star_rating",
}


def download_cms_hospitals(dest: Path = CMS_FILE, force: bool = False) -> Path:
    """Download the latest CMS Hospital General Information CSV (public, no key)."""
    if dest.exists() and not force:
        return dest
    cfg = metro_config()["cms"]
    meta = requests.get(cfg["metastore_url"].format(id=cfg["hospital_dataset_id"]), timeout=60).json()
    url = meta["distribution"][0]["downloadURL"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    (dest.with_suffix(".source.txt")).write_text(
        f"url: {url}\nmodified: {meta.get('modified')}\n", encoding="utf-8"
    )
    return dest


def scope_reason(hospital_type: str, ownership: str, cfg: dict) -> str:
    """Return '' if in scope, otherwise the reason it is excluded."""
    if ownership in cfg["exempt_ownership"]:
        return "federal hospital, exempt from price transparency rule"
    if hospital_type not in cfg["in_scope_types"]:
        return f"type '{hospital_type}' does not offer the chosen procedures"
    return ""


def metro_hospitals(cms: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or metro_config()
    df = cms.rename(columns=COLUMNS)[list(COLUMNS.values())].copy()
    metro = cfg["metro"]
    df = df[(df["state"] == metro["state"]) & (df["county"].str.upper().isin(metro["counties"]))]
    df["star_rating"] = pd.to_numeric(df["star_rating"], errors="coerce").astype("Int64")
    df["exclusion_reason"] = [
        scope_reason(t, o, cfg["scope"]) for t, o in zip(df["hospital_type"], df["ownership"])
    ]
    df["in_scope"] = df["exclusion_reason"] == ""
    return df.sort_values("ccn").reset_index(drop=True)


def load_metro_hospitals(force_download: bool = False) -> pd.DataFrame:
    path = download_cms_hospitals(force=force_download)
    return metro_hospitals(pd.read_csv(path, dtype=str))
