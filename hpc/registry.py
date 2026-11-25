"""Hospital registry: the metro's hospitals with CCN, location, health system and price-file location.

Built from the Phase 0 discovery results plus geocoding (US Census geocoder, public, no key).
Run:  python -m hpc.registry            (uses data/phase0/discovery.csv)
      python -m hpc.registry --rediscover  (runs discovery again first)
Writes data/registry/hospitals.csv and hospitals.json.
"""
from __future__ import annotations

import argparse
import json
import time

import pandas as pd
import requests

from hpc.config import DATA_DIR, PHASE0_DIR, metro_config, sites_config

REGISTRY_DIR = DATA_DIR / "registry"
REGISTRY_CSV = REGISTRY_DIR / "hospitals.csv"
GEOCODE_CACHE = REGISTRY_DIR / "geocode_cache.json"
GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

COLUMNS = [
    "ccn", "name", "health_system", "address", "city", "zip", "county", "lat", "lon", "geocode_source",
    "hospital_type", "ownership", "emergency_services", "star_rating", "in_scope", "exclusion_reason",
    "price_file_status", "mrf_url", "mrf_url_source", "file_format", "cms_version", "last_updated_on",
    "source_page_url", "hpt_status", "hpt_domain", "notes",
]


def price_file_status(row: pd.Series) -> str:
    """One word summary used downstream: usable / blocked / not_listed / not_found / out_of_scope."""
    if not row["in_scope"]:
        return "out_of_scope"
    if row.get("mrf_access") == "plain" and str(row.get("file_format", "")).startswith("cms_"):
        return "usable"
    if row.get("mrf_access") == "plain" and row.get("file_format"):
        return "non_standard"
    if row.get("hpt_status") == "blocked" or row.get("mrf_access") == "blocked":
        return "blocked"
    if row.get("hpt_status") == "location_not_listed":
        return "not_listed"
    return "not_found"


def health_system(system_key: str, name: str, sites: dict) -> str:
    s = sites.get("systems", {}).get(system_key or "")
    return s["display"] if s else "Independent"


_ABBREV = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "ROAD": "RD", "STREET": "ST",
    "AVENUE": "AVE", "BOULEVARD": "BLVD", "PARKWAY": "PKWY", "DRIVE": "DR", "LANE": "LN",
}
GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_zcta_national.zip"
GAZETTEER_FILE = DATA_DIR / "reference" / "zcta_gazetteer.txt"


def abbreviate(address: str) -> str:
    return " ".join(_ABBREV.get(w, w) for w in address.upper().split())


def _census_lookup(oneline: str, user_agent: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            GEOCODER_URL,
            params={"address": oneline, "benchmark": "Public_AR_Current", "format": "json"},
            headers={"User-Agent": user_agent},
            timeout=30,
        )
        matches = r.json()["result"]["addressMatches"]
    except (requests.RequestException, KeyError, ValueError):
        return None
    if not matches:
        return None
    c = matches[0]["coordinates"]
    return round(c["y"], 6), round(c["x"], 6)


def zip_centroids(user_agent: str) -> dict[str, tuple[float, float]]:
    """ZIP (ZCTA) centroid lat/lon from the Census Gazetteer file (downloaded once)."""
    if not GAZETTEER_FILE.exists():
        import io
        import zipfile
        r = requests.get(GAZETTEER_URL, headers={"User-Agent": user_agent}, timeout=120)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            GAZETTEER_FILE.parent.mkdir(parents=True, exist_ok=True)
            GAZETTEER_FILE.write_bytes(z.read(z.namelist()[0]))
    with open(GAZETTEER_FILE, encoding="utf-8-sig") as f:
        sep = "|" if "|" in f.readline() else "\t"  # 2025 uses '|', earlier years tab
    g = pd.read_csv(GAZETTEER_FILE, sep=sep, dtype=str)
    g.columns = [c.strip() for c in g.columns]
    return {
        z: (round(float(la), 6), round(float(lo), 6))
        for z, la, lo in zip(g["GEOID"], g["INTPTLAT"], g["INTPTLONG"])
    }


def geocode(address: str, city: str, state: str, zip_code: str, cache: dict, user_agent: str,
            centroids: dict | None = None) -> tuple:
    """Street-level coordinates from the Census geocoder; ZIP centroid as a flagged fallback."""
    key = f"{address}, {city}, {state} {zip_code}"
    if key in cache and cache[key][0] is not None:
        return tuple(cache[key])
    result = (None, None, "")
    for addr in dict.fromkeys([address, abbreviate(address)]):
        hit = _census_lookup(f"{addr}, {city}, {state} {zip_code}", user_agent)
        time.sleep(0.5)
        if hit:
            result = (*hit, "census_address")
            break
    if result[0] is None and centroids and zip_code in centroids:
        result = (*centroids[zip_code], "zip_centroid_approx")
    cache[key] = list(result)
    return result


def build_registry(discovery: pd.DataFrame, do_geocode: bool = True) -> pd.DataFrame:
    sites = sites_config()
    cfg = metro_config()
    df = discovery.copy()
    df["ccn"] = df["ccn"].astype(str).str.zfill(6)
    df["zip"] = df["zip"].astype(str).str.zfill(5)
    df["in_scope"] = df["in_scope"].astype(str).str.lower().eq("true")
    df = df.fillna({c: "" for c in df.columns if df[c].dtype == object})
    df["health_system"] = [health_system(s, n, sites) for s, n in zip(df["system"], df["name"])]
    df["price_file_status"] = df.apply(price_file_status, axis=1)

    cache = json.loads(GEOCODE_CACHE.read_text()) if GEOCODE_CACHE.exists() else {}
    ua = cfg["http"]["user_agent"]
    centroids = zip_centroids(ua) if do_geocode else {}
    lats, lons, srcs = [], [], []
    for r in df.itertuples():
        lat, lon, src = (
            geocode(r.address, r.city, r.state, r.zip, cache, ua, centroids)
            if do_geocode else (None, None, "")
        )
        lats.append(lat), lons.append(lon), srcs.append(src)
    df["lat"], df["lon"], df["geocode_source"] = lats, lons, srcs
    if do_geocode:
        GEOCODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        GEOCODE_CACHE.write_text(json.dumps(cache, indent=1))
    df["star_rating"] = pd.to_numeric(df["star_rating"], errors="coerce").astype("Int64")
    return df[COLUMNS].sort_values("ccn").reset_index(drop=True)


def load_registry() -> pd.DataFrame:
    return pd.read_csv(REGISTRY_CSV, dtype={"ccn": str, "zip": str}, keep_default_na=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rediscover", action="store_true", help="run Phase 0 discovery again first")
    ap.add_argument("--no-geocode", action="store_true")
    args = ap.parse_args()
    if args.rediscover:
        from hpc.discovery import run as discovery_run
        discovery_run.main()
    discovery = pd.read_csv(PHASE0_DIR / "discovery.csv", dtype=str, keep_default_na=False)
    reg = build_registry(discovery, do_geocode=not args.no_geocode)
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    reg.to_csv(REGISTRY_CSV, index=False)
    reg.to_json(REGISTRY_DIR / "hospitals.json", orient="records", indent=1)
    print(reg["price_file_status"].value_counts().to_string())
    print(reg["geocode_source"].value_counts().to_string())


if __name__ == "__main__":
    main()
