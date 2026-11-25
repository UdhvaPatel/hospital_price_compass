"""Phase 6 tests: number guard, template, retry, cache and API failure handling (fake model)."""
import pytest

from hpc.language.facts import FactSheet, money, where
from hpc.language.guard import SAFETY_NOTICE, check, template
from hpc.language.summaries import SummaryCache, run, summarise

FACTS = {
    "procedure": "MRI of the knee (no contrast)",
    "who_pays": "Aetna",
    "plan": "commercial (employer or individual) plans",
    "hospitals_with_usable_data": "24",
    "health_systems": "5",
    "median_price": "$749",
    "lowest_price": "$350",
    "lowest_price_where": "7 HonorHealth hospitals",
    "highest_price": "$1,587",
    "highest_price_where": "St Joseph's Hospital",
    "most_prices_fall_between_low": "$350",
    "most_prices_fall_between_high": "$749",
    "highest_price_is_this_many_times_the_lowest": "4.5",
}
GOOD = ("For an MRI of the knee with Aetna commercial plans, prices at 24 hospitals range from $350 to $1,587, "
        "and the median is $749. The highest price is 4.5 times the lowest.")


def sheet(facts=FACTS, id_="mri_knee__aetna"):
    return FactSheet(id=id_, procedure_id="mri_knee", view="aetna", facts=dict(facts))


# ------------------------------------------------------------------ formatting

def test_money_format():
    assert (money(749.4), money(1587.0), money(9.714), money(38781.2)) == ("$749", "$1,587", "$9.71", "$38,781")


def test_where_names_one_hospital_or_counts_ties():
    import pandas as pd
    pts = pd.DataFrame({"ccn": ["a", "b", "c", "d"], "price": [350.0, 350.0, 350.0, 900.0]})
    names = {"a": "A", "b": "B", "c": "C", "d": "D Hospital"}
    assert where(pts, 900.0, names, {}) == "D Hospital"
    assert where(pts, 350.0, names, {"a": "HonorHealth", "b": "HonorHealth", "c": "HonorHealth"}) == "3 HonorHealth hospitals"
    assert where(pts, 350.0, names, {"a": "HonorHealth", "b": "Banner Health"}) == "3 hospitals"


# ------------------------------------------------------------------ guard

def test_guard_accepts_faithful_text():
    assert check(GOOD, FACTS).ok


@pytest.mark.parametrize("text,problem", [
    (GOOD.replace("$1,587", "$1,600"), "dollar amount not in facts"),          # rounded
    (GOOD.replace("$749", "about $750"), "vague quantity"),
    (GOOD + " Some hospitals charge 25% more.", "percentage not in facts"),     # invented
    (GOOD.replace("4.5 times", "5 times"), "multiple not in facts"),
    (GOOD.replace("24 hospitals", "26 hospitals"), "number not in facts"),
    (GOOD.replace("24 hospitals", "twenty-four hospitals"), "number in words"),
    (GOOD + " That's twice the median.", "number in words"),
    (GOOD.replace("$1,587", "$1.6k"), "abbreviated amount"),
    (GOOD + " You should choose the lowest-priced hospital.", "advice"),
    (GOOD + " You will pay $749.", "advice"),
    (GOOD + " Quality of care is similar.", "quality"),
    (GOOD.replace("Aetna ", ""), "doesn't name who pays"),
    ("- " + GOOD, "not plain prose"),
    (GOOD + " " + " ".join(["word"] * 80), "too long"),
    ("", "empty"),
])
def test_guard_rejects(text, problem):
    r = check(text, FACTS)
    assert not r.ok and any(problem in p for p in r.problems), r.problems


def test_template_uses_only_facts_and_passes_guard():
    t = template(FACTS)
    assert check(t, FACTS).ok
    assert "$350 at 7 HonorHealth hospitals" in t and "MRI of the knee" in t
    cash = {**FACTS, "who_pays": "the cash (self-pay) price", "plan": "cash",
            "hospitals_compared_cash_vs_insured": "29", "share_where_cash_is_below_typical_insured_price": "0%"}
    tc = template(cash)
    assert check(tc, cash).ok and "none of the 29 hospitals" in tc


def test_safety_notice_mentions_the_key_caveats():
    for phrase in ("doctors' fees", "deductible", "coinsurance", "complications", "not medical, insurance or financial advice"):
        assert phrase in SAFETY_NOTICE


# ------------------------------------------------------------------ summarise: retry, fallback, cache

class FakeClient:
    chat_model = "fake"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def chat_text(self, messages, **kw):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_good_first_draft_is_used():
    s = summarise(sheet(), FakeClient([GOOD]), None)
    assert (s.source, s.text, s.safety_notice) == ("llm", GOOD, SAFETY_NOTICE)


def test_rejected_draft_gets_one_retry_with_feedback():
    c = FakeClient([GOOD.replace("$749", "$750"), GOOD])
    s = summarise(sheet(), c, None)
    assert s.source == "llm_retry" and c.calls == 2
    assert "$750" in s.problems[0]["draft"]


def test_two_bad_drafts_fall_back_to_template():
    c = FakeClient([GOOD.replace("$749", "$750"), GOOD.replace("$749", "$760")])
    s = summarise(sheet(), c, None)
    assert s.source == "template" and s.text == template(FACTS) and len(s.problems) == 2


def test_api_error_falls_back_to_template():
    s = summarise(sheet(), FakeClient([RuntimeError("NIM HTTP 503")]), None)
    assert s.source == "template" and "api error" in s.problems[0]


def test_cache_reused_until_numbers_change(tmp_path):
    cache = SummaryCache(tmp_path / "c.json")
    c = FakeClient([GOOD])
    run([sheet()], c, cache, workers=1)
    again = run([sheet()], FakeClient([]), SummaryCache(tmp_path / "c.json"), workers=1)   # no replies needed
    assert again[0].source == "cached:llm" and again[0].text == GOOD
    changed = dict(FACTS, median_price="$760")
    c2 = FakeClient([GOOD.replace("$749", "$760")])
    out = run([sheet(changed)], c2, SummaryCache(tmp_path / "c.json"), workers=1)
    assert c2.calls == 1 and out[0].source == "llm"                                     # new numbers -> regenerated


def test_no_llm_mode_uses_templates():
    out = run([sheet()], None, None)
    assert out[0].source == "template"


def test_progress_lines_and_incremental_cache_saves(tmp_path):
    lines = []
    sheets = [sheet(dict(FACTS, median_price=f"${700 + i}"), id_=f"s{i}") for i in range(5)]
    replies = [GOOD.replace("$749", f"${700 + i}") for i in range(5)]
    cache = SummaryCache(tmp_path / "c.json")
    run(sheets, FakeClient(replies), cache, workers=1, progress_every=2, progress=lines.append)
    assert [l.split("summaries")[0].strip() for l in lines] == ["... 2/5", "... 4/5", "... 5/5"]
    assert "5 written by the model, 0 template" in lines[-1]
    assert len(SummaryCache(tmp_path / "c.json").data) == 5


def test_api_failures_are_not_cached(tmp_path):
    cache = SummaryCache(tmp_path / "c.json")
    run([sheet()], FakeClient([RuntimeError("ran out of tokens")]), cache, workers=1)
    c2 = FakeClient([GOOD])
    out = run([sheet()], c2, SummaryCache(tmp_path / "c.json"), workers=1)
    assert c2.calls == 1 and out[0].source == "llm"          # retried, not served a cached template


def test_coverage_claims_rejected():
    r = check(GOOD.replace("For an MRI", "Aetna commercial plans cover an MRI. For an MRI"), FACTS)
    assert not r.ok and any("cover" in p for p in r.problems)


def test_cached_text_rechecked_against_current_guard(tmp_path):
    cache = SummaryCache(tmp_path / "c.json")
    run([sheet()], FakeClient([GOOD]), cache, workers=1)
    key = next(iter(cache.data))
    cache.data[key]["text"] = GOOD + " Aetna covers it."      # imagine a rule added after caching
    cache.save()
    c2 = FakeClient([GOOD])
    out = run([sheet()], c2, SummaryCache(tmp_path / "c.json"), workers=1)
    assert c2.calls == 1 and out[0].source == "llm"            # regenerated, not served
