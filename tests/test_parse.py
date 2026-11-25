"""Phase 2 parser tests: one small sample per format with known correct output."""
import io
import json
import zipfile

import pytest

from hpc.parse import legacy
from hpc.parse.cms_csv import ParseStats, parse_cms_csv
from hpc.parse.cms_json import parse_cms_json
from hpc.parse.schema import CodeMatcher, iso_date, to_float

PROCS = {
    "families": {"cpt": ["CPT", "HCPCS"], "drg": ["MS-DRG", "TRIS-DRG"]},
    "procedure": [
        {"id": "mri_knee", "family": "cpt", "code": "73721", "related": ["73722"]},
        {"id": "joint_drg", "family": "drg", "code": "470"},
        {"id": "cmp", "family": "cpt", "code": "80053"},
    ],
}


@pytest.fixture
def matcher():
    return CodeMatcher(PROCS)


def b(s: str) -> io.BytesIO:
    return io.BytesIO(s.encode("utf-8"))


# ------------------------------------------------------------------ helpers

def test_value_cleaning():
    assert to_float("$1,234.50") == 1234.5
    assert to_float("53%") == 53.0
    assert to_float("") is None and to_float("N/A") is None and to_float(None) is None
    assert iso_date("11/4/2026") == "2026-11-04"
    assert iso_date("2026-09-30") == "2026-09-30"
    assert iso_date("08/14/2026") == "2026-08-14"
    assert iso_date("garbage") == ""


def test_matcher_rules(matcher):
    assert [m.procedure_id for m in matcher.match([("0470", "MS-DRG")])] == ["joint_drg"]
    assert matcher.match([("470", "APR-DRG")]) == []          # different DRG system
    assert matcher.match([("73721", "CDM")]) == []            # chargemaster code, not CPT
    m = matcher.match([("73722", "CPT")])[0]
    assert m.procedure_id == "mri_knee" and not m.is_primary  # related code
    m = matcher.match([("73722", "CPT"), ("73721", "HCPCS")])[0]
    assert m.is_primary                                       # primary wins over related
    fixed = CodeMatcher(PROCS, type_aliases={"CDM": "CPT"})
    assert fixed.match([("73721", "CDM")])[0].code_type == "CDM→CPT"


# ------------------------------------------------------------------ CSV tall

TALL = '''hospital_name,last_updated_on,version,location_name
Test Hospital,11/4/2026,3.0.0,Main
description,code|1,code|1|type,code|2,code|2|type,setting,billing_class,modifiers,standard_charge|gross,standard_charge|discounted_cash,payer_name,plan_name,standard_charge|negotiated_dollar,standard_charge|negotiated_percentage,standard_charge|negotiated_algorithm,median_amount,10th_percentile,90th_percentile,count,standard_charge|methodology,standard_charge|min,standard_charge|max,additional_generic_notes
"MRI KNEE W/O, ""LEFT""",4501,CDM,73721,HCPCS,outpatient,facility,,"$2,500.00",1000,Aetna,PPO,900,,,850,700,1000,11 through 50,Fee Schedule,600,1200,
"MRI KNEE W/O, ""LEFT""",4501,CDM,73721,HCPCS,outpatient,facility,,2500,1000,Cigna,Open Access,,60,,,,,,Percent of Total Billed Charges,600,1200,note
JOINT REPLACEMENT,0470,MS-DRG,,,inpatient,facility,,80000,,UHC,Commercial,40000,,,,,,,Case Rate,30000,50000,
APR DRG 470,470,APR-DRG,,,inpatient,facility,,9999,,AHCCCS,Medicaid,5000,,,,,,,Case Rate,,,
ASPIRIN,999,CDM,,,outpatient,facility,,1,,,,,,,,,,,,,,
'''


def test_cms_csv_tall(matcher):
    stats = ParseStats()
    rows = list(parse_cms_csv(b(TALL), matcher, "030000", stats))
    assert stats.items_scanned == 5 and stats.items_matched == 3 and len(rows) == 3
    aetna, cigna, drg = rows
    assert aetna["description"] == 'MRI KNEE W/O, "LEFT"'
    assert (aetna["procedure_id"], aetna["matched_code"], aetna["matched_code_type"]) == ("mri_knee", "73721", "HCPCS")
    assert aetna["other_codes"] == "CDM:4501"
    assert (aetna["amount"], aetna["gross_charge"], aetna["cash_price"]) == (900.0, 2500.0, 1000.0)
    assert (aetna["median_amount"], aetna["p10_amount"], aetna["count"]) == (850.0, 700.0, "11 through 50")
    assert aetna["rate_kind"] == "dollar" and aetna["methodology"] == "fee schedule"
    assert aetna["file_date"] == "2026-11-04" and aetna["cms_version"] == "3.0.0"
    assert aetna["source_format"] == "cms_csv_tall" and aetna["source_item"] == 4
    assert cigna["rate_kind"] == "percentage" and cigna["negotiated_percentage"] == 60.0 and cigna["amount"] is None
    assert drg["procedure_id"] == "joint_drg" and drg["matched_code"] == "470" and drg["setting"] == "inpatient"


# ------------------------------------------------------------------ CSV wide

WIDE = '''hospital_name,last_updated_on,version
Wide Hospital,2026-10-29,3.0.0
description,code|1,code|1|type,setting,standard_charge|gross,standard_charge|discounted_cash,standard_charge|Aetna|PPO|negotiated_dollar,standard_charge|Aetna|PPO|negotiated_percentage,standard_charge|Aetna|PPO|methodology,median_amount|Aetna|PPO,additional_payer_notes|Aetna|PPO,standard_charge|BCBS AZ|HMO|negotiated_dollar,standard_charge|BCBS AZ|HMO|negotiated_algorithm,standard_charge|BCBS AZ|HMO|methodology
CMP,80053,CPT,outpatient,300,120,95,,fee schedule,90,lab rate,,,
CMP,80053,CPT,both,300,120,,,,,,,see contract section 4,other
CMP CASH ONLY,80053,CPT,outpatient,310,125,,,,,,,,
'''


def test_cms_csv_wide(matcher):
    rows = list(parse_cms_csv(b(WIDE), matcher, "031300"))
    assert len(rows) == 3
    a, algo, cash = rows
    assert (a["payer"], a["plan"], a["amount"], a["median_amount"], a["notes"]) == ("Aetna", "PPO", 95.0, 90.0, "lab rate")
    assert (algo["payer"], algo["rate_kind"], algo["negotiated_algorithm"]) == ("BCBS AZ", "algorithm", "see contract section 4")
    assert (cash["payer"], cash["cash_price"], cash["rate_kind"]) == ("", 125.0, "none")  # gross/cash kept
    assert a["source_format"] == "cms_csv_wide"


def test_wide_header_truncated_payer_with_pipe(matcher):
    head = WIDE.replace("BCBS AZ|HMO", "BCBS|AZ|HMO")  # illegal '|' in payer name is tolerated
    rows = list(parse_cms_csv(b(head), matcher, "x"))
    assert any(r["payer"] == "BCBS|AZ" for r in rows)


# ------------------------------------------------------------------ JSON

JSON_FILE = {
    "hospital_name": "AGH MESA LLC", "last_updated_on": "2026-09-30", "version": "3.0.0",
    "standard_charge_information": [
        {"description": "MAJOR JOINT", "code_information": [{"code": "470", "type": "MS-DRG"}],
         "standard_charges": [{"minimum": "3322.79", "maximum": 52172.09, "setting": "inpatient",
                               "payers_information": [
                                   {"payer_name": "Aetna", "plan_name": "Commercial|PPO",
                                    "standard_charge_dollar": 39389, "methodology": "case rate"},
                                   {"payer_name": "Coventry", "plan_name": "All Plans",
                                    "standard_charge_percentage": "53", "count": "0",
                                    "methodology": "percent of total billed charges",
                                    "additional_payer_notes": "No services in lookback"}]}]},
        {"description": "SUPPLY", "code_information": [{"code": "123", "type": "CDM"}],
         "standard_charges": [{"gross_charge": 5, "setting": "both"}]},
        {"description": "MRI", "code_information": [{"code": "73721", "type": "CPT"}, {"code": "610", "type": "RC"}],
         "standard_charges": [{"gross_charge": 2000, "discounted_cash": "900.50", "setting": "outpatient",
                               "billing_class": "facility"}]},
    ],
}


def test_cms_json(matcher):
    raw = json.dumps(JSON_FILE).encode()
    stats = ParseStats()
    rows = list(parse_cms_json(lambda: io.BytesIO(raw), matcher, "030139", stats))
    assert stats.items_scanned == 3 and stats.items_matched == 2 and len(rows) == 3
    aetna, cov, mri = rows
    assert (aetna["amount"], aetna["plan"], aetna["min_charge"], aetna["max_charge"]) == (39389.0, "Commercial|PPO", 3322.79, 52172.09)
    assert cov["rate_kind"] == "percentage" and cov["negotiated_percentage"] == 53.0 and cov["notes"] == "No services in lookback"
    assert (mri["payer"], mri["cash_price"], mri["other_codes"]) == ("", 900.5, "RC:610")
    assert aetna["file_hospital_name"] == "AGH MESA LLC" and aetna["file_date"] == "2026-09-30"


def test_json_metadata_after_array(matcher):
    data = {"standard_charge_information": JSON_FILE["standard_charge_information"],
            "hospital_name": "LATE", "last_updated_on": "2026-01-01", "version": "2.2.0"}
    raw = json.dumps(data).encode()
    rows = list(parse_cms_json(lambda: io.BytesIO(raw), matcher, "x"))
    assert rows[0]["file_hospital_name"] == "LATE" and rows[0]["cms_version"] == "2.2.0"


# ------------------------------------------------------------------ zipped file end-to-end

def test_parse_file_reads_inside_zip(matcher, tmp_path):
    from hpc.parse.run import parse_file
    z = tmp_path / "h.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("123_h_standardcharges.csv", TALL)
    rows, fmt = parse_file(z, "033302", matcher, ParseStats())
    assert fmt == "cms_csv_tall" and len(rows) == 3


# ------------------------------------------------------------------ legacy + LLM mapping

LEGACY = '''Hospital Charge Master,,,
Effective 2024-01-01,,,
Charge Code,CPT Code,Service Description,Gross Charge,Self Pay Price
1001,73721,MRI LOWER EXTREMITY JOINT,2400.00,960.00
1002,99999,SOMETHING ELSE,10.00,5.00
1003,80053,COMP METABOLIC PANEL,250,100
'''

GOOD = {"code": "CPT Code", "code_type": None, "description": "Service Description", "setting": None,
        "payer": None, "plan": None, "amount": None, "gross_charge": "Gross Charge",
        "cash_price": "Self Pay Price", "min_charge": None, "max_charge": None, "default_code_type": "CPT"}


class FakeNim:
    chat_model = "fake-model"

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def chat_json(self, system, user, max_tokens=800):
        self.calls += 1
        return self.reply


@pytest.fixture
def mapping_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, "MAPPING_DIR", tmp_path / "maps")
    return tmp_path / "maps"


def test_legacy_llm_mapping_accepted_saved_and_reused(matcher, mapping_dir):
    nim = FakeNim(GOOD)
    rows = list(legacy.parse_legacy_csv(b(LEGACY), matcher, "039999", nim))
    assert [r["procedure_id"] for r in rows] == ["mri_knee", "cmp"]
    assert rows[0]["gross_charge"] == 2400.0 and rows[0]["cash_price"] == 960.0
    assert rows[0]["source_format"] == "legacy"
    saved = json.loads((mapping_dir / "039999.json").read_text())
    assert saved["source"] == "llm:fake-model"
    rows2 = list(legacy.parse_legacy_csv(b(LEGACY), matcher, "039999", nim))
    assert nim.calls == 1 and len(rows2) == 2  # second run uses the saved mapping


def test_legacy_bad_llm_mapping_rejected(matcher, mapping_dir):
    bad = {**GOOD, "code": "Service Description", "description": "CPT Code"}  # swapped columns
    with pytest.raises(legacy.MappingRejected, match="don't look like"):
        list(legacy.parse_legacy_csv(b(LEGACY), matcher, "039998", FakeNim(bad)))
    assert not (mapping_dir / "039998.json").exists()
    with pytest.raises(legacy.MappingRejected, match="unknown column"):
        list(legacy.parse_legacy_csv(b(LEGACY), matcher, "039997", FakeNim({**GOOD, "amount": "Price $"})))


def test_legacy_without_nim_needs_saved_mapping(matcher, mapping_dir):
    with pytest.raises(legacy.MappingRejected, match="NIM client unavailable"):
        list(legacy.parse_legacy_csv(b(LEGACY), matcher, "039996", None))


def test_saved_mapping_ignored_when_header_changes(matcher, mapping_dir):
    list(legacy.parse_legacy_csv(b(LEGACY), matcher, "039995", FakeNim(GOOD)))
    changed = LEGACY.replace("Self Pay Price", "Cash Price")
    with pytest.raises(legacy.MappingRejected):
        list(legacy.parse_legacy_csv(b(changed), matcher, "039995", None))


# ------------------------------------------------------------------ streaming parse (no disk)

def test_stream_and_parse_matches_disk_parse(matcher, tmp_path):
    from hpc.download import Downloader
    from hpc.parse.run import stream_and_parse
    from tests.test_download import Server

    s = Server()
    try:
        body = (TALL.split("\n", 3)[3] * 2000)  # many rows
        csv_text = "\n".join(TALL.split("\n")[:3]) + "\n" + body
        s.files["/huge.csv"] = {"body": csv_text.encode(), "etag": '"v1"'}
        s.drop_after["/huge.csv"] = [50_000]  # connection drops once mid-stream
        dl = Downloader("t", raw_dir=tmp_path / "raw", state_file=tmp_path / "s.json",
                        log_file=tmp_path / "l.jsonl", delay_seconds=0, sleep=lambda x: None,
                        chunk_size=8192)
        rows, info = stream_and_parse(dl, s.url("/huge.csv"), "030103", matcher, ParseStats(), force=True)
        disk = list(parse_cms_csv(io.BytesIO(csv_text.encode()), matcher, "030103"))
        assert len(rows) == len(disk) == 6000 and rows == disk
        assert len(info["sha256"]) == 64 and not (tmp_path / "raw").exists()
    finally:
        s.httpd.shutdown()
