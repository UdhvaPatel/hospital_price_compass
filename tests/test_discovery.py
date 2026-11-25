import gzip
import io
import zipfile

from hpc.discovery.hospitals import scope_reason
from hpc.discovery.hpt import match_location, normalise_name, parse_hpt
from hpc.discovery.run import total_size
from hpc.discovery.sniff import sniff

# ------------------------------------------------------------------ cms-hpt.txt

HPT_STANDARD = """location-name: HonorHealth Deer Valley Medical Center\r
source-page-url: https://www.honorhealth.com/patients-visitors/average-pricing\r
mrf-url: https://www.honorhealth.com/files/860181654_honorhealthdvmc_standardcharges.csv\r
contact-name: A Person\r
contact-email: a@example.org\r
\r
location-name: HonorHealth Tempe Spare\r
source-page-url: https://example.org/p\r
mrf-url: https://example.org/b.csv\r
"""

# Valleywise style: blank line between every key of one block
HPT_SPACED = """location-name: Valleywise Health Medical Center

source-page-url: https://valleywisehealth.org/patients/hospital-price-transparency/

mrf-url: https://valleywise.pt.panaceainc.com/MRFDownload/valleywise/valleywise

contact-name: Someone

contact-email: someone@valleywisehealth.org
"""

# CommonSpirit style: no space after the colon, curly apostrophe, BOM
HPT_TIGHT = "﻿location-name:St. Joseph’s Hospital and Medical Center\nmrf-url:https://x.org/a.json\n" \
            "location-name:St. Joseph’s Westgate Medical Center\nmrf-url:https://x.org/b.json\n"


def test_parse_standard_blocks_and_missing_fields():
    entries = parse_hpt(HPT_STANDARD)
    assert len(entries) == 2
    assert entries[0].mrf_url.endswith("honorhealthdvmc_standardcharges.csv")
    assert entries[0].missing_keys == []
    assert entries[1].missing_keys == ["contact-name", "contact-email"]


def test_parse_blank_lines_inside_block():
    entries = parse_hpt(HPT_SPACED)
    assert len(entries) == 1
    assert entries[0].contact_email == "someone@valleywisehealth.org"


def test_parse_tight_colons_and_bom():
    entries = parse_hpt(HPT_TIGHT)
    assert [e.mrf_url for e in entries] == ["https://x.org/a.json", "https://x.org/b.json"]


def test_url_with_colons_kept_whole():
    (e,) = parse_hpt("location-name: A\nmrf-url: https://h.org:8443/a.csv\n")
    assert e.mrf_url == "https://h.org:8443/a.csv"


# ------------------------------------------------------------------ name matching

def test_normalise_name():
    assert normalise_name("HONOR HEALTH JOHN C. LINCOLN MEDICAL CENTER") == "HONORHEALTH JOHN C LINCOLN"
    assert normalise_name("St. Joseph’s Hospital and Medical Center") == "ST JOSEPHS"


def test_match_prefers_exact_over_superset():
    entries = parse_hpt(HPT_TIGHT)
    e, score = match_location("ST JOSEPHS HOSPITAL AND MEDICAL CENTER", entries)
    assert e.mrf_url.endswith("a.json") and score == 100


def test_match_picks_right_city():
    entries = parse_hpt(
        "location-name: Banner University Medical Center Tucson\nmrf-url: t\n"
        "location-name: Banner University Medical Center Phoenix\nmrf-url: p\n"
    )
    e, _ = match_location("BANNER - UNIVERSITY MEDICAL CENTER PHOENIX", entries)
    assert e.mrf_url == "p"


def test_no_false_match_for_unlisted_hospital():
    entries = parse_hpt(
        "location-name: HonorHealth Deer Valley Medical Center\nmrf-url: a\n"
        "location-name: HonorHealth Four Peaks Medical Center\nmrf-url: b\n"
    )
    e, score = match_location("HONORHEALTH TEMPE MEDICAL CENTER", entries)
    assert e is None and score < 85


def test_pinned_location():
    entries = parse_hpt("location-name: Western Regional Medical Center\nmrf-url: w\n")
    e, _ = match_location("CITY OF HOPE CANCER CENTER PHOENIX", entries, pinned="Western Regional Medical Center")
    assert e.mrf_url == "w"


# ------------------------------------------------------------------ format sniffing

META = "hospital_name,last_updated_on,version,location_name\nTest Hospital,2026-09-30,3.0.0,Main\n"
TALL = META + "description,code|1,code|1|type,setting,standard_charge|gross,payer_name,plan_name,standard_charge|negotiated_dollar\n" \
              "MRI knee,73721,CPT,outpatient,2500,Aetna,PPO,900\n"
WIDE_TRUNCATED = META + "description,code|1,code|1|type,standard_charge|gross,standard_charge|Aetna|PPO|negotiated_dollar," + "x" * 70000


def test_sniff_cms_csv_tall():
    s = sniff(TALL.encode())
    assert (s.format, s.version, s.last_updated_on, s.hospital_name) == ("cms_csv_tall", "3.0.0", "2026-09-30", "Test Hospital")


def test_sniff_cms_csv_wide_with_truncated_header():
    s = sniff(WIDE_TRUNCATED.encode()[:65536])
    assert s.format == "cms_csv_wide"


def test_sniff_cms_json():
    s = sniff(b'{"hospital_name": "X", "last_updated_on": "2026-07-23", "version": "3.0.0", "standard_charge_information": [')
    assert (s.format, s.version) == ("cms_json", "3.0.0")


def test_sniff_zip_and_gzip_look_inside():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("123_h_standardcharges.csv", TALL * 50)
    s = sniff(buf.getvalue()[:4000])
    assert s.format == "cms_csv_tall" and "123_h_standardcharges.csv" in s.note
    assert sniff(gzip.compress(TALL.encode())).format == "cms_csv_tall"


def test_sniff_html_and_legacy():
    assert sniff(b"<!DOCTYPE html><html><head><title>Just a moment</title>").format == "html"
    assert sniff(b"Charge Code,Description,Price\n1,MRI,100\n").format == "legacy_csv"


# ------------------------------------------------------------------ misc

def test_scope_reason():
    cfg = {"in_scope_types": ["Acute Care Hospitals"], "exempt_ownership": ["Government - Federal"]}
    assert scope_reason("Acute Care Hospitals", "Proprietary", cfg) == ""
    assert "federal" in scope_reason("Acute Care Hospitals", "Government - Federal", cfg)
    assert "Psychiatric" in scope_reason("Psychiatric", "Proprietary", cfg)


def test_total_size():
    assert total_size({"Content-Range": "bytes 0-65535/141241152"}, 206) == 141241152
    assert total_size({"Content-Length": "500"}, 200) == 500
    assert total_size({"Content-Length": "65536"}, 206) is None
