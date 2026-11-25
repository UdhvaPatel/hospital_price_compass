"""Payer and plan normalisation: "BCBS AZ PPO", "Blue Cross Blue Shield of Arizona - PPO",
"BCBSAZ" -> one insurer (payer_id) and one plan type.

Order: saved decisions -> alias patterns -> NIM embeddings (auto-accept above threshold) ->
own cleaned name ("other:<slug>"), with borderline cases written to a review file.

Embedding anchors are the canonical names *plus every raw name the alias rules already
matched*, so an unknown spelling is compared with all known spellings of each insurer.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from hpc.config import CONFIG_DIR, DATA_DIR, load_toml
from hpc.nlp.embeddings import best_matches

DECISIONS_FILE = CONFIG_DIR / "payer_decisions.csv"
REVIEW_DIR = DATA_DIR / "review"

# Wickenburg-style contract suffixes: "AETNA_PLAN_41124_-IP", "MEDICARE-O/P", "SCAN_ADV_-_SWING"
_SUFFIX = re.compile(r"[\s_]*-[\s_]*(CLIN|I/?P|O/?P|PRO|REC(?:URRING)?|SW(?:ING)?)\s*$", re.I)
_SUFFIX_MEANING = {"CLIN": "clinic", "IP": "inpatient", "I/P": "inpatient", "OP": "outpatient",
                   "O/P": "outpatient", "PRO": "professional", "REC": "recurring",
                   "RECURRING": "recurring", "SW": "swing_bed", "SWING": "swing_bed"}


def clean_payer(raw: str) -> tuple[str, str]:
    """Return (cleaned upper-case name, contract setting from a suffix or '')."""
    s = (raw or "").strip()
    setting = ""
    m = _SUFFIX.search(s)
    if m:
        setting = _SUFFIX_MEANING.get(m.group(1).upper(), "")
        s = s[:m.start()]
    s = s.replace("_", " ")
    s = re.sub(r"\(fka [^)]*\)", "", s, flags=re.I)
    # plan numbers / group ids ("PLAN 41124", "AMT001", "12X42"), but keep ordinals like "1ST"
    s = re.sub(r"\b(PLAN\s+)?(?!\d+(?:ST|ND|RD|TH)\b)[A-Z]*\d[A-Z0-9]*\b", " ", s.upper())
    s = re.sub(r"\b(INC|LLC|CORP|CORPORATION|CO|THE)\b\.?", " ", s)
    s = re.sub(r"[^A-Z0-9/&+ ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bPLAN$", "", s).strip()
    return s, setting


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "unknown"


# ------------------------------------------------------------------ plan types

PLAN_TYPES = {
    "commercial": "Commercial (employer / individual)",
    "exchange": "Marketplace (ACA exchange)",
    "medicare_advantage": "Medicare Advantage",
    "medicare_supplement": "Medicare Supplement",
    "medicare": "Medicare (traditional)",
    "medicaid": "Medicaid (AHCCCS)",
    "tricare": "TRICARE",
    "va": "VA Community Care",
    "workers_comp": "Workers' compensation",
    "rental_network": "Rental network",
    "other_government": "Other government",
    "all_products": "All products (mixed)",
    "self_pay": "Self-pay",
    "unknown": "Unknown",
}

# (pattern, plan_type) in priority order; applied to "payer + plan" text
_PLAN_RULES = [
    (r"SUPPLEMENT|SUPPLMT|MEDIGAP", "medicare_supplement"),
    (r"MEDICARE\s*/\s*MEDICAID|DUAL COMPLETE|\bD-?SNP\b", "medicare_advantage"),
    (r"MEDICARE ADV|ADVANTAGE|\bADV\b|\bMAPD\b|MEDICARE MANAGED|MEDICARECOMPLETE|GROUP MEDICARE|"
     r"MEDICARE SOLUTION|\bSNP\b|OPTUMCARE|OPTUMHEALTH", "medicare_advantage"),
    (r"WORKERS? COMP|\bWORK COMP", "workers_comp"),
    (r"TRICARE", "tricare"),
    (r"\bVA\b|VETERAN|COMMUNITY CARE NETWORK", "va"),
    (r"FEDERAL PRISON|^GOVERNMENT$|^FEDERAL$", "other_government"),
    (r"EXCHANGE|MARKETPLACE|\bHIX\b|\bIFP\b|\bQHP\b|\bACA\b|INDIVIDUAL|HMOX|PPOX", "exchange"),
    (r"MEDICAID|AHCCCS|\bLTC\b|\bALTCS\b|KIDSCARE|FAMILY CARE|FAMILY PLAN|COMMUNITY PLAN|\bRBHA\b|"
     r"\bACC\b|COMPLETE CARE|\bCMDP\b", "medicaid"),
    (r"MEDICAL RENTAL|RENTAL", "rental_network"),
    (r"\bMEDICARE\b", "medicare_advantage"),   # a private insurer's "Medicare" plan is MA
    # "Commercial" and the common typo "Commerical"; employer / TPA products (GEHA = federal employees)
    (r"COMM?ER(C?IAL|ICAL)|\bHMO\b|\bPPO\b|\bEPO\b|\bPOS\b|EMPLOYEE|GROUP|NETWORK|OPEN ACCESS|LOCAL ?PLUS|"
     r"LOCAL ?FLEX|SUREFIT|NAVIGATE|OPTIONS|COMPASS|CHOICE PLUS|\bCORE\b|DOCTOR'?S PLAN|SAVINGS PLUS|"
     r"MARICOPA ?FOCUS|NEIGHBORHOOD|ALLIANCE|JOINT VENTURE|DESIGNATED|TRUST|\bTPA\b|\bGEHA\b|SUREST|"
     r"ALL SAVERS|\bUMR\b|MERITAIN|\bALLIED\b|INTERLINK|\bACN\b|\bSPP\b", "commercial"),
    (r"^ALL (PLANS|PRODUCTS|OTHER PLANS)\b|^ALL PRODUCTS", "all_products"),
]
_NETWORK = [(r"\bHMO\b", "HMO"), (r"\bPPO\b", "PPO"), (r"\bEPO\b", "EPO"), (r"\bPOS\b", "POS")]
_PROFESSIONAL = re.compile(r"PHYSICIAN|\bPCP\b|SPECIALIST|NON-?FACILITY|\bOFFICE\b|TELEHEALTH|MID-LEVEL|"
                           r"\bCLINICAL\b|\bPROFESSIONAL\b", re.I)


@dataclass
class PlanInfo:
    plan_type: str
    network: str
    professional_hint: bool


def classify_plan(payer_id: str, payer_text: str, plan: str, default_plan_type: str) -> PlanInfo:
    plan_u = (plan or "").upper().replace("|", " ")
    text = f"{payer_text} {plan_u}".upper()
    plan_type = ""
    if payer_id == "medicare" and not re.search(r"ADVANTAGE|\bADV\b", text):
        plan_type = "medicare"
    elif payer_id == "self_pay":
        plan_type = "self_pay"
    else:
        # plan text first (it is more specific), then payer + plan together
        for target in (plan_u, text):
            for pat, pt in _PLAN_RULES:
                if re.search(pat, target):
                    plan_type = pt
                    break
            if plan_type:
                break
    if not plan_type or plan_type == "all_products" and default_plan_type not in ("unknown", ""):
        plan_type = plan_type or default_plan_type or "unknown"
    network = next((n for pat, n in _NETWORK if re.search(pat, text)), "")
    return PlanInfo(plan_type, network, bool(_PROFESSIONAL.search(plan or "")))


# ------------------------------------------------------------------ payer matching

@dataclass
class PayerConfig:
    payers: list[dict]
    matching: dict

    @classmethod
    def load(cls) -> "PayerConfig":
        cfg = load_toml("payers.toml")
        return cls(cfg["payer"], cfg["matching"])

    def by_id(self) -> dict[str, dict]:
        return {p["id"]: p for p in self.payers}


def load_decisions(path: Path = DECISIONS_FILE) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["payer_clean"].strip().upper(): r["payer_id"].strip()
                for r in csv.DictReader(f) if r.get("payer_clean") and r.get("payer_id")}


def alias_match(clean: str, cfg: PayerConfig) -> str | None:
    for p in cfg.payers:
        if any(re.search(pat, clean) for pat in p.get("patterns", [])):
            return p["id"]
    return None


def match_payers(raw_names: list[str], cfg: PayerConfig, embedder, decisions: dict[str, str] | None = None
                 ) -> pd.DataFrame:
    """One row per distinct raw payer name with the chosen payer_id and how it was chosen."""
    decisions = decisions if decisions is not None else load_decisions()
    by_id = cfg.by_id()
    m = cfg.matching
    rows = []
    for raw in dict.fromkeys(raw_names):
        clean, setting = clean_payer(raw)
        rows.append({"payer_raw": raw, "payer_clean": clean, "contract_setting": setting})
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["payer_id"], df["method"], df["score"] = "", "", np.nan
    df["suggested_id"], df["suggested_score"], df["runner_up_id"], df["runner_up_score"] = "", np.nan, "", np.nan
    for i, r in df.iterrows():
        if r.payer_clean in decisions:
            df.loc[i, ["payer_id", "method", "score"]] = [decisions[r.payer_clean], "decision", 1.0]
        elif (pid := alias_match(r.payer_clean, cfg)):
            df.loc[i, ["payer_id", "method", "score"]] = [pid, "alias", 1.0]

    todo = df[df["payer_id"] == ""]
    if len(todo) and embedder is not None:
        known = df[df["method"].isin(["alias", "decision"]) & ~df["payer_id"].str.startswith("other")]
        anchor_text = [p["name"].upper() for p in cfg.payers] + known["payer_clean"].tolist()
        anchor_ids = [p["id"] for p in cfg.payers] + known["payer_id"].tolist()
        a_vecs = embedder.embed(anchor_text)
        q_vecs = embedder.embed([t or "(blank)" for t in todo["payer_clean"]])
        for idx, (best, score, second, s2) in zip(todo.index, best_matches(q_vecs, a_vecs, anchor_ids)):
            df.loc[idx, ["suggested_id", "suggested_score", "runner_up_id", "runner_up_score"]] = [best, score, second, s2]
            if score >= m["auto_accept"] and score - s2 >= m["min_margin"]:
                df.loc[idx, ["payer_id", "method", "score"]] = [best, "embedding", score]

    rest = df["payer_id"] == ""
    df.loc[rest, "payer_id"] = "other:" + df.loc[rest, "payer_clean"].map(slug)
    df.loc[rest, "method"] = np.where(df.loc[rest, "suggested_score"].fillna(0) >= m["review_floor"],
                                      "needs_review", "unmatched")
    df["payer_name"] = [
        by_id[p]["name"] if p in by_id else (c.title() if c else r) for p, c, r in
        zip(df["payer_id"], df["payer_clean"], df["payer_raw"])
    ]
    df["payer_parent"] = [by_id.get(p, {}).get("parent", by_id.get(p, {}).get("name", "")) for p in df["payer_id"]]
    return df


def write_review(df: pd.DataFrame, path: Path | None = None) -> Path:
    """Borderline matches for a person to confirm. Copy accepted rows into
    config/payer_decisions.csv (payer_clean,payer_id) and re-run."""
    path = path or REVIEW_DIR / "payer_review.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["payer_clean", "suggested_id", "suggested_score", "runner_up_id", "runner_up_score",
            "method", "payer_id", "payer_raw"]
    out = df[df["method"].isin(["needs_review", "embedding"])][cols].sort_values("suggested_score", ascending=False)
    out.to_csv(path, index=False)
    return path


def threshold_check(df: pd.DataFrame, cfg: PayerConfig, embedder) -> pd.DataFrame:
    """How reliable would embeddings alone be? For every alias-matched name, match it against
    all *other* known spellings (leave-one-out) and record whether the embedding agrees."""
    known = df[df["method"] == "alias"].drop_duplicates("payer_clean").reset_index(drop=True)
    if len(known) < 5:
        return pd.DataFrame()
    names = [p["name"].upper() for p in cfg.payers] + known["payer_clean"].tolist()
    ids = [p["id"] for p in cfg.payers] + known["payer_id"].tolist()
    vecs = embedder.embed(names)
    n_can = len(cfg.payers)
    out = []
    for k in range(len(known)):
        mask = np.ones(len(names), dtype=bool)
        mask[n_can + k] = False
        same_text = [i for i, t in enumerate(names) if t == known.loc[k, "payer_clean"]]
        mask[same_text] = False
        best, score, _, s2 = best_matches(vecs[n_can + k:n_can + k + 1], vecs[mask],
                                          [i for i, keep in zip(ids, mask) if keep])[0]
        out.append({"payer_clean": known.loc[k, "payer_clean"], "true_id": known.loc[k, "payer_id"],
                    "embedding_id": best, "score": score, "margin": score - s2,
                    "correct": best == known.loc[k, "payer_id"]})
    return pd.DataFrame(out)
