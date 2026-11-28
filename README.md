# Hospital Price Compass

**What will this procedure really cost me, at which hospital, with my insurance?**

US hospitals have had to publish their prices since 2021, in a standard CMS format since July 2024.
The files are public but huge and inconsistent. This project collects them for one metro area,
cleans and normalises them, scores how trustworthy each hospital's data is, and shows patients a
simple comparison: **procedure + insurer (or cash) → hospitals ranked by price**.

Educational information, **not medical, insurance or financial advice**. A real bill can differ
(doctor's fees billed separately, deductible, coinsurance, complications).

> Status: **Live**, redesigned 2026-11-26 (dark & modern, animated). **Phase 8 built**. Pipeline, website and automation are done and tested; the site goes live on
> GitHub Pages after the first push and refresh run (see [`PLAN.md`](PLAN.md), [`steps.md`](steps.md)).

```bash
pip install -r requirements.txt
python -m hpc.discovery.run   # Phase 0: find each hospital's price file  -> data/phase0/
python -m hpc.registry        # hospital registry with coordinates        -> data/registry/
python -m hpc.download        # download changed files (skips unchanged)  -> data/raw/, data/logs/
python -m hpc.parse.run --skip-streaming  # parse into one table       -> data/extracts/
python -m hpc.normalize.run   # insurers, plans, rates, duplicates     -> data/normalized/, data/review/
python -m hpc.quality.run     # quality labels, compliance scores       -> data/quality/
python -m hpc.quality.audit   # re-check 500 prices against the source files
python -m hpc.stats.run       # statistics for the site                 -> data/stats/
python -m hpc.language.summaries  # plain-English summaries (NIM + guard) -> data/summaries/
python -m hpc.site.build      # JSON for the website                    -> site/data/
python -m http.server 8000 -d site   # preview at http://localhost:8000
pytest
```

## How it works

```
CMS hospital list ──► find each hospital's price file (cms-hpt.txt) ──► download (fingerprinted)
        │
        ▼
parse every format (CMS CSV tall/wide, JSON, older layouts) into one table
(NVIDIA NIM LLM proposes column mappings for odd files; rules verify them)
        │
        ▼
normalise procedure codes, payer and plan names (NIM embeddings), rate types
        │
        ▼
quality label on every row + compliance score for every hospital
        │
        ▼
statistics: price spread, multiple of Medicare, insurer index, cash vs insured, what drives price
        │
        ▼
NIM LLM writes plain-English summaries (number guard: it can't invent figures)
        │
        ▼
static website on GitHub Pages, refreshed monthly by GitHub Actions
```

## Data sources (all public, no key needed)

| Source | Use |
|---|---|
| Hospital machine-readable price files (Hospital Price Transparency rule) | Negotiated rates, cash prices, gross charges |
| CMS Hospital General Information (data.cms.gov) | Hospital list, CCN, ownership, type, star rating |
| CMS Medicare rates (OPPS Addendum B, IPPS DRG weights, wage index) | "Multiple of Medicare" baseline |
| CMS shoppable services list | Choosing the procedures to cover |

## NVIDIA NIM models (build.nvidia.com)

| Job | Model type |
|---|---|
| Match payer/plan names and procedure descriptions | Text embedding (+ reranker) |
| Propose column mappings for non-standard files | Instruct LLM (Llama / Nemotron) |
| Plain-English summaries | Instruct LLM, with a number guard |

The models run inside the GitHub Actions jobs, so the API key never reaches the browser.
Locally the key goes in a `.env` file (git-ignored): `NVIDIA_API_KEY=nvapi-...`. **Never put it in `config/`**,
which is published with the code.

## Automation (GitHub Actions)

| Workflow | When | What |
|---|---|---|
| `tests` | every push | the full test suite, including every page rendered in headless Chrome |
| `check` | Mondays | new hospitals? changed or broken price files (by fingerprint)? starts `refresh` if so |
| `refresh` | the 3rd of each month, after `check` finds changes, or by hand | the whole pipeline, then saves state to the private vault, publishes the site, opens an issue on problems |
| `pages` | push to `main` touching `site/` | publish the site |

Secrets (repository settings → Secrets → Actions): `NVIDIA_API_KEY`, `VAULT_TOKEN`. Run it all locally with
`python -m hpc.pipeline refresh` (or `check`).

