"""Fallback for non-CMS (older / custom) CSV layouts.

1. Find the header row.
2. Get a column mapping: a saved one (config/column_mappings/<ccn>.json) if the header is
   unchanged, otherwise ask a NIM LLM to *propose* one.
3. Rules *check* the proposal against sample rows (codes look like CPT/HCPCS/DRG, prices are
   numbers). Only an accepted mapping is saved and used, so the LLM is asked once per layout.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import IO, Iterator

from hpc.config import CONFIG_DIR
from hpc.parse.cms_csv import ParseStats, _text_stream
from hpc.parse.schema import CodeMatcher, other_codes, rate_kind, text, to_float

MAPPING_DIR = CONFIG_DIR / "column_mappings"
FIELDS = ("code", "code_type", "description", "setting", "payer", "plan", "amount",
          "gross_charge", "cash_price", "min_charge", "max_charge")
REQUIRED = ("code", "description")
CODE_RE = re.compile(r"^(\d{5}|[A-Z]\d{4}|\d{4}[A-Z]|\d{3,4})$")

SYSTEM_PROMPT = (
    "You map columns of US hospital price transparency files. Reply with one JSON object only."
)
USER_PROMPT = """Columns: {header}

Sample rows:
{samples}

Return JSON with these keys; each value is an exact column name from the list, or null:
"code" (CPT/HCPCS/DRG procedure code), "code_type" (column saying CPT/HCPCS/DRG),
"description", "setting" (inpatient/outpatient), "payer" (insurer name), "plan",
"amount" (negotiated dollar rate), "gross_charge", "cash_price" (discounted cash / self-pay),
"min_charge", "max_charge".
Also "default_code_type": "CPT", "HCPCS", "MS-DRG" or null if code_type column exists."""


class MappingRejected(ValueError):
    pass


def header_fingerprint(header: list[str]) -> str:
    return hashlib.sha256("\x1f".join(h.strip().lower() for h in header).encode()).hexdigest()[:16]


def find_header(rows: list[list[str]]) -> int:
    """Index of the first row that looks like a header (several non-numeric, non-empty cells)."""
    for i, row in enumerate(rows[:30]):
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) >= 3 and sum(to_float(c) is None for c in cells) >= 0.8 * len(cells):
            return i
    raise MappingRejected("no header row found in the first 30 rows")


def validate_mapping(mapping: dict, header: list[str], samples: list[list[str]]) -> list[str]:
    """Return a list of problems (empty = accepted)."""
    problems = []
    cols = {h: i for i, h in enumerate(header)}
    for f in REQUIRED:
        if not mapping.get(f):
            problems.append(f"missing required field '{f}'")
    for f in FIELDS:
        c = mapping.get(f)
        if c and c not in cols:
            problems.append(f"'{f}' maps to unknown column '{c}'")
    if problems:
        return problems
    if not any(mapping.get(f) for f in ("amount", "gross_charge", "cash_price")):
        problems.append("no price column (amount, gross_charge or cash_price)")

    def values(f):
        i = cols[mapping[f]]
        return [r[i].strip() for r in samples if i < len(r) and r[i].strip()]

    codes = values("code")
    if not codes or sum(bool(CODE_RE.match(c.upper())) for c in codes) < 0.6 * len(codes):
        problems.append("code column values don't look like CPT/HCPCS/DRG codes")
    for f in ("amount", "gross_charge", "cash_price", "min_charge", "max_charge"):
        if mapping.get(f):
            v = values(f)
            if v and sum(to_float(x) is not None for x in v) < 0.8 * len(v):
                problems.append(f"'{f}' column is not mostly numbers")
    desc = values("description")
    if desc and sum(to_float(d) is None for d in desc) < 0.8 * len(desc):
        problems.append("description column is mostly numbers")
    if not mapping.get("code_type") and not mapping.get("default_code_type"):
        problems.append("no code_type column and no default_code_type")
    return problems


def load_mapping(ccn: str, header: list[str]) -> dict | None:
    path = MAPPING_DIR / f"{ccn}.json"
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    return saved if saved.get("header_fingerprint") == header_fingerprint(header) else None


def save_mapping(ccn: str, header: list[str], mapping: dict, source: str) -> None:
    MAPPING_DIR.mkdir(parents=True, exist_ok=True)
    saved = {**mapping, "header_fingerprint": header_fingerprint(header), "source": source,
             "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (MAPPING_DIR / f"{ccn}.json").write_text(json.dumps(saved, indent=1), encoding="utf-8")


def propose_mapping(client, header: list[str], samples: list[list[str]]) -> dict:
    sample_txt = "\n".join(json.dumps(dict(zip(header, r)))[:600] for r in samples[:5])
    return client.chat_json(SYSTEM_PROMPT, USER_PROMPT.format(header=json.dumps(header), samples=sample_txt))


def get_mapping(ccn: str, header: list[str], samples: list[list[str]], client=None) -> dict:
    saved = load_mapping(ccn, header)
    if saved:
        return saved
    if client is None:
        raise MappingRejected("non-standard layout and no saved mapping; NIM client unavailable")
    proposal = propose_mapping(client, header, samples)
    problems = validate_mapping(proposal, header, samples)
    if problems:
        raise MappingRejected("LLM mapping rejected: " + "; ".join(problems))
    save_mapping(ccn, header, proposal, source=f"llm:{getattr(client, 'chat_model', '?')}")
    return proposal


def parse_legacy_csv(stream: IO[bytes], matcher: CodeMatcher, ccn: str, client=None,
                     stats: ParseStats | None = None, file_date: str = "") -> Iterator[dict]:
    stats = stats if stats is not None else ParseStats()
    reader = csv.reader(_text_stream(stream))
    head_rows = []
    for row in reader:
        head_rows.append(row)
        if len(head_rows) >= 60:
            break
    h = find_header(head_rows)
    header = [c.strip() for c in head_rows[h]]
    buffered = head_rows[h + 1:]
    mapping = get_mapping(ccn, header, buffered[:20], client)
    cols = {name: i for i, name in enumerate(header)}
    get = {f: cols.get(mapping.get(f) or "") for f in FIELDS}
    default_type = mapping.get("default_code_type") or ""

    def v(row, f):
        i = get[f]
        return row[i] if i is not None and i < len(row) else ""

    def rows():
        yield from buffered
        yield from reader

    for n, row in enumerate(rows(), start=h + 2):
        stats.items_scanned += 1
        code = text(v(row, "code"))
        if not matcher.could_match([code]):
            continue
        pairs = [(code, text(v(row, "code_type")) or default_type)]
        for m in matcher.match(pairs):
            stats.items_matched += 1
            amount = to_float(v(row, "amount"))
            stats.rows_out += 1
            yield {
                "ccn": ccn, "file_hospital_name": "", "file_date": file_date, "cms_version": "",
                "source_format": "legacy", "source_item": n,
                "procedure_id": m.procedure_id, "matched_code": m.code, "matched_code_type": m.code_type,
                "is_primary_code": m.is_primary, "match_method": "code", "other_codes": other_codes(pairs, m),
                "description": text(v(row, "description")), "setting": text(v(row, "setting")).lower(),
                "billing_class": "", "modifiers": "",
                "payer": text(v(row, "payer")), "plan": text(v(row, "plan")),
                "rate_kind": rate_kind(amount, None, ""), "methodology": "",
                "amount": amount, "negotiated_percentage": None, "negotiated_algorithm": "",
                "gross_charge": to_float(v(row, "gross_charge")), "cash_price": to_float(v(row, "cash_price")),
                "min_charge": to_float(v(row, "min_charge")), "max_charge": to_float(v(row, "max_charge")),
                "median_amount": None, "p10_amount": None, "p90_amount": None, "count": "",
                "notes": f"mapping: {mapping.get('source', 'saved')}",
            }
