"""Phase 0 discovery: hospital list -> cms-hpt.txt -> price file probe.

Run:  python -m hpc.discovery.run
Writes data/phase0/discovery.csv, data/phase0/hpt_files/*.txt and data/phase0/summary.json.
Price files are NOT downloaded: only the first 64 KB is read to identify the format.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import pandas as pd
import requests

from hpc.config import PHASE0_DIR, metro_config, sites_config
from hpc.discovery.hospitals import load_metro_hospitals
from hpc.discovery.hpt import HptEntry, looks_like_hpt, match_location, parse_hpt
from hpc.discovery.sniff import HTML, sniff

HEAD_BYTES = 65536
BLOCK_MARKERS = ("just a moment", "access denied", "attention required", "captcha", "cf-chl")


class Fetcher:
    """Polite HTTP with an honest, identifying user agent.

    We never disguise the client as a browser: if a site's bot protection refuses us,
    that is recorded as 'blocked' (it means the file is not machine-readable in practice).
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.plain = {"User-Agent": cfg["user_agent"]}
        self.session = requests.Session()

    @staticmethod
    def _blocked(status: int, body: bytes) -> bool:
        if status in (401, 403, 429, 503):
            return True
        low = body[:3000].decode("latin-1").lower()
        return "<html" in low and any(m in low for m in BLOCK_MARKERS)

    def get_head(self, url: str, nbytes: int = HEAD_BYTES) -> dict:
        """GET the first `nbytes`. Returns status, headers, body, access, final url, error."""
        result = {"status": None, "headers": {}, "body": b"", "access": "", "url": url, "error": ""}
        for access, headers in (("plain", self.plain),):
            time.sleep(self.cfg["delay_seconds"])
            try:
                with self.session.get(
                    url,
                    headers={**headers, "Range": f"bytes=0-{nbytes - 1}"},
                    stream=True,
                    timeout=self.cfg["timeout_seconds"],
                    allow_redirects=True,
                ) as r:
                    body = b""
                    for chunk in r.iter_content(8192):
                        body += chunk
                        if len(body) >= nbytes:
                            break
                    result.update(
                        status=r.status_code, headers=dict(r.headers), body=body[:nbytes],
                        url=r.url, access=access, error="",
                    )
            except requests.RequestException as exc:
                result.update(error=f"{type(exc).__name__}: {exc}"[:300], access=access)
                continue
            if not self._blocked(result["status"], result["body"]):
                return result
        result["access"] = "blocked" if result["status"] else "error"
        return result


def total_size(headers: dict, status: int | None) -> int | None:
    cr = headers.get("Content-Range") or headers.get("content-range")
    if cr and "/" in cr and not cr.endswith("/*"):
        return int(cr.rsplit("/", 1)[1])
    cl = headers.get("Content-Length") or headers.get("content-length")
    if status == 200 and cl and cl.isdigit():
        return int(cl)
    return None


def decode_text(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def domains_for(ccn: str, name: str, sites: dict) -> tuple[list[str], str, dict]:
    """Return (domains, system key, override dict)."""
    ov = sites.get("overrides", {}).get(ccn, {})
    system = ""
    for key, s in sites.get("systems", {}).items():
        if re.search(s["pattern"], name, re.IGNORECASE):
            system = key
            break
    if ov.get("domains"):
        return ov["domains"], system or "independent", ov
    if system:
        return sites["systems"][system]["domains"], system, ov
    return [], "", ov


@dataclass
class Row:
    hpt_status: str = "no_website_known"
    hpt_domain: str = ""
    hpt_access: str = ""
    hpt_locations: int | None = None
    location_name: str = ""
    match_score: float | None = None
    hpt_missing_fields: str = ""
    source_page_url: str = ""
    mrf_url: str = ""
    mrf_url_source: str = ""
    mrf_http_status: int | None = None
    mrf_access: str = ""
    mrf_content_type: str = ""
    mrf_size_bytes: int | None = None
    mrf_http_last_modified: str = ""
    file_format: str = ""
    cms_version: str = ""
    last_updated_on: str = ""
    file_hospital_name: str = ""
    notes: str = ""


def discover(hospitals: pd.DataFrame, fetcher: Fetcher, sites: dict) -> pd.DataFrame:
    hpt_dir = PHASE0_DIR / "hpt_files"
    hpt_dir.mkdir(parents=True, exist_ok=True)
    hpt_cache: dict[str, tuple[dict, list[HptEntry]]] = {}
    mrf_cache: dict[str, dict] = {}
    out = []

    for h in hospitals.itertuples():
        row = Row()
        domains, system, override = domains_for(h.ccn, h.name, sites)
        pinned = override.get("location")
        print(f"[{h.ccn}] {h.name} ({system or '?'})", flush=True)
        if not h.in_scope:
            out.append({"system": system, **asdict(Row(hpt_status="out_of_scope"))})
            continue

        entries: list[HptEntry] = []
        for domain in domains:
            if domain not in hpt_cache:
                res = fetcher.get_head(f"https://{domain}/cms-hpt.txt", nbytes=2_000_000)
                text = decode_text(res["body"])
                parsed = parse_hpt(text) if res["status"] in (200, 206) and looks_like_hpt(text) else []
                if parsed:
                    (hpt_dir / f"{domain}.txt").write_text(text, encoding="utf-8")
                hpt_cache[domain] = (res, parsed)
            res, parsed = hpt_cache[domain]
            row.hpt_domain, row.hpt_access = domain, res["access"]
            if parsed:
                entries, row.hpt_locations = parsed, len(parsed)
                break
            row.hpt_status = "blocked" if res["access"] == "blocked" else "not_found"
            row.notes = res["error"] or f"HTTP {res['status']}"

        if entries:
            entry, score = match_location(h.name, entries, pinned=pinned)
            row.match_score = round(score, 1)
            if entry is None:
                row.hpt_status = "location_not_listed"
                row.notes = f"{len(entries)} locations in cms-hpt.txt, none match"
            else:
                row.hpt_status = "found"
                row.location_name = entry.location_name
                row.source_page_url = entry.source_page_url
                row.mrf_url = entry.mrf_url
                row.mrf_url_source = "cms-hpt.txt"
                row.hpt_missing_fields = ",".join(entry.missing_keys)

        if not row.mrf_url and override.get("mrf_url"):
            # cms-hpt.txt unreachable for an automated client; use the address recorded by hand
            row.mrf_url = override["mrf_url"]
            row.mrf_url_source = "manual"

        if row.mrf_url:
            if row.mrf_url not in mrf_cache:
                mrf_cache[row.mrf_url] = fetcher.get_head(row.mrf_url)
            res = mrf_cache[row.mrf_url]
            hdrs = {k.lower(): v for k, v in res["headers"].items()}
            row.mrf_http_status = res["status"]
            row.mrf_access = res["access"]
            row.mrf_content_type = hdrs.get("content-type", "")
            row.mrf_size_bytes = total_size(res["headers"], res["status"])
            row.mrf_http_last_modified = hdrs.get("last-modified", "")
            if res["status"] in (200, 206) and res["access"] != "blocked":
                s = sniff(res["body"], row.mrf_content_type)
                row.file_format, row.cms_version = s.format, s.version
                row.last_updated_on, row.file_hospital_name = s.last_updated_on, s.hospital_name
                if s.note:
                    row.notes = s.note
                if s.format == HTML:
                    row.mrf_access = "needs_browser"
            else:
                row.notes = (row.notes + "; " if row.notes else "") + (
                    res["error"] or f"price file HTTP {res['status']}"
                )
        out.append({"system": system, **asdict(row)})

    result = pd.concat([hospitals.reset_index(drop=True), pd.DataFrame(out)], axis=1)
    return result


def summarise(df: pd.DataFrame) -> dict:
    s = df[df["in_scope"]]
    cms_formats = {"cms_csv_tall", "cms_csv_wide", "cms_json"}
    usable = s["file_format"].isin(cms_formats) & (s["mrf_access"] == "plain")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metro_hospitals_total": int(len(df)),
        "in_scope": int(len(s)),
        "out_of_scope": int((~df["in_scope"]).sum()),
        "hpt_status": s["hpt_status"].value_counts().to_dict(),
        "file_format": s["file_format"].replace("", "none").value_counts().to_dict(),
        "mrf_access": s["mrf_access"].replace("", "none").value_counts().to_dict(),
        "usable_cms_format_files": int(usable.sum()),
        "unique_price_files": int(s.loc[s["mrf_url"] != "", "mrf_url"].nunique()),
        "known_size_total_gb": round(
            s.drop_duplicates("mrf_url")["mrf_size_bytes"].dropna().sum() / 1e9, 2
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh-cms", action="store_true", help="re-download the CMS hospital list")
    args = ap.parse_args()

    cfg = metro_config()
    hospitals = load_metro_hospitals(force_download=args.refresh_cms)
    PHASE0_DIR.mkdir(parents=True, exist_ok=True)
    df = discover(hospitals, Fetcher(cfg["http"]), sites_config())
    df.to_csv(PHASE0_DIR / "discovery.csv", index=False)
    summary = summarise(df)
    (PHASE0_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
