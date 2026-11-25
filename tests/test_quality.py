"""Phase 4 tests: Medicare base rates, row quality labels, compliance score, findings, M4 sample."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from hpc.quality import compliance as C
from hpc.quality.rows import is_placeholder, label_rows
from hpc.reference import medicare as M


# ------------------------------------------------------------------ Medicare

def test_ipps_base_rate_matches_hand_calculation():
    inputs = {"op_gt1": (4456.72, 2295.89), "op_le1": (4186.62, 2565.99), "capital": 524.15}
    # DRG 470 at the Phoenix wage index (FY 2026 values)
    expected = 1.9289 * (4456.72 * 1.0741 + 2295.89 + 524.15 * 1.0741 ** 0.6848)
    assert M.ipps_base_rate(1.9289, 1.0741, inputs) == pytest.approx(expected)
    assert round(expected, 2) == 14723.87
    # wage index <= 1 uses the Table 1B split
    assert M.ipps_base_rate(1.0, 0.9, inputs) == pytest.approx(4186.62 * 0.9 + 2565.99 + 524.15 * 0.9 ** 0.6848)


def test_ipps_inputs_parse_cms_text_tables(tmp_path):
    (tmp_path / "CMS-1833-F Tables 1A - 1E.txt").write_text(
        'TABLE 1A ...\nLabor-related\tNonlabor-related\n"$4,456.72 "\t"$2,295.89 "\t"$4,349.21 "\t"$2,240.51 "\t'
        '"$4,420.88 "\t"$2,277.43 "\t"$4,313.38 "\t"$2,222.05 "\n'
        'TABLE 1B ...\n"$4,186.62 "\t"$2,565.99 "\t"$4,085.63 "\t"$2,504.09 "\t"$4,152.95 "\t"$2,545.36 "\t'
        '"$4,051.97 "\t"$2,483.46 "\nTABLE 1D. -  CAPITAL STANDARD FEDERAL PAYMENT RATE\n\tRate\nNational\t$524.15 \n',
        encoding="latin-1")
    (tmp_path / "CMS-1833-F Table 5.txt").write_text(
        '"TABLE 5.-LIST OF MEDICARE SEVERITY DIAGNOSIS-RELATED GROUPS (MS-DRGS), ..."\n'
        "MS-DRG \tMS-DRG Title\tWeights - Before Cap\tWeights - 10% Cap Applied \n"
        "470\tMAJOR HIP AND KNEE\t1.9289\t1.9289\n015\tRETIRED\t.\t.\n", encoding="latin-1")
    (tmp_path / "CMS-1833-F Tables 2.txt").write_text(
        '"TABLE 2- CASE MIX INDEX AND WAGE INDEX TABLE BY CCN"\n'
        "1CCN\tFY 2025 Wage Index\t3,6 FY 2026 Wage Index With Cap\n030002\t1.1097\t1.0741\n", encoding="latin-1")
    (tmp_path / "CMS-1833-F Tables 3.txt").write_text(
        '"Table 3- WAGE INDEX TABLE BY CBSA"\nCBSA\tArea Name\tState\tWage Index\n38060\tPhoenix\tAZ\t1.0741\n',
        encoding="latin-1")
    i = M.ipps_inputs(tmp_path)
    assert i["op_gt1"] == (4456.72, 2295.89) and i["op_le1"] == (4186.62, 2565.99) and i["capital"] == 524.15
    assert i["weights"] == {"470": 1.9289}             # the retired DRG ('.') is skipped
    assert i["wage_by_ccn"] == {"030002": 1.0741} and i["wage_by_cbsa"]["38060"] == 1.0741


# ------------------------------------------------------------------ row labels

def test_is_placeholder():
    s = pd.Series([0, 0.01, 1.0, 1.5, 99999, 9999999.99, 12345, np.nan])
    assert is_placeholder(s).tolist() == [True, True, True, False, True, True, False, False]


def row(**kw):
    base = dict(ccn="030001", procedure_id="mri_knee", payer="Aetna", plan="PPO", amount_usd=800.0,
                amount_basis="negotiated_dollar", rate_kind="dollar", gross_charge=2000.0, cash_price=900.0,
                min_charge=500.0, max_charge=1200.0, is_per_diem=False, comparable=True)
    base.update(kw)
    return base


def labels(rows, medicare=None):
    out = label_rows(pd.DataFrame(rows), medicare)
    return out


def test_label_basic_rules():
    out = labels([
        row(),                                                    # good
        row(amount_usd=1.0),                                      # placeholder
        row(amount_usd=np.nan, rate_kind="algorithm"),            # formula only
        row(amount_usd=np.nan, rate_kind="percentage", gross_charge=np.nan),   # % without a gross charge
        row(amount_usd=2500.0, max_charge=3000.0),                # above gross (2000)
        row(min_charge=900.0, max_charge=100.0),                  # min > max
        row(amount_usd=1500.0, amount_basis="percent_of_gross"),  # converted %, above file max (1200)
    ])
    assert out["quality_label"].tolist() == ["good", "rejected", "rejected", "rejected", "suspect", "suspect", "suspect"]
    assert out.loc[3, "quality_flags"] == "percent_no_gross"
    assert "converted from % of charges" in out.loc[6, "quality_reasons"]


def test_label_medicare_multiples_and_per_diem():
    med = pd.DataFrame({"ccn": ["030001"], "procedure_id": ["drg470"], "medicare_rate": [14724.0],
                        "source": ["ipps_fy2026"]})
    out = labels([
        row(procedure_id="drg470", amount_usd=30000.0, gross_charge=90000.0, min_charge=np.nan, max_charge=np.nan),
        row(procedure_id="drg470", amount_usd=3000.0, gross_charge=90000.0, min_charge=np.nan, max_charge=np.nan),
        row(procedure_id="drg470", amount_usd=1000.0, gross_charge=90000.0, min_charge=np.nan, max_charge=np.nan),
        row(procedure_id="drg470", amount_usd=3000.0, gross_charge=90000.0, is_per_diem=True,
            min_charge=np.nan, max_charge=np.nan),
    ], med)
    assert out["medicare_multiple"].round(2).tolist()[:3] == [2.04, 0.2, 0.07]
    assert out["quality_label"].tolist() == ["good", "suspect", "rejected", "good"]
    assert pd.isna(out.loc[3, "medicare_multiple"])      # per-day rate isn't compared with a stay


def test_label_market_outlier_only_without_medicare():
    rows = [row(ccn=f"03000{i}", amount_usd=a, max_charge=np.nan, min_charge=np.nan, gross_charge=np.nan)
            for i, a in enumerate([500, 520, 480, 510, 30])]
    out = labels(rows)
    assert out["quality_label"].tolist() == ["good", "good", "good", "good", "suspect"]
    assert out.loc[4, "quality_flags"] == "market_outlier"


def test_label_same_amount_for_every_code():
    procs = ["mri_knee", "lab_cbc", "colonoscopy", "c_section", "ecg"]
    rows = [row(procedure_id=p, amount_usd=4160.0, gross_charge=np.nan, min_charge=np.nan, max_charge=np.nan,
                plan="EXCHANGE") for p in procs]
    rows += [row(procedure_id=p, amount_usd=100.0 + i, gross_charge=np.nan, min_charge=np.nan, max_charge=np.nan)
             for i, p in enumerate(procs)]
    out = labels(rows)
    assert (out.loc[out["plan"] == "EXCHANGE", "quality_flags"].str.contains("same_amount_every_code")).all()
    assert not out.loc[out["plan"] == "PPO", "quality_flags"].str.contains("same_amount").any()


def test_cash_labels():
    out = labels([row(), row(cash_price=1.0), row(cash_price=2500.0), row(cash_price=np.nan)])
    assert out["cash_label"].tolist() == ["good", "rejected", "suspect", ""]


# ------------------------------------------------------------------ compliance score

def hosp(**kw):
    base = dict(ccn="030001", name="TEST HOSPITAL", hpt_status="found", hpt_missing_fields="",
                price_file_status="usable", file_format="cms_csv_tall", cms_version="3.0.0",
                file_date="2026-09-30", mrf_url_source="cms-hpt.txt", file_hospital_name="Test Hospital")
    base.update(kw)
    return base


def prices(n_proc=4, label="good", payer="Aetna", **kw):
    rows = []
    for i in range(n_proc):
        r = dict(procedure_id=f"p{i}", source_item=i, payer=payer, plan="PPO", amount_usd=100.0,
                 gross_charge=200.0, cash_price=150.0, setting="outpatient", quality_label=label,
                 quality_flags="", comparable=True, match_method="code", amount_basis="negotiated_dollar",
                 description=f"item {i}")
        r.update(kw)
        rows.append(r)
    return pd.DataFrame(rows)


TODAY = date(2026, 11, 22)


def test_perfect_hospital_scores_100():
    s = C.score_hospital(hosp(), prices(), n_procedures=4, today=TODAY)
    assert s["score"] == 100 and C.grade(s["score"]) == "A"


def test_blocked_and_missing_and_unprocessed():
    blocked = C.score_hospital(hosp(hpt_status="blocked", price_file_status="blocked", file_format=""), None, 4, TODAY)
    assert blocked["score"] == 0
    not_listed = C.score_hospital(hosp(hpt_status="location_not_listed", price_file_status="not_listed",
                                       file_format=""), None, 4, TODAY)
    assert not_listed["score"] == 5
    mayo = C.score_hospital(hosp(), None, 4, TODAY)
    assert mayo["score"] is None and mayo["score_status"] == "not processed"


def test_freshness_points():
    pts = lambda d: C.score_hospital(hosp(file_date=d), prices(), 4, TODAY)["points"]["freshness"]
    assert (pts("2026-01-01"), pts("2025-08-01"), pts("2024-12-01")) == (10, 5, 0)


def test_no_insurer_rates_caps_score_at_50():
    s = C.score_hospital(hosp(), prices(payer=""), 4, TODAY)
    assert s["score"] == 50 and s["capped"]


def test_validity_counts_invalid_not_formulas():
    p = prices(n_proc=4)
    p.loc[0, ["quality_label", "quality_flags", "amount_usd"]] = ["rejected", "formula_only", np.nan]
    formula = C.score_hospital(hosp(), p, 4, TODAY)["points"]
    p2 = prices(n_proc=4)
    p2.loc[0, ["quality_label", "quality_flags"]] = ["rejected", "placeholder_amount"]
    placeholder = C.score_hospital(hosp(), p2, 4, TODAY)["points"]
    assert formula["data_validity"] == 15 and formula["data_elements"] < 15    # formulas lower data elements
    assert placeholder["data_validity"] == pytest.approx(15 * 0.75)            # placeholders lower validity


def test_twins_and_findings():
    a, b, c = prices(), prices(), prices(amount_usd=999.0)
    sigs = {"1": C.price_list_signature(a), "2": C.price_list_signature(b), "3": C.price_list_signature(c)}
    twins = C.find_twins(sigs, {"1": "AGH MESA", "2": "AGH LAVEEN", "3": "OTHER"})
    assert twins == {"1": ["Agh Laveen"], "2": ["Agh Mesa"]}

    # parser matched nothing by code; only description-matched gross/cash rows exist
    f = C.findings(hosp(), prices(payer="", match_method="description_accepted"),
                   {"status": "parsed", "items_matched": 0, "items_scanned": 9473},
                   {"code_type_aliases": {"CDM": "CPT"}}, [], {"points": {"freshness": 10}})
    text = " ".join(f)
    assert "none of the chosen procedures by code" in text and "no insurer-specific" in text
    assert "mislabels code types (CDM as CPT)" in text
    blocked = C.findings(hosp(hpt_status="blocked", price_file_status="blocked", mrf_url_source="manual"),
                         None, {}, {}, [], {"points": {}})
    assert any("refuses automated access" in x for x in blocked)


def test_grade_boundaries():
    assert [C.grade(x) for x in (85, 84.9, 70, 55, 40, 39.9, None)] == ["A", "B", "B", "C", "D", "F", "not scored"]


# ------------------------------------------------------------------ M4 sample

def test_m4_sample_spreads_across_hospitals_and_systems():
    from hpc.quality.run import m4_sample
    rng = np.random.default_rng(0)
    rows = []
    for h in range(12):
        for p in range(8):
            rows.append(dict(ccn=f"03{h:04d}", procedure_id=f"p{p}", payer="Aetna", payer_id="aetna",
                             payer_name="Aetna", plan="PPO", amount_usd=float(rng.integers(50, 5000)),
                             is_canonical=True, comparable=True, quality_label="good" if p % 3 else "suspect",
                             matched_code="1", matched_code_type="CPT", description="d", price_setting="outpatient",
                             amount_basis="negotiated_dollar", gross_charge=1.0, cash_price=1.0, quality_reasons=""))
    lab = pd.DataFrame(rows)
    reg = pd.DataFrame({"ccn": [f"03{h:04d}" for h in range(12)], "name": [f"H{h}" for h in range(12)],
                        "health_system": ["Big"] * 8 + ["A", "B", "C", "D"], "source_page_url": "u"})
    s = m4_sample(lab, reg, n_good=6, n_flagged=4)
    assert s["hospital"].is_unique
    assert (s["health_system"] == "Big").sum() <= 2 + 3
    assert {"YOUR_RESULT (matches / different / cant_find)", "hospital_price_page"} <= set(s.columns)


def test_market_outlier_compares_within_plan_type():
    # Medicaid prices are low; a commercial price of $36 must be judged against commercial prices
    rows = [row(ccn=f"m{i}", amount_usd=a, plan_type="medicaid", max_charge=np.nan, min_charge=np.nan,
                gross_charge=np.nan) for i, a in enumerate([30, 35, 40, 45, 50, 60])]
    rows += [row(ccn=f"c{i}", amount_usd=a, plan_type="commercial", max_charge=np.nan, min_charge=np.nan,
                 gross_charge=np.nan) for i, a in enumerate([550, 600, 650, 36])]
    out = labels(rows)
    assert out.loc[out["amount_usd"] == 36, "quality_label"].iloc[0] == "suspect"
    assert (out.loc[out["plan_type"] == "medicaid", "quality_label"] == "good").all()


def test_component_line_needs_tiny_gross_and_tiny_price():
    base = dict(max_charge=np.nan, min_charge=np.nan, plan_type="medicare_advantage")
    rows = [row(ccn=f"h{i}", amount_usd=a, gross_charge=g, source_item=i, **base)
            for i, (a, g) in enumerate([(250, 1500), (260, 1600), (240, 1400), (255, 1550)])]
    rows.append(row(ccn="chandler", amount_usd=35.96, gross_charge=35.96, source_item=9, **base))   # both tiny
    rows.append(row(ccn="cheaplab", amount_usd=245, gross_charge=60, source_item=8, **base))         # only gross tiny
    out = labels(rows).set_index("ccn")
    assert "component_line" in out.loc["chandler", "quality_flags"]
    assert "component_line" not in out.loc["cheaplab", "quality_flags"]
