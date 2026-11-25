"""Parse cms-hpt.txt files and match their locations to CMS hospitals.

cms-hpt.txt (required since 1 Jan 2025) is a plain-text file at the root of a hospital's
website. One block per location, blocks separated by blank lines:

    location-name: Example Hospital
    source-page-url: https://example.org/price-transparency
    mrf-url: https://example.org/123456789_example-hospital_standardcharges.csv
    contact-name: Jane Doe
    contact-email: jane@example.org
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

REQUIRED_KEYS = ("location-name", "source-page-url", "mrf-url", "contact-name", "contact-email")


@dataclass
class HptEntry:
    location_name: str = ""
    source_page_url: str = ""
    mrf_url: str = ""
    contact_name: str = ""
    contact_email: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def missing_keys(self) -> list[str]:
        return [k for k in REQUIRED_KEYS if not getattr(self, k.replace("-", "_"))]


def looks_like_hpt(text: str) -> bool:
    return bool(re.search(r"^\s*location-name\s*:", text, re.IGNORECASE | re.MULTILINE))


def parse_hpt(text: str) -> list[HptEntry]:
    """Parse cms-hpt.txt. Tolerates CRLF, missing spaces after ':', BOMs and blank lines
    between keys of the same block (a new block starts at each location-name)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    entries: list[HptEntry] = []
    current: HptEntry | None = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "location-name":
            current = HptEntry(location_name=value)
            entries.append(current)
            continue
        if current is None:
            continue
        attr = key.replace("-", "_")
        if key in REQUIRED_KEYS:
            setattr(current, attr, value)
        else:
            current.extra[key] = value
    return entries


# ---------------------------------------------------------------- name matching

_GENERIC = {
    "THE", "AND", "OF", "HOSPITAL", "MEDICAL", "MED", "CENTER", "CTR", "CAMPUS",
    "LLC", "INC", "HEALTH", "HEALTHCARE", "SYSTEM",
}


def normalise_name(name: str) -> str:
    s = name.upper().replace("’", "'")
    s = s.replace("HONOR HEALTH", "HONORHEALTH")
    s = re.sub(r"'S\b", "S", s)
    s = re.sub(r"\bST\.?\s", "ST ", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t not in _GENERIC]
    return " ".join(tokens)


def match_location(
    cms_name: str, entries: list[HptEntry], pinned: str | None = None, threshold: float = 85.0
) -> tuple[HptEntry | None, float]:
    """Find the cms-hpt.txt entry for a CMS hospital name. Returns (entry, score 0-100)."""
    if not entries:
        return None, 0.0
    if pinned:
        for e in entries:
            if normalise_name(e.location_name) == normalise_name(pinned):
                return e, 100.0
        return None, 0.0
    target = normalise_name(cms_name)
    scored = [(fuzz.token_sort_ratio(target, normalise_name(e.location_name)), e) for e in entries]
    score, best = max(scored, key=lambda x: x[0])
    return (best, float(score)) if score >= threshold else (None, float(score))
