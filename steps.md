# Hospital Price Compass: steps

**What will this procedure really cost me, at which hospital, with my insurance?**

US hospitals must publish their prices by law (Hospital Price Transparency rule, since 1 Jan 2021;
CMS's standard file format required since 1 Jul 2024). The files exist, but they're huge, inconsistent,
and unreadable for patients. This project downloads them for one metro area, cleans and checks them,
and shows a simple comparison: **procedure + insurer → hospitals ranked by price**, with a quality badge
on every number.

Two parts:

- **Part 1: Your manual steps.** Only you can do these. Click-by-click.
- **Part 2: Build plan.** What gets built, phase by phase. After each phase you get a report:
  what was built, what's next, and "XX% completed / YY% pending".

Status key: `[ ]` to do, `[x]` done. Tick them as you go.

---

# Part 1: Your manual steps

Do **M1 and M2 before Phase 0**. The others come later; the phase that needs them is shown.

### M1. NVIDIA NIM API key (needed for Phase 2): **done** (kept in the git-ignored `.env` file)
- [x] If you already made one for Mandi Price Compass, **reuse the same key**. Skip to M2.
- [ ] Otherwise: open https://build.nvidia.com → sign in → open any model page → **Get API Key**.
      The key starts with `nvapi-`. Keep it private; **don't paste it into a chat**.

### M2. Make a few choices (needed for Phase 0)
Reply with your answers, or say "use defaults". **Done: defaults chosen (2026-11-22).**
- [x] 1. **Metro area:** default **Phoenix, AZ** (a few dozen hospitals, a manageable start).
      Alternatives: Dallas–Fort Worth, Houston, Atlanta, Chicago.
- [x] 2. **Procedures (about 30):** default: common shoppable services from CMS's list, for example
      MRI of the knee or brain, CT of the abdomen, colonoscopy, mammogram, knee and hip replacement,
      C-section and vaginal delivery, cataract surgery, ER visit levels, basic lab panels.
- [x] 3. **Insurers to show first:** default: the 5 largest in the chosen metro
      (for Phoenix, roughly UnitedHealthcare, Blue Cross Blue Shield of Arizona, Aetna, Cigna, Humana),
      plus **cash price**.

**No API key is needed for the government data.** The CMS hospital list, Medicare rates and hospital
price files are all public.

### M3. GitHub setup (needed for Phase 8): **steps 1-5 done (2026-11-25)**
- [x] 1. https://github.com/new → name `hospital_price_compass` → **Public** → nothing ticked → **Create repository**.
- [x] 2. https://github.com/new → name `hospital-price-vault` → **Private** → nothing ticked → **Create**.
      (This holds the filtered raw extracts. The full hospital files are too big for git; only their
      download date, web address and fingerprint are stored.)
- [x] 3. Vault token: https://github.com/settings/personal-access-tokens/new
      - Name `hospital-vault-writer`, expiration 1 year (**put a renewal reminder in your calendar**)
      - **Only select repositories** → `hospital-price-vault` → **Contents: Read and write** → **Generate** → copy it.
- [x] 4. https://github.com/UdhvaPatel/hospital_price_compass/settings/secrets/actions → **New repository secret**:
      - `NVIDIA_API_KEY` = your key from M1
      - `VAULT_TOKEN` = the token from step 3
- [x] 5. https://github.com/UdhvaPatel/hospital_price_compass/settings/pages → **Source: GitHub Actions**.
- [ ] 6. Tell me. I'll push the code. (The vault fills itself on the first refresh run on GitHub, so the
      vault token never has to be on your computer.)
- [ ] 7. After the first push: GitHub → Actions → **refresh** → **Run workflow** (or wait for the 3rd of the month).

### M4. Spot-check prices by hand: **skipped (your decision, 2026-11-24)**
Replaced by an automated source audit (`python -m hpc.quality.audit`): 500 random final prices across
32 hospitals were re-read from the original files with an independent reader; all 500 matched (code,
insurer, plan, amount, gross, cash, description). It can't check a hospital's file against its website
or a real bill; the quality labels flag odd values instead. The optional sheet is still generated at
`data/quality/m4_spot_check.csv` if you ever want to do it.

### M6. Medicare outpatient and lab rates: **skipped for now (your decision, 2026-11-24)**
CMS puts these two files behind the AMA's CPT licence click-through, so only a person can accept it.
Without them, "multiple of Medicare" covers hospital stays (DRGs) only; outpatient prices are compared
with other Phoenix hospitals instead. To add them later: accept the licence and download the July 2026
OPPS Addendum B (https://www.cms.gov/medicare/payment/prospective-payment-systems/hospital-outpatient-pps/quarterly-addenda-updates/july-2026-addendum-b)
into `data/reference/medicare/opps_cy2026/` and the 26CLABQ3 lab fee schedule
(https://www.cms.gov/medicare/payment/fee-schedules/clinical-laboratory-fee-schedule-clfs/files/26clabq3)
into `data/reference/medicare/clfs_2026/`; the next run picks them up. Those folders are git-ignored.

### M5. Real-world check (after Phase 7, optional but valuable)
- [ ] 1. Show the site to one or two people with US health insurance who have had a scan or procedure recently.
- [ ] 2. Ask: "Does the price look like what you were billed? Is anything confusing?"
- [ ] 3. Tell me what they said.

---

# Part 2: Build plan

Each phase ends with working, tested code and a short report. The weights add up to 100%.

Tools: Python, **DuckDB** (handles multi-gigabyte files on an ordinary computer), pandas, pyarrow,
streaming JSON parsing (`ijson`), scikit-learn/statsmodels, plain HTML/CSS/JS site, GitHub Actions + Pages.

### Phase 0: Discovery and feasibility (5%) [needs M2]: **done**, see [`PLAN.md`](PLAN.md)
- [x] Build the hospital list for the metro from the **CMS Hospital General Information** dataset
      (data.cms.gov: name, address, CCN id, type, ownership, star rating).
- [x] For each hospital, find its price file through the required `cms-hpt.txt` file at the root of the
      hospital's website, falling back to the "Price Transparency" link in the page footer.
- [x] Record, per hospital: file found or not, format (CMS CSV "tall", CMS CSV "wide", CMS JSON, or older
      custom format), file size, last-updated date, and whether it can be downloaded without a browser.
- [x] Write `PLAN.md` with real numbers ("N of M hospitals publish a usable CMS-format file") and
      anything that changes this plan.

### Phase 1: Hospital registry and downloads (10%): **done**
- [x] `registry.py`: the metro's hospitals with CCN, location, health system and file location.
- [x] `download.py`: polite, resumable downloads with retries, size limits and a fingerprint (hash) of every file.
      Unchanged files are skipped on the next run.
- [x] Download log: web address, date, size, hash, HTTP status, and why a download failed.
- **Tests:** resume after interruption, skip unchanged files, handle redirects and zip files. (13 download tests, all passing)
- Result: 34 files downloaded (2.9 GB). Mayo Clinic Arizona (21.5 GB) is too big for disk; it is fingerprinted
  and filtered by streaming (`Downloader.stream`), which Phase 2 plugs its filter into.

### Phase 2: Parsing every format into one table (15%) [needs M1]: **done** (Mayo skipped for now)
- [x] Parsers for CMS CSV tall, CMS CSV wide (one column per payer/plan, turned into rows), CMS JSON
      (streamed, never loaded whole), and the common older custom layouts.
- [x] **Column mapping with help from a NIM LLM** for non-standard files: the LLM *proposes* which column
      is the code, description, payer or price. Rules then *check* the proposal (codes look like CPT/HCPCS/DRG,
      prices are numbers). Accepted mappings are saved per hospital so the LLM is asked only once.
- [x] One standard table: `hospital, code, code_type, description, setting (inpatient/outpatient),
      payer, plan, rate_type (negotiated $ / % of charges / per diem / case rate), amount,
      gross_charge, cash_price, min, max, file_date`.
- [x] Keep only the chosen procedures (plus their related codes), so extracts stay small enough for the vault.
- **Tests:** a small sample file of each format, with known correct output. (15 parser tests, all passing)
- Result: 94,003 price rows for 34 procedures from 33 hospitals; extracts total 2.3 MB.
  The NIM mapping fallback is built and tested but wasn't needed: every file is CMS v3.
  Mayo Clinic Arizona (21.5 GB) was skipped at your request; the streaming parser is tested and ready
  (`python -m hpc.parse.run --ccn 030103`, about 30-45 min on this connection).

### Phase 3: Normalising codes, payers and plans (15%): **done**
- [x] **Procedure matching:** map CPT/HCPCS/DRG codes to the chosen procedures. Where a row has only a text
      description, use **NIM embeddings** to match it, and send borderline matches to a review file.
- [x] **Payer and plan names with NIM embeddings:** "BCBS AZ PPO", "Blue Cross Blue Shield of Arizona - PPO",
      "BCBSAZ" → one payer, one plan type (PPO / HMO / Medicare Advantage / Medicaid managed care).
      Matching uses high-confidence auto-accept, a review file for borderline cases, and a cache.
- [x] **Rate types:** convert "% of charges" into dollars using the gross charge, keep per-diem rates separate,
      and never mix inpatient and outpatient prices.
- [x] **Package vs line item:** flag when one hospital's price covers the whole episode and another's is
      only the facility part, so they aren't compared as if they were the same thing.
- [x] Remove duplicates (the same code, payer and plan listed many times with modifiers).
- Result: 463 raw insurer names → 57 insurers; 46,142 canonical prices, 32,544 directly comparable,
  from 34 hospitals. 6,801 percentage rates converted to dollars. 48 Phase 3 tests, all passing.
- **Your review (optional, 5 minutes):** `data/review/payer_review.csv` (2 names) and
  `data/review/description_review.csv`. Accept an insurer match by adding a line to
  `config/payer_decisions.csv` (`payer_clean,payer_id,note`) and re-running `python -m hpc.normalize.run`.

### Phase 4: Data quality and compliance score (10%): **done** (M4 replaced by an automated audit)
Every price row is labelled `good` / `suspect` / `rejected` with a reason:
- [x] **Placeholder values:** $0, $0.01, $9,999,999, or the same amount for every code.
- [x] **Impossible values:** negotiated rate above the gross charge, cash price above the gross charge,
      min > max, or a price that is a tiny or huge multiple of the Medicare rate.
- [x] **Algorithm-only rows:** "see contract" or a formula with no dollar amount (counted, not priced).
- [x] **Hospital-level score:** file found, CMS format used, required fields present, file freshness,
      share of chosen procedures with a usable price, and share of rows rejected.
- [x] A data-quality report for the website: who publishes clean data and who doesn't, and why.
- **Output:** quality report plus the M4 sample sheet for you.
- Result: of 32,360 comparable insurer prices, 29,852 good, 2,180 suspect, 328 rejected. Grades: 16 A, 16 B,
  2 D, 5 F, Mayo not scored. (Updated in Phase 6: 30,110 good, 1,922 suspect, 328 rejected; see PLAN.md.) Medicare baseline: inpatient automatic (FY 2026 IPPS); outpatient needs M6.
  16 Phase 4 tests, all passing. Source audit: 500/500 sampled prices match the original files (10 audit tests).

### Phase 5: Statistics (15%): **done**
- [x] **Price spread:** for each procedure, the range across hospitals (median, 10th–90th percentile,
      max ÷ min) for each insurer and for cash.
- [x] **Multiple of Medicare:** each negotiated price ÷ the Medicare rate for the same service
      (OPPS rates for outpatient, DRG weights for inpatient, adjusted for the local wage index).
      This puts prices on a common scale.
- [x] **Insurer index:** which insurer negotiates the lowest prices across all procedures at the same
      hospitals (a fixed-effects comparison, so a cheap hospital doesn't make its insurers look good).
- [x] **Cash vs insured:** how often the cash price is *lower* than the insured negotiated price.
- [x] **What explains price differences:** a model of price (as a multiple of Medicare) using hospital
      features (system membership, size, ownership, teaching status, star rating, market concentration).
      It's tested on held-out hospitals, and only the factors that survive that test are shown.
- [x] **Change over time:** compare file versions as hospitals update them (which prices went up or down).
- **Tests:** a hand-computed example for each statistic. (11 tests, all passing)
- Result: see PLAN.md, "Phase 5 findings". Multiple of Medicare covers hospital stays only (M6 skipped);
  outpatient prices use a Phoenix market index instead. Change over time: baseline snapshot saved.

### Phase 6: Language layer with NIM (10%): **done**
- [x] For each procedure and insurer, a 2–3 sentence plain-English summary
      ("For a knee MRI with Aetna PPO, prices at the hospitals with usable data range from $X to $Y…").
- [x] **Number guard:** the LLM receives only computed numbers; any summary that contains a number not in
      the input is rejected and replaced with a template sentence.
- [x] **Patient-safety wording:** always say that the final bill can differ (doctor's fees billed separately,
      deductible, coinsurance, complications), and that this is not medical or insurance advice.
- [x] Summaries are cached and regenerated only when the numbers change.
- Result: 203 summaries (34 procedures × the 5 default insurers + cash); all 203 written by the model and
  passing the number guard (template fallback ready). 30 Phase 6 tests, all passing.

### Phase 7: Website (15%): **done** (local; goes live in Phase 8)
- [x] **Compare:** pick a procedure + insurer/plan (or cash) → hospitals ranked by price, on a map,
      with distance from a ZIP code, quality badge, star rating, and the multiple of Medicare.
- [x] **Procedure page:** price distribution, insurer comparison, cash vs insured.
- [x] **Hospital page:** compliance score, file details, how its prices compare across procedures.
- [x] **Insurer page:** the insurer index and where that insurer pays more or less than others.
- [x] **Data quality page:** what was found, fixed or rejected, per hospital.
- [x] **About:** method, sources, limitations, "your bill may differ", not-advice notice.
- [x] Fast, mobile-friendly, accessible (keyboard and screen reader), clear "data as of" date on every page.
- Result: static site in `site/` (plain HTML/CSS/JS, no third-party scripts or map tiles), data from
  `python -m hpc.site.build`. Preview: `python -m http.server 8000 -d site` then http://localhost:8000.
  23 site tests, including every page rendered headless (loads, shows data date + safety notice, no overflow).

### Phase 8: Automation and deployment (5%) [needs M3]: **built and tested locally; first GitHub run pending**
- [x] GitHub Actions jobs:
      - `check` (weekly): look for changed price files (by fingerprint) and new hospitals.
      - `refresh` (monthly, and when `check` finds changes): download → parse → normalise → quality → stats → summaries → publish.
      - `tests` on every push.
- [x] Files are processed in streaming chunks so the job fits GitHub's free runner (disk and 6-hour limits).
      If a file is too big, the job skips it and reports it rather than failing.
- [x] Filtered extracts go to the private vault; only aggregated JSON is published.
- [x] If a hospital's file disappears or breaks, the site shows it and the job opens a GitHub issue.
- Built: `.github/workflows/` (tests, check, refresh, pages), `hpc/pipeline.py`, `hpc/vault.py`, `hpc/report.py`.
  The weekly check ran end to end locally (no false triggers). 7 Phase 8 tests.

### Phase 9 (optional): Grow
- [ ] Add more metro areas (Phoenix → Dallas → Houston …), as many as the free runner allows.
- [ ] Cross-check against insurers' own price files (the Transparency in Coverage rule). These are much
      bigger, so only for a few procedures.
- [ ] "Estimate my out-of-pocket": the user enters their deductible and coinsurance, and the site estimates what they'd pay.

---

## Progress

| Phase | Weight | Status |
|---|---|---|
| 0 Discovery | 5% | **done** (35 of 40 hospitals usable) |
| 1 Registry + downloads | 10% | **done** (34 files downloaded, Mayo streams) |
| 2 Parsing all formats | 15% | **done** (33 hospitals; Mayo skipped) |
| 3 Normalising codes and payers | 15% | **done** (57 insurers; 32,544 comparable prices) |
| 4 Data quality + compliance | 10% | **done** (500/500 prices verified against source files) |
| 5 Statistics | 15% | **done** |
| 6 Language layer | 10% | **done** (203 guarded summaries) |
| 7 Website | 15% | **done** (local preview; deploy in Phase 8) |
| 8 Automation | 5% | **built**; goes live with the first push + refresh run |
| **Total** | **100%** | **95% completed / 5% pending** |

**Next action:** approve the first push (M3 step 6), then run the **refresh** workflow once on GitHub (step 7).
