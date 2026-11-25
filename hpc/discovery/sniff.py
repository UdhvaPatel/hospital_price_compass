"""Identify a price file's format from its first bytes (no full download needed).

CMS CSV template (v2.x / v3.x):
    row 1: hospital_name, last_updated_on, version, location_name, ...   (metadata headers)
    row 2: metadata values
    row 3: data headers: description, code|1, code|1|type, ..., standard_charge|gross, ...
      tall: has payer_name / plan_name columns
      wide: has standard_charge|<payer>|<plan>|negotiated_dollar columns
CMS JSON template: object with hospital_name, last_updated_on, version, standard_charge_information.
"""
from __future__ import annotations

import csv
import io
import re
import struct
import zlib
from dataclasses import dataclass

CMS_CSV_TALL = "cms_csv_tall"
CMS_CSV_WIDE = "cms_csv_wide"
CMS_JSON = "cms_json"
LEGACY_CSV = "legacy_csv"
LEGACY_JSON = "legacy_json"
ZIP = "zip"
GZIP = "gzip"
EXCEL = "excel"
HTML = "html"
UNKNOWN = "unknown"


@dataclass
class Sniff:
    format: str
    version: str = ""
    last_updated_on: str = ""
    hospital_name: str = ""
    note: str = ""


def _decode(head: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = head.decode(enc)
            if enc == "utf-16" and not head[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return head.decode("latin-1")


def _json_field(text: str, key: str) -> str:
    m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else ""


def _zip_first_member(head: bytes) -> tuple[str, bytes]:
    """Name and (partially) decompressed start of the first member of a zip archive."""
    if len(head) < 30:
        return "", b""
    method, = struct.unpack("<H", head[8:10])
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    name = head[30:30 + name_len].decode("utf-8", errors="replace")
    data = head[30 + name_len + extra_len:]
    if method == 0:
        return name, data
    if method == 8:
        try:
            return name, zlib.decompressobj(-15).decompress(data)
        except zlib.error:
            return name, b""
    return name, b""


def sniff(head: bytes, content_type: str = "") -> Sniff:
    if not head:
        return Sniff(UNKNOWN, note="empty response")
    if head[:4] == b"PK\x03\x04":
        name, inner = _zip_first_member(head)
        if name.startswith("xl/") or name == "[Content_Types].xml":
            return Sniff(EXCEL)
        if inner:
            s = sniff(inner)
            s.note = f"zipped: {name}" + (f"; {s.note}" if s.note else "")
            return s
        return Sniff(ZIP, note=f"zip member: {name}")
    if head[:2] == b"\x1f\x8b":
        try:
            s = sniff(zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(head))
            s.note = "gzipped" + (f"; {s.note}" if s.note else "")
            return s
        except zlib.error:
            return Sniff(GZIP)
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return Sniff(EXCEL, note="legacy .xls")

    text = _decode(head)
    stripped = text.lstrip()
    low = stripped[:500].lower()
    if low.startswith("<!doctype html") or low.startswith("<html") or "<head" in low[:200]:
        return Sniff(HTML, note="got a web page, not a data file")

    if stripped.startswith("{") or stripped.startswith("["):
        if '"standard_charge_information"' in text or ('"hospital_name"' in text and '"version"' in text):
            return Sniff(
                CMS_JSON,
                version=_json_field(text, "version"),
                last_updated_on=_json_field(text, "last_updated_on"),
                hospital_name=_json_field(text, "hospital_name"),
            )
        return Sniff(LEGACY_JSON)

    # CSV. The last row may be cut off by the byte limit; for wide files the data header
    # row alone can exceed it, so a truncated row is still read (its leading cells are intact).
    try:
        rows = list(csv.reader(io.StringIO(text)))[:5]
    except csv.Error:
        return Sniff(UNKNOWN, note="unreadable CSV")
    if not rows:
        return Sniff(UNKNOWN)
    first = [c.strip().lower() for c in rows[0]]
    if "hospital_name" in first and "last_updated_on" in first and len(rows) >= 3:
        meta = dict(zip(first, rows[1])) if len(rows) > 1 else {}
        headers = [c.strip().lower() for c in rows[2]]
        info = dict(
            version=meta.get("version", "").strip(),
            last_updated_on=meta.get("last_updated_on", "").strip(),
            hospital_name=meta.get("hospital_name", "").strip(),
        )
        if "payer_name" in headers:
            return Sniff(CMS_CSV_TALL, **info)
        if any(h.startswith("standard_charge|") and h.count("|") >= 3 for h in headers):
            return Sniff(CMS_CSV_WIDE, **info)
        return Sniff(LEGACY_CSV, note="CMS metadata rows but unrecognised data headers", **info)
    if len(first) >= 3:
        return Sniff(LEGACY_CSV, note="header: " + ", ".join(first[:8])[:200])
    return Sniff(UNKNOWN, note=f"content-type {content_type}")
