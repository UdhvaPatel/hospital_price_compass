"""The standard price table, procedure code matching and value cleaning shared by all parsers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from hpc.config import load_toml

# One row = one price for one item at one hospital for one payer/plan (or no payer: gross/cash only).
COLUMNS = {
    "ccn": "string",                 # hospital CMS Certification Number
    "file_hospital_name": "string",  # hospital_name as written in the file
    "file_date": "string",           # last_updated_on, ISO yyyy-mm-dd
    "cms_version": "string",
    "source_format": "string",       # cms_csv_tall / cms_csv_wide / cms_json / legacy
    "source_item": "int64",          # row number (CSV) or item index (JSON) in the file
    "procedure_id": "string",        # from config/procedures.toml
    "matched_code": "string",
    "matched_code_type": "string",   # CPT / HCPCS / MS-DRG / TRIS-DRG
    "is_primary_code": "bool",       # False = a related code (close variant)
    "match_method": "string",        # code / description_candidate (decided in Phase 3)
    "other_codes": "string",         # all other codes on the row, e.g. "CDM:45021560;RC:450"
    "description": "string",
    "setting": "string",             # inpatient / outpatient / both
    "billing_class": "string",       # facility / professional
    "modifiers": "string",
    "payer": "string",               # empty = no payer (gross / cash only row)
    "plan": "string",
    "rate_kind": "string",           # dollar / percentage / algorithm / none
    "methodology": "string",         # case rate / fee schedule / percent of total billed charges / per diem / other
    "amount": "float64",             # negotiated dollar amount
    "negotiated_percentage": "float64",
    "negotiated_algorithm": "string",
    "gross_charge": "float64",
    "cash_price": "float64",         # discounted cash price
    "min_charge": "float64",         # de-identified minimum negotiated charge
    "max_charge": "float64",
    "median_amount": "float64",      # v3: median allowed amount (or v2 estimated_amount)
    "p10_amount": "float64",
    "p90_amount": "float64",
    "count": "string",               # v3: count of allowed amounts (may be "1 through 10")
    "notes": "string",
}

PROCEDURE_FIELDS = ("procedure_id", "matched_code", "matched_code_type", "is_primary_code")


# ------------------------------------------------------------------ value cleaning

_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def to_float(value) -> float | None:
    """'$1,234.50' -> 1234.5 ; '' / 'N/A' / None -> None ; '53%' -> 53.0"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        m = _NUM.fullmatch(s.strip())
        return float(m.group()) if m else None


def text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%m-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%Y%m%d")


def iso_date(value) -> str:
    """Normalise the many date styles in price files to yyyy-mm-dd ('' if unreadable)."""
    s = text(value)
    if not s:
        return ""
    s = s.split(" ")[0] if re.match(r"\d{4}-\d{2}-\d{2} ", s) else s
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s[:19], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def rate_kind(amount, percentage, algorithm) -> str:
    if amount is not None:
        return "dollar"
    if percentage is not None:
        return "percentage"
    if text(algorithm):
        return "algorithm"
    return "none"


# ------------------------------------------------------------------ code matching

_TYPE_ALIASES = {
    "MSDRG": "MS-DRG", "MS DRG": "MS-DRG", "MS-DRG": "MS-DRG", "MS_DRG": "MS-DRG",
    "TRIS-DRG": "TRIS-DRG", "TRISDRG": "TRIS-DRG",
    "CPT": "CPT", "CPT4": "CPT", "CPT-4": "CPT",
    "HCPCS": "HCPCS", "HCPC": "HCPCS",
}


def normalise_type(code_type: str) -> str:
    t = text(code_type).upper()
    return _TYPE_ALIASES.get(t, t)


def normalise_code(code: str, family: str) -> str:
    c = text(code).upper()
    if family == "drg":
        c = c.lstrip("0") or "0"
    return c


@dataclass(frozen=True)
class Match:
    procedure_id: str
    code: str
    code_type: str
    is_primary: bool


class CodeMatcher:
    """Matches (code, type) pairs from a price file row against the chosen procedures."""

    def __init__(self, config: dict | None = None, type_aliases: dict[str, str] | None = None):
        """type_aliases: per-hospital fixes for mislabelled code types, e.g. {"CDM": "CPT"} when a
        file labels real CPT codes as chargemaster codes. Matches are labelled "CDM→CPT"."""
        cfg = config or load_toml("procedures.toml")
        self.aliases = {normalise_type(k): normalise_type(v) for k, v in (type_aliases or {}).items()}
        self.procedures = {p["id"]: p for p in cfg["procedure"]}
        self.type_family: dict[str, str] = {}
        for fam, types in cfg["families"].items():
            for t in types:
                self.type_family[normalise_type(t)] = fam
        self.lookup: dict[tuple[str, str], tuple[str, bool]] = {}
        for p in cfg["procedure"]:
            fam = p["family"]
            self.lookup[(fam, normalise_code(p["code"], fam))] = (p["id"], True)
            for rc in p.get("related", []):
                self.lookup.setdefault((fam, normalise_code(rc, fam)), (p["id"], False))
        # raw strings that could possibly match: a cheap pre-filter before full matching
        self.raw_candidates = {code for (_, code) in self.lookup}
        self.raw_candidates |= {c.zfill(4) for (fam, c) in self.lookup if fam == "drg"}
        self.raw_candidates |= {c.zfill(3) for (fam, c) in self.lookup if fam == "drg"}
        # description-only candidates (rows with no standard code): one combined keyword regex
        kws = [p["keywords"] for p in cfg["procedure"] if p.get("keywords")]
        self.keyword_re = re.compile("|".join(f"(?:{k})" for k in kws), re.I) if kws else None
        self._candidate_memo: dict[tuple, bool] = {}
        # upper-case raw type strings that mean "this row has a standard code"
        std = set(self.type_family) | set(self.aliases) | {"APR-DRG"}
        self.standard_type_strings = std | {a for a, n in _TYPE_ALIASES.items() if n in std}

    def could_match(self, raw_codes) -> bool:
        return any(text(c).upper() in self.raw_candidates for c in raw_codes)

    def has_standard_code(self, pairs: list[tuple[str, str]]) -> bool:
        """True if any code on the row is a CPT/HCPCS/DRG-type code (even one we don't track)."""
        return any(
            text(c) and (normalise_type(t) in self.type_family or normalise_type(t) in self.aliases
                         or normalise_type(t) == "APR-DRG")
            for c, t in pairs
        )

    def is_description_candidate(self, description: str, pairs: list[tuple[str, str]]) -> bool:
        """A row with no standard code whose description mentions one of our procedures and
        doesn't look like a supply or drug. Phase 3 decides the procedure with embeddings."""
        if self.keyword_re is None or not description or self.has_standard_code(pairs):
            return False
        # tall files repeat an item once per payer: remember each (description, revenue codes)
        key = (description, tuple(text(c) for c, t in pairs if normalise_type(t) == "RC"))
        hit = self._candidate_memo.get(key)
        if hit is None:
            hit = bool(self.keyword_re.search(description)) and not looks_like_supply(description, pairs)
            self._candidate_memo[key] = hit
        return hit

    def match(self, pairs: list[tuple[str, str]]) -> list[Match]:
        """pairs = [(code, code_type), ...] from one row. Returns one Match per procedure found."""
        found: dict[str, Match] = {}
        for code, ctype in pairs:
            nt = normalise_type(ctype)
            fam = self.type_family.get(nt)
            if nt in self.aliases:
                fam = self.type_family.get(self.aliases[nt])
                nt = f"{nt}→{self.aliases[nt]}"
            if not fam:
                continue
            hit = self.lookup.get((fam, normalise_code(code, fam)))
            if not hit:
                continue
            pid, primary = hit
            prev = found.get(pid)
            if prev is None or (primary and not prev.is_primary):
                found[pid] = Match(pid, normalise_code(code, fam), nt, primary)
        return list(found.values())


_SUPPLY_WORDS = re.compile(
    r"\b(SUP|SUPPLY|SUPPLIES|DRAPE|PACK|KIT|TRAY|KNIFE|BLADE|SUTURE|VICRYL|PROLENE|IMPLANT|SCREW|"
    r"STENT|CATH|CATHETER|ELECTRODE|EMULSION|EMULSON|SYRINGE|NEEDLE|MAMMOTOME|MESH|GRAFT|IOL|LENS|"
    r"SOLN|SOLUTION|INJ|INJECTION|TAB|CAP|\d+ ?MG|\d+ ?ML|DRESSING|GLOVE|WIRE|BIT|DRILL|BURR|"
    r"WAND|PROBE|CANNULA|TROCAR|STAPLER|CLIP|DEVICE|PROSTHESIS|COMPONENT|INSERT|PATELLA|TIBIAL|"
    r"FEMORAL|ACETABULAR|STEM|LINER)\b", re.I)


def looks_like_supply(description: str, pairs: list[tuple[str, str]]) -> bool:
    """Supplies and drugs: by wording, or by revenue code 025x (pharmacy), 027x (supplies),
    0278 (implants) or 0636 (drugs requiring detailed coding)."""
    if _SUPPLY_WORDS.search(description or ""):
        return True
    for c, t in pairs:
        if normalise_type(t) == "RC":
            rc = text(c).zfill(4)
            if rc.startswith(("025", "027")) or rc == "0636":
                return True
    return False


def other_codes(pairs: list[tuple[str, str]], matched: Match) -> str:
    return ";".join(
        f"{normalise_type(t)}:{text(c)}" for c, t in pairs
        if text(c) and not (normalise_type(t) == matched.code_type and text(c).upper().lstrip("0") == matched.code.lstrip("0"))
    )
