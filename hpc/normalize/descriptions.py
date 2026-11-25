"""Match description-only rows (no CPT/HCPCS/DRG code) to the chosen procedures.

1. Keyword gate: only procedures whose `keywords` regex matches the description are considered.
2. Sibling rules pick between close variants that embeddings can't tell apart reliably
   (ER level digits, with/without contrast, colonoscopy with biopsy / polyp removal).
3. NIM embeddings decide "this procedure" vs "reject": the description is compared with the
   procedure's names (name + aka) and with reject anchors (cardiac MRI, CT-guided biopsy, ...).
   Accepted only if the procedure beats every reject anchor and clears the calibrated threshold.

The threshold is calibrated on descriptions of rows whose CPT code tells us the right answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from hpc.config import load_toml

REJECT = "_reject"
REJECT_ANCHORS = [
    "cardiac MRI", "MRI guided biopsy", "MRI angiography", "CT guided biopsy", "CT guided drainage",
    "CT angiography", "fine needle aspiration with CT guidance", "breast biopsy with mammography guidance",
    "cardiac rehabilitation exercise with ECG monitoring", "ECG telemetry monitoring", "Holter monitor",
    "ambulance ECG transmission", "stress test", "echocardiogram", "anesthesia for colonoscopy",
    "pathology interpretation", "contrast agent", "surgical supply", "implant device", "drug injection",
    "room and board per day", "observation hours", "emergency room supplies", "trauma activation",
    "CT procedure time per hour", "lipid emulsion infusion", "ultrasound guidance for vascular access",
    "hernia mesh", "physician professional fee",
    "emergency department wound repair", "emergency department incision and drainage",
    "emergency department burn treatment", "emergency department foreign body removal",
    "emergency department nasal hemorrhage control", "emergency department tendon repair",
    "endoscopy suite time per 15 minutes",
]
# An ER *visit* description must not name a procedure performed in the ER
_ER_PROCEDURE = re.compile(r"I&D|\bRPR\b|REPAIR|BURN|\bHEM\b|\bREM\b|\bFB\b|PROCEDURE|TENDON|ENDO|"
                           r"\bASP\b|PUNCT|\bMIN\b|SUTURE|\bLAC\b|SPLINT|\bCAST\b|REDUCT|CRITICAL", re.I)


@dataclass
class DescriptionMatch:
    description: str
    procedure_id: str      # "" when rejected
    decision: str          # accepted / review / rejected
    score: float
    reject_score: float
    reason: str


def _level(desc: str) -> int | None:
    m = re.search(r"(?:LEVEL|LVL|LEV)\s*[-#:]?\s*(\d|V|IV|III|II|I)\b", desc, re.I)
    if not m:
        return None
    v = m.group(1).upper()
    return int(v) if v.isdigit() else {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}[v]


_CONTRAST_WORD = r"(?:CONTRAST|CONTRST|CONTR|CONT|CON|CM|DYE)"


def _contrast(desc: str) -> str:
    """'both' (without and with), 'without', 'with', or '' when the description doesn't say.
    Handles WO/W, W/WO, W/O & W/, WWO, W & WO, WITHOUT AND WITH, and contrast words such as
    CONTRAST / CONTR / CM (contrast media)."""
    d = re.sub(r"\s+", " ", desc.upper())
    both = re.search(
        r"\bWO ?/ ?W\b|\bW ?/ ?WO\b|\bW/O ?(&|AND|/) ?W/?\b|\bWO ?(&|AND) ?W\b|\bW ?(&|AND) ?W/?O\b|"
        r"\bWWO\b|\bW/WO\b|WITHOUT ?(&|AND|/) ?WITH|WITH ?(&|AND|/) ?WITHOUT", d)
    if both:
        return "both"
    if re.search(r"\bW/O\b|\bWO\b|WITHOUT|\bNO " + _CONTRAST_WORD + r"|NON-?CON", d):
        return "without"
    if re.search(r"\bW/? ?" + _CONTRAST_WORD + r"\b|\bWITH " + _CONTRAST_WORD + r"\b|\bW/C\b|\bWC\b|"
                 + r"\b" + _CONTRAST_WORD + r"\b", d):
        return "with"
    return ""


def sibling_rule(desc: str, candidates: list[str]) -> tuple[list[str], str]:
    """Narrow candidate procedures using rules; returns (remaining candidates, reason)."""
    c = list(candidates)
    d = desc.upper()
    if "lab_cbc" in c and re.search(r"W/?O ?DIFF|WO ?DIFF|WITHOUT DIFF|NO DIFF", d):
        c.remove("lab_cbc")        # CBC without differential is 85027, a different test
    if "upper_gi_endoscopy" in c and re.search(r"TUMOR|LESION|POLYP|ABLAT|DILAT|STENT|BLEED|FOREIGN", d):
        c.remove("upper_gi_endoscopy")    # tracked EGD is "with biopsy"; removal/dilation are other codes
    if "mammogram_screening" in c and re.search(r"\bDX\b|DIAG", d):
        c.remove("mammogram_screening")   # diagnostic, not screening
    er = [p for p in c if p.startswith("er_visit_level_")]
    if er and re.search(r"\bOB\b|OBSTET|L&D|LABOR", d):
        c = [p for p in c if p not in er]   # labour & delivery triage, not a regular ER visit
        if not c:
            return [], "OB triage visit, not a regular ER visit"
        er = []
    if er and _ER_PROCEDURE.search(d):
        c = [p for p in c if p not in er]
        if not c:
            return [], "procedure done in the ER, not an ER visit level"
        er = []
    if er:
        lvl = _level(d)
        keep = [p for p in er if lvl is not None and p == f"er_visit_level_{lvl}"]
        if lvl in (1, 2) and "er_visit_level_3" in er:
            keep = []      # levels 1-2 are only *related* codes; don't price them as level 3
        c = [p for p in c if p not in er] + keep
        if not keep:
            return [p for p in c if not p.startswith("er_visit")], f"ER level {lvl} not tracked" if lvl else "no ER level"
    pair = [p for p in c if p in ("ct_abdomen_pelvis", "ct_abdomen_pelvis_contrast")]
    if len(pair) == 2:
        con = _contrast(d)
        if con == "with":
            c.remove("ct_abdomen_pelvis")
        elif con == "without":
            c.remove("ct_abdomen_pelvis_contrast")
        else:
            c = [p for p in c if p not in pair]
    if "mri_brain" in c and _contrast(d) not in ("both",):
        c.remove("mri_brain")       # tracked MRI brain is "with and without contrast" only
    if "mri_knee" in c and _contrast(d) in ("with", "both"):
        c.remove("mri_knee")
    if "mri_lumbar_spine" in c and _contrast(d) in ("with", "both"):
        c.remove("mri_lumbar_spine")
    if "ct_head" in c and _contrast(d) in ("with", "both"):
        c.remove("ct_head")
    colo = [p for p in c if p.startswith("colonoscopy")]
    if colo:
        if re.search(r"POLYP|SNARE|LESION|REMOV", d):
            pick = "colonoscopy_polyp_removal"
        elif re.search(r"BIOPSY|\bBX\b", d):
            pick = "colonoscopy_biopsy"
        else:
            pick = "colonoscopy"
        c = [p for p in c if not p.startswith("colonoscopy") or p == pick]
    return c, ""


class DescriptionMatcher:
    def __init__(self, embedder, procedures: dict | None = None, threshold: float = 0.6,
                 review_floor: float = 0.45):
        cfg = procedures or load_toml("procedures.toml")
        self.procs = [p for p in cfg["procedure"] if p.get("keywords")]
        self.kw = {p["id"]: re.compile(p["keywords"], re.I) for p in self.procs}
        self.embedder = embedder
        self.threshold, self.review_floor = threshold, review_floor
        texts, labels = [], []
        for p in self.procs:
            for t in [p["name"], *p.get("aka", [])]:
                texts.append(t)
                labels.append(p["id"])
        texts += REJECT_ANCHORS
        labels += [REJECT] * len(REJECT_ANCHORS)
        self.anchor_labels = np.asarray(labels)
        self.anchor_vecs = embedder.embed(texts) if texts else None

    def candidates(self, desc: str) -> list[str]:
        return [pid for pid, rx in self.kw.items() if rx.search(desc or "")]

    def match(self, descriptions: list[str]) -> list[DescriptionMatch]:
        uniq = list(dict.fromkeys(d for d in descriptions if d))
        if not uniq:
            return []
        vecs = self.embedder.embed(uniq)
        sims = vecs @ self.anchor_vecs.T
        rej = sims[:, self.anchor_labels == REJECT].max(axis=1)
        out = []
        for i, d in enumerate(uniq):
            cands, reason = sibling_rule(d, self.candidates(d))
            if not cands:
                out.append(DescriptionMatch(d, "", "rejected", 0.0, float(rej[i]), reason or "no keyword match"))
                continue
            scores = {p: float(sims[i, self.anchor_labels == p].max()) for p in cands}
            best = max(scores, key=scores.get)
            s = scores[best]
            if s <= rej[i]:
                out.append(DescriptionMatch(d, "", "rejected", s, float(rej[i]), "closer to a reject anchor"))
            elif s >= self.threshold:
                out.append(DescriptionMatch(d, best, "accepted", s, float(rej[i]), ""))
            elif s >= self.review_floor:
                out.append(DescriptionMatch(d, best, "review", s, float(rej[i]), "below threshold"))
            else:
                out.append(DescriptionMatch(d, "", "rejected", s, float(rej[i]), "low similarity"))
        return out


def calibrate(matcher: DescriptionMatcher, coded: pd.DataFrame,
              thresholds=(0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75)) -> pd.DataFrame:
    """coded: rows with columns description, procedure_id (from real codes). For each threshold,
    how many descriptions would be accepted and how many of those get the right procedure."""
    df = coded[["description", "procedure_id"]].drop_duplicates()
    df = df[df["procedure_id"].isin(matcher.kw)]
    saved = matcher.threshold
    rows = []
    try:
        matcher.threshold = 0.0
        res = {m.description: m for m in matcher.match(df["description"].tolist())}
    finally:
        matcher.threshold = saved
    df = df.assign(pred=[res[d].procedure_id if d in res else "" for d in df["description"]],
                   score=[res[d].score if d in res else 0 for d in df["description"]],
                   gated=[bool(matcher.candidates(d)) for d in df["description"]])
    for t in thresholds:
        acc = df[(df["pred"] != "") & (df["score"] >= t)]
        rows.append({"threshold": t, "descriptions": len(df), "keyword_gate_pass": int(df["gated"].sum()),
                     "accepted": len(acc), "correct": int((acc["pred"] == acc["procedure_id"]).sum()),
                     "precision": round(float((acc["pred"] == acc["procedure_id"]).mean()), 3) if len(acc) else None})
    return pd.DataFrame(rows), df
