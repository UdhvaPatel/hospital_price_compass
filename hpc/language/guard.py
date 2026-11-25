"""Number guard and safety rules for generated summaries.

A summary is rejected (and replaced by the template) if it:
  - contains a dollar amount, percentage or multiple that isn't exactly one of the facts
    (typed: "$350" must match a dollar fact, "54%" a percentage fact, "4.5 times" a multiple);
  - contains any other number that isn't in the facts;
  - writes numbers in words or as vague quantities ("twice", "half", "a third", "$39k", "about 40");
  - gives advice or rankings ("you should", "best", "recommend", "save"), claims certainty about a
    bill ("you will pay"), talks about quality of care, or makes coverage claims ("covers",
    "in-network": the data shows prices, not what a plan covers);
  - is empty, too long, or not plain prose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(?:%|percent\b)", re.I)
TIMES = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(?:x\b|×|times\b|-fold)", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w])")
ABBREV = re.compile(r"\$?\d[\d,.]*\s?(?:k|K|M|bn|thousand|million|billion)\b")
NUMBER_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
    r"million|billion|half|halves|twice|double|doubled|triple|tripled|quadruple|dozen|third|quarter|"
    r"several-fold|tenfold|majority|minority)\b", re.I)
VAGUE = re.compile(r"\b(about|around|roughly|approximately|nearly|almost|over|under|more than|less than|up to)\s+\$?\d", re.I)
BANNED = re.compile(
    r"\b(you should|should you|we recommend|recommend|recommended|best|worst|cheapest option|top choice|"
    r"bargain|deal|save money|you('ll| will) (pay|owe|be charged)|guarantee[ds]?|quality|safer|better care|"
    r"worse care|choose|pick|cover|covers|covered|coverage|in-network|out-of-network)\b", re.I)
MAX_WORDS = 90
TIMES_FACTS = ("highest_price_is_this_many_times_the_lowest", "median_price_is_this_many_times_the_medicare_rate")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class Allowed:
    money: set[float] = field(default_factory=set)
    percent: set[float] = field(default_factory=set)
    times: set[float] = field(default_factory=set)
    plain: set[float] = field(default_factory=set)   # counts, codes, dates and every number above

    @classmethod
    def from_facts(cls, facts: dict) -> "Allowed":
        a = cls()
        for key, v in facts.items():
            v = str(v)
            for m in MONEY.findall(v):
                a.money.add(_num(m))
            for m in PERCENT.findall(v):
                a.percent.add(_num(m))
            if key in TIMES_FACTS:
                a.times.add(_num(v))
            for m in re.findall(r"\d[\d,]*(?:\.\d+)?", v):
                a.plain.add(_num(m))
            for part in re.findall(r"\d+", v):          # e.g. the parts of a date
                a.plain.add(float(part))
        return a


@dataclass
class GuardResult:
    ok: bool
    problems: list[str]


def check(text: str, facts: dict) -> GuardResult:
    problems = []
    t = (text or "").strip()
    if not t:
        return GuardResult(False, ["empty"])
    if len(t.split()) > MAX_WORDS:
        problems.append(f"too long ({len(t.split())} words)")
    if re.search(r"[#*_`|<>]|^\s*[-•]", t, re.M):
        problems.append("not plain prose (markdown or list)")
    a = Allowed.from_facts(facts)
    used = set()
    for m in MONEY.finditer(t):
        if _num(m.group(1)) not in a.money:
            problems.append(f"dollar amount not in facts: ${m.group(1)}")
        used.add(m.span(1))
    for m in PERCENT.finditer(t):
        if _num(m.group(1)) not in a.percent:
            problems.append(f"percentage not in facts: {m.group(1)}%")
        used.add(m.span(1))
    for m in TIMES.finditer(t):
        if _num(m.group(1)) not in a.times:
            problems.append(f"multiple not in facts: {m.group(1)}x")
        used.add(m.span(1))
    for m in NUMBER.finditer(t):
        if m.span(1) in used or any(s <= m.start() < e for s, e in used):
            continue
        if _num(m.group(1)) not in a.plain:
            problems.append(f"number not in facts: {m.group(1)}")
    if ABBREV.search(t):
        problems.append(f"abbreviated amount: {ABBREV.search(t).group(0)}")
    for w in NUMBER_WORDS.findall(t):
        problems.append(f"number in words: {w}")
    if VAGUE.search(t):
        problems.append(f"vague quantity: {VAGUE.search(t).group(0)}")
    for b in BANNED.findall(t):
        problems.append(f"advice / ranking / quality language: {b if isinstance(b, str) else b[0]}")
    # it must say who pays: the insurer's name, or that it's the cash price
    who = "cash" if facts.get("plan") == "cash" else str(facts.get("who_pays", ""))
    if who and who.lower() not in t.lower():
        problems.append(f"doesn't name who pays ({who})")
    return GuardResult(not problems, problems)


SAFETY_NOTICE = (
    "These are prices hospitals publish for their own (facility) part of the service. Your bill can differ: "
    "doctors' fees are often billed separately, and your deductible, coinsurance and any complications change "
    "what you pay. Educational information only, not medical, insurance or financial advice."
)


def template(f: dict) -> str:
    """Deterministic fallback, built only from the facts (passes the guard by construction)."""
    proc = f["procedure"]
    proc = proc if proc[:2].isupper() else proc[0].lower() + proc[1:]   # keep "MRI", "CT", "ECG"
    lead = (f"For {proc} with {f['who_pays']} {f['plan']}, published prices" if f["plan"] != "cash"
            else f"For {proc}, published cash (self-pay) prices")
    s = [f"{lead} at the {f['hospitals_with_usable_data']} Phoenix-area hospitals with usable data "
         f"({f['health_systems']} health systems) range from {f['lowest_price']} at {f['lowest_price_where']} "
         f"to {f['highest_price']} at {f['highest_price_where']}; the median is {f['median_price']}."]
    if "most_prices_fall_between_low" in f and f["most_prices_fall_between_low"] != f["most_prices_fall_between_high"]:
        s.append(f"Most fall between {f['most_prices_fall_between_low']} and {f['most_prices_fall_between_high']}.")
    if "median_price_is_this_many_times_the_medicare_rate" in f:
        s.append(f"The median is {f['median_price_is_this_many_times_the_medicare_rate']} times the Medicare base "
                 f"rate for this stay.")
    if "share_where_cash_is_below_typical_insured_price" in f:
        share, n = f["share_where_cash_is_below_typical_insured_price"], f["hospitals_compared_cash_vs_insured"]
        if share == "0%":
            s.append(f"At none of the {n} hospitals compared is the cash price below the typical commercial "
                     f"insured price.")
        elif share == "100%":
            s.append(f"At all {n} hospitals compared, the cash price is below the typical commercial insured price.")
        else:
            s.append(f"At {share} of the {n} hospitals compared, the cash price is below the typical commercial "
                     f"insured price.")
    return " ".join(s)
