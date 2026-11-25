"""Plain-English summaries with NVIDIA NIM, guarded and cached.

For each fact sheet:
  1. cached? (same facts, same prompt version, same model) -> reuse, no API call
  2. ask the model; check with the number guard
  3. if rejected, ask once more, telling it exactly what was wrong
  4. still rejected (or the API fails) -> the deterministic template
The patient-safety notice is never written by the model: it is attached separately.

Run:  python -m hpc.language.summaries [--no-llm] [--limit N] [--workers 3]
Out:  data/summaries/summaries.json (+ .csv), data/cache/summaries.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import pandas as pd

from hpc.config import DATA_DIR
from hpc.language.facts import FactSheet, build_fact_sheets
from hpc.language.guard import SAFETY_NOTICE, check, template

PROMPT_VERSION = "3"
CACHE_FILE = DATA_DIR / "cache" / "summaries.json"
OUT_DIR = DATA_DIR / "summaries"

SYSTEM = """You write short, neutral, plain-English summaries of hospital price data for patients.
Rules:
- Use ONLY the facts given. Copy every number exactly as written in the facts, including $ and %.
- Never round, estimate, add up, subtract or calculate a new number. Write numbers as digits.
- No advice, no recommendations, no words like best, worst, cheapest option, save, choose or pick.
- Say nothing about quality of care, and nothing about what the reader will pay.
- Start by naming the procedure and who pays (the insurer and plan, or "cash price").
- Present tense. Everyday words: say "the highest price is 4.5 times the lowest", not "ratio".
- The full range is lowest to highest price; "most prices fall between" is a narrower middle range. Don't mix them up.
- 2 or 3 sentences, under 80 words, plain prose: no lists, no markdown, no headings.
- Do not add a disclaimer; one is added separately."""

USER = """Facts (JSON):
{facts}

Write the summary."""


@dataclass
class Summary:
    id: str
    procedure_id: str
    view: str
    text: str
    source: str              # llm / llm_retry / template / cached:<source>
    problems: list
    facts_digest: str
    safety_notice: str = SAFETY_NOTICE


def cache_key(sheet: FactSheet, model: str) -> str:
    return hashlib.sha256(f"{sheet.digest()}|{PROMPT_VERSION}|{model}".encode()).hexdigest()[:20]


class SummaryCache:
    def __init__(self, path=CACHE_FILE):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.lock = threading.Lock()

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value: dict) -> None:
        with self.lock:
            self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")


def summarise(sheet: FactSheet, client, cache: SummaryCache | None) -> Summary:
    model = getattr(client, "chat_model", "none") if client else "none"
    key = cache_key(sheet, model)
    hit = cache.get(key) if cache is not None and client is not None else None
    if hit and check(hit["text"], sheet.facts).ok:   # cached text must still pass the *current* guard
        return Summary(sheet.id, sheet.procedure_id, sheet.view, hit["text"], f"cached:{hit['source']}",
                       hit.get("problems", []), sheet.digest())
    problems: list = []
    text, source = "", "template"
    if client is not None:
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": USER.format(facts=json.dumps(sheet.facts, indent=1))}]
        for attempt in range(2):
            try:
                draft = client.chat_text(messages)
            except Exception as exc:                 # API trouble -> template, never a crash
                problems.append(f"api error: {str(exc)[:120]}")
                break
            res = check(draft, sheet.facts)
            if res.ok:
                text, source = draft, "llm" if attempt == 0 else "llm_retry"
                break
            problems.append({"attempt": attempt + 1, "draft": draft, "problems": res.problems})
            messages += [{"role": "assistant", "content": draft},
                         {"role": "user", "content": "That breaks the rules: " + "; ".join(res.problems)
                          + ". Rewrite it using only numbers copied exactly from the facts."}]
    if not text:
        text, source = template(sheet.facts), "template"
    api_failed = any(isinstance(p, str) and p.startswith("api error") for p in problems)
    if cache is not None and client is not None and not api_failed:   # retry API failures next run
        cache.put(key, {"text": text, "source": source, "problems": problems, "model": model,
                        "prompt_version": PROMPT_VERSION,
                        "created": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return Summary(sheet.id, sheet.procedure_id, sheet.view, text, source, problems, sheet.digest())


def run(sheets: list[FactSheet], client, cache: SummaryCache | None, workers: int = 3,
        progress_every: int = 20, progress=None) -> list[Summary]:
    """Summarise every sheet. Every `progress_every` summaries: a progress line (if `progress` is
    given, e.g. print) and a cache save, so an interrupted run keeps what it already paid for."""
    done = {"n": 0, "llm": 0}
    lock = threading.Lock()

    def one(s: FactSheet) -> Summary:
        r = summarise(s, client, cache)
        with lock:
            done["n"] += 1
            done["llm"] += r.source.replace("cached:", "").startswith("llm")
            if done["n"] % progress_every == 0 or done["n"] == len(sheets):
                if cache is not None:
                    cache.save()
                if progress:
                    progress(f"    ... {done['n']}/{len(sheets)} summaries ({done['llm']} written by the model, "
                             f"{done['n'] - done['llm']} template)")
        return r

    if client is None or workers <= 1:
        out = [one(s) for s in sheets]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            out = list(ex.map(one, sheets))
    if cache is not None:
        cache.save()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-llm", action="store_true", help="templates only")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    client = None
    if not args.no_llm:
        from hpc.parse.nim import NimClient
        client = NimClient()
    sheets = build_fact_sheets()
    if args.limit:
        sheets = sheets[:args.limit]
    results = run(sheets, client, SummaryCache(), args.workers, progress=lambda s: print(s, flush=True))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = [asdict(r) for r in results]
    (OUT_DIR / "summaries.json").write_text(json.dumps(records, indent=1), encoding="utf-8")
    df = pd.DataFrame(records)
    df.assign(problems=df["problems"].map(lambda p: json.dumps(p)[:500])).to_csv(OUT_DIR / "summaries.csv", index=False)
    print(df["source"].value_counts().to_string())


if __name__ == "__main__":
    main()
