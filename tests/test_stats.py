"""Phase 5 tests: a hand-computed example for each statistic."""
import numpy as np
import pandas as pd
import pytest

from hpc.stats import core, drivers, history


def price_rows(spec):
    """spec: list of (ccn, procedure, payer_id, plan, plan_type, amount, extra-dict)."""
    rows = []
    for ccn, proc, pid, plan, ptype, amt, *extra in spec:
        r = dict(ccn=ccn, procedure_id=proc, payer_id=pid, payer_name=pid.upper(), payer="X", plan=plan,
                 plan_type=ptype, amount_usd=amt, is_canonical=True, comparable=True, quality_label="good",
                 medicare_rate=np.nan, cash_price=np.nan, cash_label="", gross_charge=np.nan,
                 is_primary_code=True, source_item=1)
        if extra:
            r.update(extra[0])
        rows.append(r)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ hospital prices + spread

def test_hospital_price_is_median_over_plans_of_good_prices_only():
    p = price_rows([
        ("A", "mri", "aetna", "PPO", "commercial", 100.0),
        ("A", "mri", "aetna", "HMO", "commercial", 200.0),
        ("A", "mri", "aetna", "EPO", "commercial", 600.0),
        ("A", "mri", "aetna", "X1", "commercial", 9999.0, {"quality_label": "suspect"}),
        ("A", "mri", "aetna", "X2", "commercial", 9999.0, {"comparable": False}),
        ("A", "mri", "aetna", "X3", "commercial", 9999.0, {"is_canonical": False}),
    ])
    hp = core.hospital_prices(p)
    assert len(hp) == 1
    r = hp.iloc[0]
    assert (r["price"], r["plan_min"], r["plan_max"], r["n_plans"]) == (200.0, 100.0, 600.0, 3)


def test_describe_by_hand():
    d = core.describe(pd.Series([100.0, 200.0, 300.0, 400.0]))
    # numpy linear quantiles: p10 = 100 + 0.3*100, p90 = 300 + 0.7*100
    assert d == {"n_hospitals": 4, "median": 250.0, "min": 100.0, "max": 400.0, "p10": 130.0, "p90": 370.0,
                 "max_over_min": 4.0}
    two = core.describe(pd.Series([100.0, 300.0]))
    assert two["p10"] is None and two["max_over_min"] == 3.0     # too few hospitals for percentiles


def test_spread_counts_systems_and_cash():
    p = price_rows([("A", "mri", "aetna", "PPO", "commercial", 100.0), ("B", "mri", "aetna", "PPO", "commercial", 300.0),
                    ("C", "mri", "aetna", "PPO", "commercial", 500.0)])
    cash = pd.DataFrame({"ccn": ["A", "B"], "procedure_id": ["mri", "mri"], "cash": [80.0, 120.0], "gross": [1, 1]})
    s = core.spread_table(core.hospital_prices(p), cash, {"A": "Banner", "B": "Banner", "C": "Dignity"})
    a = s[s["payer_id"] == "aetna"].iloc[0]
    assert (a["n_hospitals"], a["n_systems"], a["median"], a["max_over_min"]) == (3, 2, 300.0, 5.0)
    c = s[s["payer_id"] == "cash"].iloc[0]
    assert (c["n_hospitals"], c["median"]) == (2, 100.0)


def test_cash_prices_median_of_good_items():
    p = price_rows([
        ("A", "mri", "aetna", "PPO", "commercial", 500.0, {"cash_price": 300.0, "cash_label": "good", "source_item": 1}),
        ("A", "mri", "cigna", "PPO", "commercial", 600.0, {"cash_price": 300.0, "cash_label": "good", "source_item": 1}),
        ("A", "mri", "uhc", "PPO", "commercial", 700.0, {"cash_price": 500.0, "cash_label": "good", "source_item": 2}),
        ("A", "mri", "bcbs", "PPO", "commercial", 700.0, {"cash_price": 1.0, "cash_label": "rejected", "source_item": 3}),
    ])
    c = core.cash_prices(p)
    assert c.loc[0, "cash"] == 400.0          # items 1 (300) and 2 (500); the repeat and the placeholder are ignored


# ------------------------------------------------------------------ Medicare and market multiples

def test_medicare_and_market_multiples():
    p = price_rows([
        ("A", "drg", "aetna", "PPO", "commercial", 30000.0, {"medicare_rate": 15000.0}),
        ("B", "drg", "aetna", "PPO", "commercial", 15000.0, {"medicare_rate": 15000.0}),
        ("A", "mri", "aetna", "PPO", "commercial", 100.0),
        ("B", "mri", "aetna", "PPO", "commercial", 200.0),
        ("C", "mri", "aetna", "PPO", "commercial", 400.0),
    ])
    hp = core.hospital_prices(p)
    assert hp.set_index(["ccn", "procedure_id"]).loc[("A", "drg"), "medicare_multiple"] == 2.0
    mm = core.medicare_multiples(hp)["by_plan_type"]
    assert mm.loc[0, "median"] == 1.5
    mi = core.market_index(hp)
    mri = mi[mi["procedure_id"] == "mri"].set_index("ccn")["market_multiple"]
    assert mri.to_dict() == {"A": 0.5, "B": 1.0, "C": 2.0}     # vs the median of 100, 200, 400
    hi = core.hospital_index(mi, min_prices=1).set_index("ccn")["price_index"]
    # A: drg 30000 / median(30000, 15000)=22500 -> 4/3 ; mri 0.5 -> geometric mean sqrt(4/3 * 0.5)
    assert hi["A"] == pytest.approx(np.sqrt(4 / 3 * 0.5))


# ------------------------------------------------------------------ insurer index

def test_insurer_index_recovers_known_ratios_regardless_of_hospital_level():
    # Hospitals differ a lot in level; at every hospital B pays 20% more than A, C pays 10% less.
    spec = []
    rng = np.random.default_rng(1)
    for h, level in zip("HIJKLM", [0.5, 0.8, 1.0, 1.5, 2.0, 3.0]):
        for proc, base in [("mri", 500.0), ("ct", 300.0), ("lab", 20.0)]:
            for pid, mult in [("a", 1.0), ("b", 1.2), ("c", 0.9)]:
                spec.append((h, proc, pid, "P", "commercial", base * level * mult * np.exp(rng.normal(0, 0.001))))
    hp = core.hospital_prices(price_rows(spec))
    ii = core.insurer_index(hp, min_hospitals=3, systems={h: h for h in "HIJKLM"}).set_index("payer_id")
    gm = (1.0 * 1.2 * 0.9) ** (1 / 3)                         # index is relative to the average insurer
    assert ii.loc["a", "index"] == pytest.approx(1.0 / gm, rel=1e-3)
    assert ii.loc["b", "index"] / ii.loc["a", "index"] == pytest.approx(1.2, rel=1e-3)
    assert ii.loc["c", "index"] / ii.loc["a", "index"] == pytest.approx(0.9, rel=1e-3)
    assert np.prod(ii["index"]) == pytest.approx(1.0)         # centred: geometric mean of indexes is 1
    assert {"ci_low_system", "ci_high_system"} <= set(ii.columns)


def test_insurer_index_excludes_rental_networks():
    spec = [(h, "mri", pid, "P", "commercial", v) for h in "HIJKL" for pid, v in [("a", 100.0), ("b", 120.0), ("rent", 300.0)]]
    ii = core.insurer_index(core.hospital_prices(price_rows(spec)), min_hospitals=3, exclude={"rent"})
    assert set(ii["payer_id"]) == {"a", "b"}


# ------------------------------------------------------------------ cash vs insured

def test_cash_vs_insured_by_hand():
    hp = pd.DataFrame({"ccn": ["A", "A", "A", "B", "B"], "procedure_id": ["mri"] * 5,
                       "payer_id": ["a", "b", "c", "a", "b"], "payer_name": list("ABCAB"),
                       "plan_type": ["commercial"] * 5, "price": [100.0, 300.0, 500.0, 100.0, 120.0]})
    cash = pd.DataFrame({"ccn": ["A", "B"], "procedure_id": ["mri", "mri"], "cash": [200.0, 150.0], "gross": [0, 0]})
    cv = core.cash_vs_insured(hp, cash)
    o = cv["overall"].iloc[0]
    # A: median insured 300 > cash 200 -> cash cheaper; B: median 110 < cash 150 -> not
    assert o["share_cash_below_median_insured"] == 0.5
    # insurer prices above cash: A 300, A 500 (2 of 5)
    assert o["share_insurer_prices_above_cash"] == pytest.approx(2 / 5)


# ------------------------------------------------------------------ drivers

def test_drivers_keep_a_real_effect_and_drop_noise():
    rng = np.random.default_rng(3)
    n_sys, per = 8, 4
    rows = []
    for s in range(n_sys):
        own = "for-profit" if s % 2 else "non-profit"
        for k in range(per):
            ccn = f"{s}{k}"
            y = (np.log(0.7) if own == "for-profit" else np.log(1.2)) + rng.normal(0, 0.05)
            rows.append(dict(ccn=ccn, price_index=float(np.exp(y)), system=f"S{s}", name=f"H{ccn}",
                             ownership=own, log_beds=rng.normal(5, 1), resident_to_bed=rng.random(),
                             dsh_pct=rng.random(), case_mix=rng.normal(2, 0.3), star_rating=rng.integers(1, 6),
                             system_share=per / (n_sys * per), specialty="no", hospital_type="acute"))
    df = pd.DataFrame(rows)
    res = drivers.explain(df[["ccn", "price_index"]], df.drop(columns=["price_index"]))
    t = res.table.set_index("feature")
    assert t.loc["ownership", "survives"] and t.loc["ownership", "gain_loso"] > 0.5
    for noise in ("log_beds", "resident_to_bed", "dsh_pct", "case_mix", "star_rating"):
        assert not t.loc[noise, "survives"], noise
    assert res.combined["features"] == ["ownership"]


def test_ownership_groups():
    assert [drivers.ownership_group(s) for s in
            ["Voluntary non-profit - Private", "Proprietary", "Government - Local", "Physician"]] == \
           ["non-profit", "for-profit", "government", "physician"]


# ------------------------------------------------------------------ change over time

def test_history_compare_by_hand(tmp_path):
    key = dict(procedure_id="mri", payer_id="a", plan_type="commercial")
    old = pd.DataFrame([{**key, "ccn": "A", "price": 100.0, "file_date": "2026-01-01"},
                        {**key, "ccn": "B", "price": 100.0, "file_date": "2026-01-01"},
                        {**key, "ccn": "C", "price": 100.0, "file_date": "2026-01-01"}])
    new = pd.DataFrame([{**key, "ccn": "A", "price": 110.0, "file_date": "2026-07-01"},   # new file, +10%
                        {**key, "ccn": "B", "price": 95.0, "file_date": "2026-07-01"},    # new file, -5%
                        {**key, "ccn": "C", "price": 130.0, "file_date": "2026-01-01"}])  # same file: ignored
    c = history.compare(old, new)
    assert c["compared"] == 2 and c["share_up"] == 0.5 and c["share_down"] == 0.5
    assert c["median_change"] == pytest.approx((0.10 - 0.05) / 2)
    assert history.compare_latest(tmp_path)["status"] == "baseline"
    hp = new.drop(columns=["file_date"])
    history.save_snapshot(hp, {"A": "2026-07-01"}, "2026-07-02", tmp_path)
    history.save_snapshot(hp, {"A": "2026-08-01"}, "2026-08-02", tmp_path)
    assert history.compare_latest(tmp_path)["status"] == "compared"
