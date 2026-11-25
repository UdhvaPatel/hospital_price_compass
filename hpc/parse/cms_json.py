"""CMS JSON template parser (v2.x / v3.x). Streams items with ijson, so the file is never
loaded whole."""
from __future__ import annotations

from typing import IO, Callable, Iterator

import ijson

from hpc.parse.cms_csv import ParseStats
from hpc.parse.schema import CodeMatcher, Match, iso_date, other_codes, rate_kind, text, to_float

META_KEYS = ("hospital_name", "last_updated_on", "version", "location_name")


def read_json_meta(stream: IO[bytes]) -> dict:
    """Top-level scalar fields. Stops as soon as all are found (usually at the top of the file)."""
    meta: dict = {}
    for prefix, event, value in ijson.parse(stream):
        if prefix in META_KEYS and event in ("string", "number"):
            meta[prefix] = text(value)
            if len(meta) == len(META_KEYS):
                break
    return meta


def _modifiers(sc: dict) -> str:
    m = sc.get("modifier_code") or sc.get("modifiers") or ""
    return ",".join(text(x) for x in m) if isinstance(m, list) else text(m)


def parse_cms_json(open_stream: Callable[[], IO[bytes]], matcher: CodeMatcher, ccn: str,
                   stats: ParseStats | None = None) -> Iterator[dict]:
    """`open_stream` is called twice: once for metadata, once for the items."""
    stats = stats if stats is not None else ParseStats()
    with open_stream() as f:
        meta = read_json_meta(f)
    base = {
        "ccn": ccn,
        "file_hospital_name": meta.get("hospital_name", ""),
        "file_date": iso_date(meta.get("last_updated_on", "")),
        "cms_version": meta.get("version", ""),
        "source_format": "cms_json",
    }
    with open_stream() as f:
        for n, item in enumerate(ijson.items(f, "standard_charge_information.item", use_float=True)):
            stats.items_scanned += 1
            codes = item.get("code_information") or []
            pairs = [(text(c.get("code")), text(c.get("type"))) for c in codes if isinstance(c, dict)]
            description = text(item.get("description"))
            method = "code"
            matches = matcher.match(pairs) if matcher.could_match([c for c, _ in pairs]) else []
            if matches:
                stats.items_matched += 1
            elif matcher.is_description_candidate(description, pairs):
                matches, method = [Match("", "", "DESCRIPTION", False)], "description_candidate"
                stats.description_candidates += 1
            else:
                continue
            for sc in item.get("standard_charges") or []:
                common = {
                    **base,
                    "source_item": n,
                    "description": description,
                    "setting": text(sc.get("setting")).lower(),
                    "billing_class": text(sc.get("billing_class")).lower(),
                    "modifiers": _modifiers(sc),
                    "gross_charge": to_float(sc.get("gross_charge")),
                    "cash_price": to_float(sc.get("discounted_cash")),
                    "min_charge": to_float(sc.get("minimum")),
                    "max_charge": to_float(sc.get("maximum")),
                }
                generic_notes = text(sc.get("additional_generic_notes"))
                payer_rows = []
                for p in sc.get("payers_information") or []:
                    amount = to_float(p.get("standard_charge_dollar"))
                    pct = to_float(p.get("standard_charge_percentage"))
                    algo = text(p.get("standard_charge_algorithm"))
                    payer_rows.append({
                        "payer": text(p.get("payer_name")), "plan": text(p.get("plan_name")),
                        "amount": amount, "negotiated_percentage": pct, "negotiated_algorithm": algo,
                        "rate_kind": rate_kind(amount, pct, algo),
                        "methodology": text(p.get("methodology")).lower(),
                        "median_amount": to_float(p.get("median_amount", p.get("estimated_amount"))),
                        "p10_amount": to_float(p.get("10th_percentile")),
                        "p90_amount": to_float(p.get("90th_percentile")),
                        "count": text(p.get("count")),
                        "notes": "; ".join(x for x in (text(p.get("additional_payer_notes")), generic_notes) if x),
                    })
                if not payer_rows:
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
