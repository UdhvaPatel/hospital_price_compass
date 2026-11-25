"""Source audit tests: a correct price passes, and each kind of corruption is caught."""
import json

import numpy as np
import pandas as pd
import pytest

from hpc.quality.audit import OK, audit

TALL = """hospital_name,last_updated_on,version
H,2026-09-30,3.0.0
description,code|1,code|1|type,code|2,code|2|type,setting,standard_charge|gross,standard_charge|discounted_cash,payer_name,plan_name,standard_charge|negotiated_dollar,standard_charge|negotiated_percentage
MRI KNEE,4501,CDM,73721,HCPCS,outpatient,2000,900,Aetna,PPO,800,
MRI KNEE,4501,CDM,73721,HCPCS,outpatient,2000,900,Cigna,HMO,,60
LAB,123,CDM,80053,CPT,outpatient,,,Aetna,PPO,40,
"""

WIDE = """hospital_name,last_updated_on,version
W,2026-10-29,3.0.0
description,code|1,code|1|type,setting,standard_charge|gross,standard_charge|discounted_cash,standard_charge|Aetna|PPO|negotiated_dollar,standard_charge|BCBS|HMO|negotiated_percentage
CMP,80053,CPT,outpatient,300,120,95,50
"""

JSON = {"hospital_name": "J", "standard_charge_information": [
    {"description": "SUPPLY", "code_information": [{"code": "1", "type": "CDM"}], "standard_charges": []},
    {"description": "JOINT", "code_information": [{"code": "470", "type": "MS-DRG"}],
     "standard_charges": [{"setting": "inpatient", "gross_charge": 90000, "payers_information": [
         {"payer_name": "Aetna", "plan_name": "Commercial|PPO", "standard_charge_dollar": 39389}]}]},
]}


@pytest.fixture
def files(tmp_path):
    (tmp_path / "t.csv").write_text(TALL, encoding="utf-8")
    (tmp_path / "w.csv").write_text(WIDE, encoding="utf-8")
    (tmp_path / "j.json").write_text(json.dumps(JSON), encoding="utf-8")
    return {"T": str(tmp_path / "t.csv"), "W": str(tmp_path / "w.csv"), "J": str(tmp_path / "j.json")}


def sample():
    rows = [
        # tall: dollar rate, converted percentage rate (60% x 2000), row with blank gross/cash
        dict(ccn="T", source_format="cms_csv_tall", source_item=4, matched_code="73721", description="MRI KNEE",
             setting="outpatient", payer="Aetna", plan="PPO", amount_usd=800.0, amount_basis="negotiated_dollar",
             gross_charge=2000.0, cash_price=900.0),
        dict(ccn="T", source_format="cms_csv_tall", source_item=5, matched_code="73721", description="MRI KNEE",
             setting="outpatient", payer="Cigna", plan="HMO", amount_usd=1200.0, amount_basis="percent_of_gross",
             gross_charge=2000.0, cash_price=900.0),
        dict(ccn="T", source_format="cms_csv_tall", source_item=6, matched_code="80053", description="LAB",
             setting="outpatient", payer="Aetna", plan="PPO", amount_usd=40.0, amount_basis="negotiated_dollar",
             gross_charge=np.nan, cash_price=np.nan),
        # wide: dollar and percentage columns
        dict(ccn="W", source_format="cms_csv_wide", source_item=4, matched_code="80053", description="CMP",
             setting="outpatient", payer="Aetna", plan="PPO", amount_usd=95.0, amount_basis="negotiated_dollar",
             gross_charge=300.0, cash_price=120.0),
        dict(ccn="W", source_format="cms_csv_wide", source_item=4, matched_code="80053", description="CMP",
             setting="outpatient", payer="BCBS", plan="HMO", amount_usd=150.0, amount_basis="percent_of_gross",
             gross_charge=300.0, cash_price=120.0),
        # json: second item (index 1); DRG code written without leading zeros
        dict(ccn="J", source_format="cms_json", source_item=1, matched_code="470", description="JOINT",
             setting="inpatient", payer="Aetna", plan="Commercial|PPO", amount_usd=39389.0,
             amount_basis="negotiated_dollar", gross_charge=90000.0, cash_price=np.nan),
    ]
    return pd.DataFrame(rows)


def test_clean_sample_passes(files):
    r = audit(sample(), files)
    assert r["passed"].all(), r[OK + ["ccn", "source_item"]]


@pytest.mark.parametrize("change,col", [
    (lambda s: s.assign(amount_usd=s["amount_usd"] * 1.01), "ok_amount"),
    (lambda s: s.assign(amount_usd=s["amount_usd"] + 1), "ok_amount"),
    (lambda s: s.assign(payer="Humana"), "ok_insurer"),
    (lambda s: s.assign(plan=s["plan"] + " X"), "ok_plan"),
    (lambda s: s.assign(matched_code="99999"), "ok_code"),
    (lambda s: s.assign(gross_charge=s["gross_charge"] * 1.05), "ok_gross"),
    (lambda s: s.assign(cash_price=s["cash_price"] + 2), "ok_cash"),
    (lambda s: s.assign(description="SOMETHING ELSE"), "ok_description"),
])
def test_each_corruption_is_caught(files, change, col):
    s = change(sample())
    # rows whose corrupted value was missing to begin with stay "both missing" and can't be caught
    if col == "ok_gross":
        s = s[s["gross_charge"].notna()]
    if col == "ok_cash":
        s = s[s["cash_price"].notna()]
    r = audit(s, files)
    assert (~r["passed"]).all()
    assert (~r[col]).all()


def test_wrong_record_and_missing_file(files):
    s = sample()
    s.loc[0, "source_item"] = 6            # points at the LAB row instead of the MRI row
    r = audit(s, files)
    wrong = r[(r["source_item"] == 6) & (r["matched_code"] == "73721")]
    legit = r[(r["source_item"] == 6) & (r["matched_code"] == "80053")]
    assert len(wrong) == 1 and not wrong["passed"].iloc[0]       # MRI expected, LAB found
    assert len(legit) == 1 and legit["passed"].iloc[0]           # the real LAB row still passes
    r2 = audit(sample(), {"T": files["T"]})
    assert set(r2.loc[~r2["passed"], "audit_note"]) == {"source file not on disk"}
