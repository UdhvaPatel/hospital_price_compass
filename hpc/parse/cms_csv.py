"""CMS CSV template parser (v2.x / v3.x), tall and wide layouts, streamed row by row.

Uses Python's csv module rather than a strict parser: real files contain quoting that strict
readers reject (seen in Banner and HonorHealth files).
"""
from __future__ import annotations

import csv
import io
import re
import sys
from dataclasses import dataclass, field
from typing import IO, Iterator

from hpc.parse.schema import (
    CodeMatcher, Match, iso_date, other_codes, rate_kind, text, to_float,
)

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

_CODE_COL = re.compile(r"^code\|(\d+)$")


@dataclass
class ParseStats:
    items_scanned: int = 0
    items_matched: int = 0
    rows_out: int = 0
    bad_rows: int = 0
    description_candidates: int = 0
    warnings: list[str] = field(default_factory=list)


def _text_stream(stream: IO[bytes]) -> io.TextIOWrapper:
    return io.TextIOWrapper(stream, encoding="utf-8-sig", errors="replace", newline="")


@dataclass
class _WideGroup:
    payer: str
    plan: str
    cols: dict[str, int] = field(default_factory=dict)  # field -> column index


def _wide_groups(header: list[str]) -> list[_WideGroup]:
    """Find payer/plan column groups such as standard_charge|Aetna|PPO|negotiated_dollar."""
    groups: dict[tuple[str, str], _WideGroup] = {}
    for i, h in enumerate(header):
        parts = h.split("|")
        key = None
        if parts[0].lower() == "standard_charge" and len(parts) >= 4:
            # standard_charge|<payer>|<plan>|<field>; extra '|' (not allowed, but seen) stays in the payer
            key, name = ("|".join(parts[1:-2]), parts[-2]), parts[-1].lower()
        elif parts[0].lower() in ("median_amount", "10th_percentile", "90th_percentile", "count",
                                  "estimated_amount", "additional_payer_notes") and len(parts) >= 3:
            key, name = ("|".join(parts[1:-1]), parts[-1]), parts[0].lower()
        if key:
            g = groups.setdefault(key, _WideGroup(*key))
            g.cols[name] = i
    return list(groups.values())


def parse_cms_csv(stream: IO[bytes], matcher: CodeMatcher, ccn: str, stats: ParseStats | None = None,
                  ) -> Iterator[dict]:
    stats = stats if stats is not None else ParseStats()
    reader = csv.reader(_text_stream(stream))
    meta_header = [h.strip().lower() for h in next(reader)]
    meta_values = next(reader)
    meta = dict(zip(meta_header, (text(v) for v in meta_values)))
    header = [h.strip() for h in next(reader)]
    low = [h.lower() for h in header]
    idx = {h: i for i, h in enumerate(low)}

    code_cols = sorted(
        (int(m.group(1)), i, idx.get(f"code|{m.group(1)}|type"))
        for i, h in enumerate(low) if (m := _CODE_COL.match(h))
    )
    code_cols = [(i, t) for _, i, t in code_cols]
    if not code_cols:
        raise ValueError("no code|N columns in data header")

    tall = "payer_name" in idx
    groups = [] if tall else _wide_groups(header)
    fmt = "cms_csv_tall" if tall else "cms_csv_wide"

    def col(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else ""

    base_meta = {
        "ccn": ccn,
        "file_hospital_name": meta.get("hospital_name", ""),
        "file_date": iso_date(meta.get("last_updated_on", "")),
        "cms_version": meta.get("version", ""),
        "source_format": fmt,
    }
    width = len(header)
    desc_i = idx.get("description")
    candidate = Match("", "", "DESCRIPTION", False)
    std_types = matcher.standard_type_strings   # raw type strings that count as a standard code
    type_idx = [t for _, t in code_cols if t is not None]
    for rownum, row in enumerate(reader, start=4):
        stats.items_scanned += 1
        raw_codes = [row[i] for i, _ in code_cols if i < len(row)]
        method = "code"
        matches, pairs = [], None
        if matcher.could_match(raw_codes):
            if len(row) < min(width, 3):
                stats.bad_rows += 1
                continue
            pairs = [(row[i], row[t] if t is not None and t < len(row) else "") for i, t in code_cols if i < len(row)]
            matches = matcher.match(pairs)
        if not matches:
            # No tracked code. Only rows with no standard code at all can be description-only
            # candidates; checking the type columns first keeps this cheap (most rows have one).
            if matcher.keyword_re is None or any(
                    t < len(row) and row[t].strip().upper() in std_types for t in type_idx):
                continue
            desc = row[desc_i] if desc_i is not None and desc_i < len(row) else ""
            if pairs is None:
                pairs = [(row[i], row[t] if t is not None and t < len(row) else "") for i, t in code_cols if i < len(row)]
            if not matcher.is_description_candidate(desc, pairs):
                continue
            matches, method = [candidate], "description_candidate"
            stats.description_candidates += 1
        else:
            stats.items_matched += 1

        common = {
            **base_meta,
            "source_item": rownum,
            "description": text(col(row, "description")),
            "setting": text(col(row, "setting")).lower(),
            "billing_class": text(col(row, "billing_class")).lower(),
            "modifiers": text(col(row, "modifiers")),
            "gross_charge": to_float(col(row, "standard_charge|gross")),
            "cash_price": to_float(col(row, "standard_charge|discounted_cash")),
            "min_charge": to_float(col(row, "standard_charge|min")),
            "max_charge": to_float(col(row, "standard_charge|max")),
        }
        generic_notes = text(col(row, "additional_generic_notes"))

        if tall:
            amount = to_float(col(row, "standard_charge|negotiated_dollar"))
            pct = to_float(col(row, "standard_charge|negotiated_percentage"))
            algo = text(col(row, "standard_charge|negotiated_algorithm"))
            payer_rows = [{
                "payer": text(col(row, "payer_name")),
                "plan": text(col(row, "plan_name")),
                "amount": amount, "negotiated_percentage": pct, "negotiated_algorithm": algo,
                "rate_kind": rate_kind(amount, pct, algo),
                "methodology": text(col(row, "standard_charge|methodology")).lower(),
                "median_amount": to_float(col(row, "median_amount")) if "median_amount" in idx
                else to_float(col(row, "estimated_amount")),
                "p10_amount": to_float(col(row, "10th_percentile")),
                "p90_amount": to_float(col(row, "90th_percentile")),
                "count": text(col(row, "count")),
                "notes": generic_notes,
            }]
        else:
            payer_rows = []
            for g in groups:
                def gv(name):
                    i = g.cols.get(name)
                    return row[i] if i is not None and i < len(row) else ""
                amount = to_float(gv("negotiated_dollar"))
                pct = to_float(gv("negotiated_percentage"))
                algo = text(gv("negotiated_algorithm"))
                if amount is None and pct is None and not algo:
                    continue
                notes = "; ".join(x for x in (text(gv("additional_payer_notes")), generic_notes) if x)
                payer_rows.append({
                    "payer": g.payer, "plan": g.plan,
                    "amount": amount, "negotiated_percentage": pct, "negotiated_algorithm": algo,
                    "rate_kind": rate_kind(amount, pct, algo),
                    "methodology": text(gv("methodology")).lower(),
                    "median_amount": to_float(gv("median_amount")) if "median_amount" in g.cols
                    else to_float(gv("estimated_amount")),
                    "p10_amount": to_float(gv("10th_percentile")),
                    "p90_amount": to_float(gv("90th_percentile")),
                    "count": text(gv("count")),
                    "notes": notes,
                })
            if not payer_rows:  # keep gross / cash prices even when no payer has a rate
                payer_rows = [{"payer": "", "plan": "", "amount": None, "negotiated_percentage": None,
                               "negotiated_algorithm": "", "rate_kind": "none", "methodology": "",
                               "median_amount": None, "p10_amount": None, "p90_amount": None,
                               "count": "", "notes": generic_notes}]

        for m in matches:
            proc = {
                "procedure_id": m.procedure_id, "matched_code": m.code,
                "matched_code_type": m.code_type, "is_primary_code": m.is_primary,
                "match_method": method, "other_codes": other_codes(pairs, m),
            }
            for pr in payer_rows:
                stats.rows_out += 1
                yield {**common, **proc, **pr}
