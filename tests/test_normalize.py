"""Phase 3 tests: payers, plans, description matching, rate types and duplicates.
A fake embedder keeps them offline and deterministic."""
import numpy as np
import pandas as pd
import pytest

from hpc.nlp.embeddings import EmbeddingCache, Embedder, best_matches
from hpc.normalize import payers as P
from hpc.normalize.descriptions import DescriptionMatcher, _contrast, sibling_rule
from hpc.normalize.rates import dedupe, settings_and_scope, to_dollars


class FakeEmbedder:
    """Texts listed in `fixed` get fixed vectors; anything else gets a bag-of-words vector."""

    def __init__(self, fixed=None, dim=64):
        self.fixed, self.dim = fixed or {}, dim

    def _bow(self, t):
        v = np.zeros(self.dim)
        for w in t.upper().replace("/", " ").split():
            v[hash(w) % self.dim] += 1
        return v

    def _vec(self, t):
        if t in self.fixed:
            v = np.zeros(self.dim)
            v[:len(self.fixed[t])] = self.fixed[t]   # pad fixed vectors to the common length
            return v
        return self._bow(t)

    def embed(self, texts, input_type="query"):
        vs = np.array([self._vec(t) for t in texts], dtype=float)
        n = np.linalg.norm(vs, axis=1, keepdims=True)
        return vs / np.where(n == 0, 1, n)


# ------------------------------------------------------------------ embeddings + cache

def test_embedder_caches_to_disk(tmp_path):
    calls = []

    def backend(texts, input_type):
        calls.append(list(texts))
        return [[len(t), 1.0] for t in texts]

    e = Embedder(backend=backend, cache=EmbeddingCache("m", tmp_path))
    v = e.embed(["aa", "b", "aa"])
    assert v.shape == (3, 2) and np.allclose(np.linalg.norm(v, axis=1), 1) and calls == [["aa", "b"]]
    e2 = Embedder(backend=backend, cache=EmbeddingCache("m", tmp_path))
    e2.embed(["b", "aa"])
    assert len(calls) == 1        # second instance served from the disk cache


def test_best_matches_takes_best_alias_per_label():
    q = np.array([[1.0, 0.0]])
    t = np.array([[0.6, 0.8], [1.0, 0.0], [0.0, 1.0]])
    (best, s, second, s2), = best_matches(q, t, ["a", "a", "b"])
    assert (best, round(s, 2), second, round(s2, 2)) == ("a", 1.0, "b", 0.0)


# ------------------------------------------------------------------ payer names

@pytest.mark.parametrize("raw,clean,setting", [
    ("AETNA_PLAN_41124_-IP", "AETNA", "inpatient"),
    ("MEDICARE-O/P", "MEDICARE", "outpatient"),
    ("DEVOTED_HEALTH_ADV_-_SWING", "DEVOTED HEALTH ADV", "swing_bed"),
    ("BCBS_AZ_METROPOLITAN_TRUST_AMT001_-REC", "BCBS AZ METROPOLITAN TRUST", "recurring"),
    ("Care 1st", "CARE 1ST", ""),
    ("Wellpoint (fka Amerigroup)", "WELLPOINT", ""),
    ("Cigna Healthcare of Arizona, Inc.", "CIGNA HEALTHCARE OF ARIZONA", ""),
])
def test_clean_payer(raw, clean, setting):
    assert P.clean_payer(raw) == (clean, setting)


def _cfg():
    return P.PayerConfig(
        payers=[
            {"id": "tricare", "name": "TRICARE", "patterns": ["TRICARE"], "default_plan_type": "tricare"},
            {"id": "humana", "name": "Humana", "patterns": ["HUMANA"], "default_plan_type": "unknown"},
            {"id": "uhc", "name": "UnitedHealthcare", "patterns": [r"\bUHC\b", "UNITED ?HEALTH"], "default_plan_type": "unknown"},
            {"id": "bcbs_az", "name": "Blue Cross Blue Shield of Arizona", "patterns": [r"\bBCBS"], "default_plan_type": "commercial"},
        ],
        matching={"auto_accept": 0.72, "min_margin": 0.06, "review_floor": 0.55},
    )


def test_alias_order_and_decisions():
    cfg = _cfg()
    assert P.alias_match("TRICARE EAST HUMANA MILITARY", cfg) == "tricare"   # specific before general
    df = P.match_payers(["UHC", "Humana", "Some Employer"], cfg, None,
                        decisions={"SOME EMPLOYER": "bcbs_az"})
    got = dict(zip(df["payer_raw"], zip(df["payer_id"], df["method"])))
    assert got["UHC"] == ("uhc", "alias")
    assert got["Some Employer"] == ("bcbs_az", "decision")


def test_embedding_accept_review_and_unmatched():
    cfg = _cfg()
    e = FakeEmbedder({
        "UNITEDHEALTHCARE": [1, 0, 0, 0], "BLUE CROSS BLUE SHIELD OF ARIZONA": [0, 1, 0, 0],
        "TRICARE": [0, 0, 1, 0], "HUMANA": [0, 0, 0, 1],
        "UNTD HLTH CARE": [0.95, 0.1, 0, 0],          # close to UHC -> accept
        "MYSTERY NETWORK": [0.45, 0.45, 0.45, 0.45],  # equally far from all -> unmatched
        "HALFWAY CARE": [0.62, 0, 0, 0, 0.78],        # 0.62 to UHC, 0 to others -> review band
    })
    df = P.match_payers(["UNTD HLTH CARE", "MYSTERY NETWORK", "HALFWAY CARE"], cfg, e, decisions={})
    got = dict(zip(df["payer_raw"], zip(df["payer_id"], df["method"])))
    assert got["UNTD HLTH CARE"] == ("uhc", "embedding")
    assert got["MYSTERY NETWORK"][1] == "unmatched" and got["MYSTERY NETWORK"][0] == "other:mystery_network"
    assert got["HALFWAY CARE"][1] == "needs_review"


@pytest.mark.parametrize("pid,payer,plan,default,expected", [
    ("uhc", "UNITED HEALTHCARE", "Commercial", "unknown", "commercial"),
    ("uhc", "UNITED HEALTHCARE", "Commerical Exchange Product", "unknown", "exchange"),
    ("uhc", "UHC", "Medicare", "unknown", "medicare_advantage"),
    ("medicare", "MEDICARE", "MC", "medicare", "medicare"),
    ("bcbs_az", "BCBS", "Medicare Supplemental Senior Preferred", "commercial", "medicare_supplement"),
    ("uhc", "UHC", "Community Plan Medicare/Medicaid Managed Care Plan", "unknown", "medicare_advantage"),
    ("aetna", "AETNA", "AETNA WORKERS COMP", "unknown", "workers_comp"),
    ("aetna", "AETNA GEHA", "DCS", "unknown", "commercial"),           # coded plan, product in payer name
    ("mercy_care", "MERCY CARE", "CAC", "medicaid", "medicaid"),       # falls back to insurer default
    ("humana", "HUMANA", "WHC", "unknown", "unknown"),
])
def test_classify_plan(pid, payer, plan, default, expected):
    assert P.classify_plan(pid, payer, plan, default).plan_type == expected


def test_plan_network_and_professional_hint():
    info = P.classify_plan("cigna", "CIGNA", "CIGNA HEALTH PLAN - PPO", "commercial")
    assert info.network == "PPO" and not info.professional_hint
    assert P.classify_plan("x", "X", "MEDICARE ADVANTAGE - PHYSICIAN", "unknown").professional_hint


# ------------------------------------------------------------------ description matching

@pytest.mark.parametrize("desc,expected", [
    ("MRI SPINE LUMBAR WO/W CM", "both"), ("MRI BRAIN W/O & W/ CONTRAST", "both"), ("MRI BRAIN WWO", "both"),
    ("CT ABD & PELVIS W/O CM", "without"), ("CT HEAD/BRAIN WO CONTRST", "without"),
    ("MRI SPINE LUMBAR W/CM", "with"), ("CT ABD PELVIS W CONTRAST", "with"), ("US ABDOMEN COMPLETE", ""),
])
def test_contrast(desc, expected):
    assert _contrast(desc) == expected


ER = ["er_visit_level_3", "er_visit_level_4", "er_visit_level_5"]


@pytest.mark.parametrize("desc,cands,expected", [
    ("ER VISIT LVL IV", ER, ["er_visit_level_4"]),
    ("Level 5 Type A ED Visits", ER, ["er_visit_level_5"]),
    ("ED RPR WOUND LVL 03 COMPLEX", ER, []),             # procedure in the ER, not a visit level
    ("OB ER VISIT LVL III", ER, []),                     # labour & delivery triage
    ("ER LEVEL 2", ER, []),                              # level 2 isn't tracked as its own procedure
    ("CT ABD & PELVIS WO/W CM", ["ct_abdomen_pelvis", "ct_abdomen_pelvis_contrast"], []),
    ("CT ABD & PELVIS W/CM", ["ct_abdomen_pelvis", "ct_abdomen_pelvis_contrast"], ["ct_abdomen_pelvis_contrast"]),
    ("MRI BRAIN W/O CONTRAST", ["mri_brain"], []),       # tracked MRI brain is with-and-without
    ("COLONOSCOPY W/BIOPSY", ["colonoscopy", "colonoscopy_biopsy", "colonoscopy_polyp_removal"], ["colonoscopy_biopsy"]),
    ("COLONOSCOPY SNARE POLYP", ["colonoscopy", "colonoscopy_biopsy", "colonoscopy_polyp_removal"], ["colonoscopy_polyp_removal"]),
    ("CBC AUTO W/O DIFF", ["lab_cbc"], []),
    ("UPPER GI ENDOSCOPY / TUMOR", ["upper_gi_endoscopy"], []),
    ("DX MAMMO+CAD BILAT", ["mammogram_screening"], []),
])
def test_sibling_rules(desc, cands, expected):
    assert sibling_rule(desc, cands)[0] == expected


PROCS = {"procedure": [
    {"id": "lab_basic_metabolic", "name": "Basic metabolic panel", "keywords": r"BASIC METABOLIC|\bBMP\b", "aka": ["BMP"]},
    {"id": "mri_knee", "name": "MRI of the knee (no contrast)", "keywords": r"MRI.*KNEE", "aka": []},
]}


def test_description_matcher_accept_reject_review():
    e = FakeEmbedder({
        "Basic metabolic panel": [1, 0, 0], "BMP": [1, 0, 0], "MRI of the knee (no contrast)": [0, 1, 0],
        "BASIC METABOLIC PANEL": [1, 0, 0],
        "BMP ROUTINE": [0.5, 0, 0.3],          # weak: below threshold but above the reject anchors -> review
        "MRI KNEE GUIDED BIOPSY": [0, 0.3, 0.95],
    })
    # reject anchors all map to [0, 0, 1] so a guided biopsy lands closer to them
    import hpc.normalize.descriptions as D
    for a in D.REJECT_ANCHORS:
        e.fixed[a] = [0, 0, 1]
    m = DescriptionMatcher(e, PROCS, threshold=0.9, review_floor=0.5)
    res = {r.description: r for r in m.match(["BASIC METABOLIC PANEL", "BMP ROUTINE", "MRI KNEE GUIDED BIOPSY", "ASPIRIN"])}
    assert res["BASIC METABOLIC PANEL"].decision == "accepted" and res["BASIC METABOLIC PANEL"].procedure_id == "lab_basic_metabolic"
    assert res["MRI KNEE GUIDED BIOPSY"].decision == "rejected"
    assert res["BMP ROUTINE"].decision == "review"     # 0.86: beats reject anchors (0.51) but < threshold 0.9
    assert res["ASPIRIN"].reason == "no keyword match"


# ------------------------------------------------------------------ rates, settings, duplicates

def _rows(**over):
    base = dict(ccn="030001", procedure_id="mri_knee", payer="Aetna", plan="PPO", setting="outpatient",
                billing_class="facility", modifiers="", methodology="fee schedule", amount=None,
                negotiated_percentage=None, gross_charge=2000.0, cash_price=900.0, is_primary_code=True,
                match_method="code", source_item=1, contract_setting="", plan_professional_hint=False)
    base.update(over)
    return base


def test_to_dollars():
    df = pd.DataFrame([_rows(amount=800.0), _rows(negotiated_percentage=60.0),
                       _rows(negotiated_percentage=0.6), _rows(negotiated_percentage=60.0, gross_charge=None),
                       _rows(methodology="per diem", amount=3000.0)])
    out = to_dollars(df)
    assert out["amount_usd"].tolist()[:3] == [800.0, 1200.0, 1200.0]
    assert pd.isna(out["amount_usd"].iloc[3])
    assert out["amount_basis"].tolist()[:4] == ["negotiated_dollar", "percent_of_gross", "percent_of_gross_scaled", "none"]
    assert out["is_per_diem"].tolist() == [False, False, False, False, True]


def test_settings_scope_and_comparability():
    fam = {"mri_knee": "cpt", "joint_drg": "drg"}
    df = to_dollars(pd.DataFrame([
        _rows(amount=800.0),                                                   # ok
        _rows(amount=800.0, setting="inpatient"),                              # CPT priced inpatient
        _rows(amount=800.0, billing_class="professional"),                     # doctor fee
        _rows(amount=800.0, contract_setting="clinic"),                        # Wickenburg clinic contract
        _rows(procedure_id="joint_drg", setting="inpatient", amount=30000.0, methodology="case rate"),
        _rows(procedure_id="joint_drg", setting="inpatient", amount=3000.0, methodology="per diem"),
        _rows(amount=None, negotiated_percentage=None),                        # formula only
        _rows(amount=5000.0, methodology="case rate"),                         # surgical package
    ]))
    out = settings_and_scope(df, fam)
    assert out["comparable"].tolist() == [True, False, False, False, True, False, False, True]
    assert out["price_scope"].tolist()[2] == "professional" and out["price_scope"].tolist()[4] == "facility_stay"
    assert out["is_package"].tolist()[-1] and not out["is_package"].tolist()[0]


def test_dedupe_prefers_clean_rows_and_records_range():
    fam = {"mri_knee": "cpt"}
    rows = [
        _rows(amount=1000.0, modifiers="TC", source_item=1),
        _rows(amount=900.0, source_item=2),            # no modifier -> preferred over the TC row
        _rows(amount=950.0, source_item=3),
        _rows(amount=1100.0, source_item=4),           # three clean variants: keep the median (950)
        _rows(amount=700.0, plan="HMO", source_item=5, is_primary_code=False),
    ]
    out = dedupe(settings_and_scope(to_dollars(pd.DataFrame(rows)), fam))
    keep = out[out["is_canonical"]].set_index("plan")
    assert keep.loc["PPO", "amount_usd"] == 950.0
    assert keep.loc["PPO", "n_variants"] == 4 and keep.loc["PPO", "variant_min"] == 900.0
    assert keep.loc["PPO", "variant_max"] == 1100.0
    assert keep.loc["HMO", "amount_usd"] == 700.0      # a different plan is its own group
    assert out["is_canonical"].sum() == 2


# ------------------------------------------------------------------ API key handling

def test_api_key_from_env_file(tmp_path, monkeypatch):
    import hpc.config
    from hpc.parse import nim
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(hpc.config, "ROOT", tmp_path)
    assert nim.api_key_from_env() == ""
    (tmp_path / ".env").write_text("OTHER=1\nNVIDIA_API_KEY=nvapi-test-123\n", encoding="utf-8")
    assert nim.api_key_from_env() == "nvapi-test-123"
    monkeypatch.setenv("NVIDIA_API_KEY", "from-env")
    assert nim.api_key_from_env() == "from-env"        # environment wins over .env
