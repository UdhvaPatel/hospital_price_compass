# Phase 0 findings: Phoenix metro feasibility

Run date: **2026-11-22**. Regenerate with `python -m hpc.discovery.run`.
Raw results: [`data/phase0/discovery.csv`](data/phase0/discovery.csv) (one row per hospital),
[`data/phase0/summary.json`](data/phase0/summary.json), and copies of each `cms-hpt.txt` in
[`data/phase0/hpt_files/`](data/phase0/hpt_files/).

## Headline

**35 of the 40 in-scope Phoenix-area hospitals publish a usable CMS-format price file that a
program can download.** All 35 use CMS template **v3.0.0**, and every one was updated within the last 11 months.
The project is feasible as planned.

## Scope

Phoenix-Mesa-Chandler metro = **Maricopa + Pinal counties**. The CMS Hospital General Information
list (updated 2026-10-26) has **58 hospitals** there:

| Group | Count | Treatment |
|---|---|---|
| Acute care, critical access, children's | 40 | **In scope** |
| Psychiatric | 15 | Out of scope (they don't offer the chosen procedures) |
| Federal (Phoenix VA, Phoenix Indian Medical Center, Hu Hu Kam) | 3 | Out of scope (exempt from the price transparency rule) |

## What we found for the 40 in-scope hospitals

| Result | Hospitals | Who |
|---|---|---|
| `cms-hpt.txt` works → CMS-format file | 18 | HonorHealth (7), Dignity/CommonSpirit (5), Mayo, Valleywise, Phoenix Children's, CORE Institute, Exceptional Maricopa, Wickenburg |
| `cms-hpt.txt` **refuses automated clients** (HTTP 403), but the price file itself downloads fine | 17 | Banner (13), Abrazo (4). File addresses recorded by hand in `config/hospital_sites.toml` |
| Blocked: index file **and** price file both refuse automated clients | 1 | City of Hope Phoenix (Goodyear) |
| Blocked: `cms-hpt.txt` refuses automated clients, price file address unknown | 1 | Arizona Spine & Joint Hospital |
| `cms-hpt.txt` doesn't list this hospital | 2 | HonorHealth Tempe (missing from HonorHealth's list, probably closed or converted), East Valley ER & Hospital (its `cms-hpt.txt` lists only a sister hospital, *Oklahoma* ER & Hospital) |
| Website not identified | 1 | Arizona Orthopedic & Surgical Specialty Hospital |

**File formats (35 usable):** CMS CSV tall: 25 · CMS JSON: 9 (all Dignity and Abrazo) ·
CMS CSV wide: 1 (Wickenburg). Phoenix Children's CSV comes zipped. **No old or custom layouts.**

**Size:** 36 distinct files, about **24 GB** in total. **Mayo Clinic Arizona alone is 21.5 GB** (one CSV).
The other 35 total about 2.6 GB, and the biggest of them is Valleywise at 540 MB. Some servers report
compressed sizes: HonorHealth's file shows about 3 MB but contains 317,000 rows.

**Content check (HonorHealth John C. Lincoln, sampled):** it contains MRI knee (CPT 73721),
colonoscopy (45378) and DRG 470 (hip or knee replacement). All 5 default insurers appear
(Aetna, BCBS, Cigna, Humana, UnitedHealthCare). Payer names are written inconsistently
("BCBS" / "Bcbs", "TriWest" / "Triwest"), as the plan expected.

## What changes in the plan

1. **Phase 2 is simpler.** Every usable file follows the CMS v3 template, so the old-format parsers and
   the NIM column-mapping step drop to a **fallback** (kept for future metros). The work moves to:
   streaming JSON (ijson) for Dignity and Abrazo, unpivoting the wide CSV, reading inside zip files, and
   v3 edge cases (multi-code rows, `%`/algorithm rates, mixed date formats like `2026-09-30` vs `11/4/2026`).
2. **Phase 1 downloads must stream and filter, never store whole files.** The 21.5 GB Mayo file can't sit
   on a free GitHub runner (about 14 GB of disk). Downloads will be read as a stream, filtered on the fly to the
   chosen procedure codes, and only the small extract kept. The file is still fingerprinted (hashed) while
   it streams. Servers support resuming downloads (byte ranges), so an interrupted Mayo download can continue
   where it stopped.
3. **Manual addresses for Banner and Abrazo.** Their `cms-hpt.txt` files refuse automated clients.
   The price-file addresses are kept by hand in `config/hospital_sites.toml`, and the monthly `check` job will
   flag any that break. We **do not** disguise the scraper as a browser to get past bot protection.
4. **"Blocked for automated access" becomes a compliance finding.** The rule (45 CFR 180.50(d)(3)) requires
   the file to be accessible to automated searches and direct downloads. City of Hope and Arizona Spine &
   Joint will appear on the data-quality page as *not machine-accessible* rather than being quietly skipped.
   East Valley ER's wrong `cms-hpt.txt` is also a finding.
5. **The compliance score (Phase 4) adds:** is `cms-hpt.txt` reachable, does it list this hospital, are all 5
   required fields present, does the file's `hospital_name` match.
6. **Coverage:** prices for 35 of 40 hospitals. The 5 missing ones are mostly small specialty hospitals.
   All 7 major systems in the metro are covered.

## Open questions for you (none block Phase 1)

- **Arizona Orthopedic & Surgical Specialty Hospital (CCN 030112):** if you know or can find its website,
  tell me. Otherwise it's listed as "price file not found".
- **City of Hope and Arizona Spine & Joint:** the recommendation is to show them as "not machine-accessible"
  rather than downloading their files by hand each month. Say if you'd rather do the manual download.

---

# Phase 2 findings (2026-11-22)

**Parsed:** 33 files → **94,003 price rows** for 34 procedures (2.3 MB of extracts, from 2.9 GB of files).
Every file was CMS v3, so the NIM column-mapping fallback wasn't needed.

| Finding | Effect | Handled in |
|---|---|---|
| **Mayo Clinic Arizona skipped** (21.5 GB) at your request | 1 major hospital missing from the comparison | Run it any time: `python -m hpc.parse.run --ccn 030103` |
| **Abrazo Scottsdale's file lists only supplies** (catheters, implants), with no procedures and no insurer rates | No prices for this hospital | Phase 4: "incomplete file" compliance finding |
| **Exceptional Maricopa labels CPT codes as chargemaster codes** | Fixed with a per-hospital setting (`code_type_aliases` in `config/hospital_sites.toml`); matches are labelled `CDM→CPT` | Phase 4: flag as a file-quality issue |
| **Arizona General Laveen and Mesa have identical contents** (same items, same rates) | Possibly one shared price list for both campuses | Phase 4: duplicate-file check |
| **21% of rates are a percentage of charges**, 3.5% are formula-only | Not yet in dollars | Phase 3 converts percentages using the gross charge; formula-only rows are counted, not priced |
| **Cash prices for surgeries are rare** (1-3 hospitals for colonoscopy, knee replacement and similar); common for imaging and labs | Cash comparison is thin for surgery | Shown on the site as "cash price not published" |
| **Strict CSV readers reject real files** (Banner, HonorHealth quoting) | The parsers use Python's forgiving CSV reader | Done |
| **Insurer names are inconsistent** ("United Healthcare", "UnitedHealthcare", "Cigna" vs "Cigna Healthcare of Arizona, Inc.") | Insurer comparison needs matching | Phase 3 (NIM embeddings) |
| **DRG numbers mix systems** (MS-DRG, TRICARE's TRIS-DRG, Medicaid's APR-DRG) | APR-DRG numbers mean different stays, so they're never matched to MS-DRG codes | Done |

---

# Phase 3 findings (2026-11-22)

**Result:** 107,035 parsed rows → **46,142 canonical prices** (one per hospital × procedure × insurer plan ×
setting × scope), of which **32,544 are directly comparable**, across 34 hospitals.

| Topic | What we found / did |
|---|---|
| **NVIDIA models** | The planned models (llama-3.3-70b-instruct, nv-embedqa-e5-v5) were retired in Aug 2026. Now using `nemotron-3.5-embed-8b` (embeddings) and `nemotron-3.5-lightning-30b-a3b` (text), set in `config/nim.toml`. The API key lives only in the git-ignored `.env`. |
| **Insurer names** | 463 raw names → **57 insurers**: 440 by alias rules, 0 needed embeddings, 2 sent to review, 21 kept under their own names (employers paying claims directly such as QuikTrip and Intel, small networks, a prison health company). |
| **Embedding reliability** | Checked on 130 names with a known answer (leave-one-out): similarity ≥ 0.70 was **100% correct**, ≥ 0.60 was 95%. Auto-accept is set at 0.72. |
| **Wickenburg's contract suffixes** | `_-IP`, `_-OP`, `_-CLIN`, `_-PRO`, `_-REC`, `_-SW` are contract settings, not insurers. They are split off; only the outpatient contract counts for outpatient comparisons. |
| **Plan types** | Commercial 35%, Medicare Advantage 33%, Medicaid 9%, exchange 7%, unknown 5% (mostly Wickenburg's coded plans). |
| **Description-only items** | 112 distinct descriptions had no CPT code. 18 were accepted, used only where a hospital has no coded price for that procedure. Rules handle what embeddings miss: ER *procedures* vs ER visit levels, "WO/W" (without and with contrast), OB triage, CBC without differential. |
| **Rates** | 6,801 percentage-of-charges rates converted to dollars using the gross charge; 725 per-day rates kept apart; 2,090 formula-only rates counted but not priced. |
| **Not comparable (and why)** | Outpatient services priced under inpatient or other contracts (8,295), doctor fees rather than hospital fees (2,670), formula-only (1,205), per-day rates (725), duplicates of coded rows (698). |
| **Spotted for Phase 4** | A **$1** Aetna rate at Abrazo Arrowhead (placeholder). Banner's **cash prices are below its insured rates** for some imaging (a Phase 5 question). |

---

# Phase 4 findings (2026-11-22)

**Labels.** Of 32,360 comparable insurer prices: **29,852 good**, 2,180 suspect, 328 rejected.
Report for the website: `data/quality/report.md`; per-hospital scores: `data/quality/hospital_scores.csv`.

| Topic | What we found |
|---|---|
| **Medicare baseline** | Inpatient (DRG) base rates from the FY 2026 IPPS final rule, per hospital wage index (all Phoenix hospitals: 1.0741). DRG 470 = $14,723.87, matching a hand calculation. **Check on the baseline:** traditional Medicare's own DRG rate in the files is exactly **1.00×** it; Medicare Advantage 1.12×, exchange 1.6×, commercial 2.19× (medians). Outpatient and lab rates (M6, AMA CPT licence) skipped for now: outpatient prices are compared with other Phoenix hospitals instead. |
| **Placeholders** | 288 insurer prices are $0, $0.01, $1 or 9,999,999-style, mostly at **Abrazo** (123 at Central Campus alone). |
| **Same amount for every procedure** | 40 prices across a few plans (e.g. one exchange plan at Abrazo lists $4,160 for everything). |
| **Wickenburg's percentages** | 92% of its percentage-of-charges rates, converted with the line's gross charge, land far above the file's own range of real payments. The percentage most likely applies to the whole claim, so these conversions are flagged as suspect; Phase 5 will rely on dollar rates there. |
| **Formulas** | HonorHealth gives 21% of insurer rates as formulas (e.g. Medicaid pointing to the state method). That's allowed, so it lowers "data elements" rather than "validity". |
| **Core requirement missing** | **Exceptional Maricopa** and **Abrazo Scottsdale** publish no insurer rates for the chosen procedures (only gross and cash prices); score capped at 50. |
| **Identical price lists** | Arizona General Laveen and Mesa have identical files (confirmed by fingerprint): one price list for two campuses. |
| **Source audit (replaces M4)** | 500 random final prices from 32 hospitals re-read from the original files with an independent reader: **500/500 match** on code, insurer, plan, amount (including % conversions), gross, cash and description. Sabotage test on 200 real prices (deliberately corrupted copies): amount +1% 200/200 caught, amount +$1 200/200, wrong plan 200/200, wrong code 200/200, wrong record 200/200, gross +5% 107/107, cash +$2 108/108, swapped insurer 189/190; the one "miss" was a swap that happened to be true (the file lists that insurer and plan at the same price). |
| **Grades** | A: Dignity (5), Valleywise, Phoenix Children's, HonorHealth (7), Wickenburg, CORE. B: Banner (13, index file blocks programs), Abrazo (3, placeholders). D: Exceptional, Abrazo Scottsdale. F: City of Hope, Arizona Spine & Joint, AZ Orthopedic & Surgical, HonorHealth Tempe, East Valley ER. Mayo: not scored (not processed). |

---

# Phase 5 findings (2026-11-25)

Unit: one price per hospital × procedure × insurer × plan type (median over plans), **good prices only**:
23,315 hospital prices from 32 hospitals in **8 price-setting systems** (Banner's 13 hospitals share one list;
every statistic reports both hospital and system counts).

| Statistic | Result |
|---|---|
| **Price spread** | Large even within one insurer. Hip/knee replacement stay, commercial: medians $39k (UHC) to $49k (Cigna), with the dearest hospital 3-4× the cheapest. Knee MRI with UHC: $230 to $2,080 (9×). |
| **Multiple of Medicare** (hospital stays) | Medians: commercial **2.18×**, exchange 1.59×, Medicare Advantage 1.14×, workers' comp 2.78×, Medicare supplement 2.74×. By insurer (commercial): BCBS AZ 1.93×, UHC 2.31×, Aetna 2.46×, Cigna 2.68×. |
| **Hospital price level** (commercial, vs Phoenix median) | Lowest: Abrazo (0.57-0.60), Chandler Regional (0.57), CORE (0.64). Highest: Phoenix Children's (1.46), St. Joseph's (1.40), Wickenburg (1.23). Banner 0.96-1.09, HonorHealth 0.80-0.90. |
| **Insurer index** (commercial, same hospitals and procedures; 1.00 = average insurer) | Oscar **0.76**, UHC 0.96, Humana 0.97, Aetna 1.03, BCBS AZ 1.12, Cigna **1.23**. With conservative intervals clustered by system, only Oscar is clearly below average; Cigna is borderline above (0.99-1.54). Oscar sells mainly marketplace plans. Rental networks (MultiPlan, First Health, Coventry) are excluded. Medicare Advantage insurers cluster within ±8% of each other. |
| **Cash vs insured** | For **54%** of hospital-procedure pairs the cash price is below the median commercial price; 59% of individual insurer prices are above cash. It depends on the service: cash usually wins for imaging and ECGs (ECG: 86% of pairs), but **labs are the reverse** (metabolic panels: cash about $65-127 vs insured about $18-22; cash never wins). |
| **What explains price differences** | Tested on held-out hospitals *and* held-out systems. Only **ownership** survives: physician-owned (0.61) and for-profit (0.75) hospitals are cheaper than non-profit and government ones (1.02); it cuts held-out error by 22%. **Size, teaching, low-income share, case mix, star rating, specialty and market concentration do not** predict held-out hospitals better than the average, so the site won't claim they matter. System membership explains ~20% within known systems but can't be tested on unseen systems. Caveats: 32 hospitals, 8 systems, and CMS ownership labels (e.g. Abrazo Central listed as physician-owned). |
| **Change over time** | Baseline snapshot saved (`data/history/`). Changes appear after the next refresh in which a hospital publishes a new file. |

---

# Phase 6 findings (2026-11-25)

**203 plain-English summaries** (34 procedures × the 5 default insurers' commercial plans, plus the cash price),
written by NVIDIA `nemotron-3.5-lightning-30b-a3b`. Output: `data/summaries/summaries.json`.

| Topic | What happened |
|---|---|
| **Number guard** | Typed: dollar amounts must match dollar facts, percentages must match percentage facts, multiples must match multiples, other numbers must match counts. It also rejects rounding ("about $750"), abbreviations ("$39k"), numbers in words ("five", "twice"), advice ("you should", "best"), certainty about bills ("you will pay"), quality-of-care claims, coverage claims ("covers") and summaries that don't say who pays. **All 203 final summaries pass**; I re-checked them after generation. |
| **How often the model needed help** | First full run: 180/203 accepted (23 fell back to the template because the reasoning model ran out of thinking room at 4,000 tokens). After raising the limit to 8,000: all 203 accepted, 4 on a second try (each had written a number as a word). |
| **Safety wording** | Never written by the model: a fixed notice on every summary (facility part only; doctors' fees billed separately; deductible, coinsurance and complications change the bill; not medical, insurance or financial advice). |
| **Cache** | Keyed by the facts, the prompt version and the model. When the numbers changed, only those summaries were regenerated (45 of 203). Cached text is also re-checked against the *current* guard, so tightening a rule regenerates only the summaries that now break it (5 of 203 for the "covers" rule). API failures are never cached. |
| **Tied prices** | When several hospitals share the lowest or highest price (common: Banner's 13 hospitals share one list), the facts say "12 Banner Health hospitals" instead of naming one. |
| **Phase 4 fix found here** | Reviewing summaries exposed a $35.96 CT and a $10.12 chest X-ray at **Chandler Regional** labelled "good": component lines (gross charge = price) rather than the full service. Two rule fixes: (1) the "far from other Phoenix prices" check now compares within the same plan type (Medicaid and Medicare Advantage prices were dragging the median down); (2) a new **component-line** rule flags items whose gross charge is under a tenth of other hospitals' *and* whose price is under a quarter of other hospitals' prices for the plan type (195 prices, mostly Chandler imaging). Comparable insurer prices are now 30,110 good, 1,922 suspect and 328 rejected. |

---

# Phase 5 numbers, updated after the Phase 6 quality fix (2026-11-25)

The Phase 6 rule fixes (outlier check within plan type; component-line rule) changed which prices count as
"good", so the Phase 5 statistics were re-run. The site always uses the current numbers. What changed:

| Statistic | Before | Now |
|---|---|---|
| Hospital price level, lowest | Abrazo 0.57-0.60, **Chandler Regional 0.57**, CORE 0.64 | Abrazo 0.54-0.58, CORE 0.62. Chandler's low index came from its component lines, now flagged |
| Hospital price level, highest | Phoenix Children's 1.46, St. Joseph's 1.40 | **St. Joseph's 1.72**, Phoenix Children's 1.50, Arizona General 1.30, Valleywise 1.25 |
| Insurer index (commercial) | Oscar 0.76 … Cigna 1.23 | Oscar 0.77, UHC 0.93, Humana 0.94, Aetna 1.02, BCBS AZ 1.09, Cigna 1.33. Still only Oscar clearly below average; Cigna borderline above (0.98-1.81) |
| Cash vs insured | 54% | 57% of pairs have cash below the median commercial price; labs still the exception (cash below insured at 3%) |
| Drivers | ownership survives (-22% error) | ownership still the only survivor (-20%): physician 0.59, for-profit 0.78, non-profit 1.07, government 1.17 |
| Multiple of Medicare | commercial 2.18×, MA 1.14× | unchanged |

---

# Phase 7 findings (2026-11-25)

**A static website in `site/`**: plain HTML, CSS and JavaScript (ES modules), no build step, no third-party
scripts, fonts or map tiles. Data comes from `python -m hpc.site.build` as small JSON files (about 5.6 MB in total,
much less compressed). Pages: Compare (main tool), Procedures, Hospitals, Insurers, Data quality, About.

| Topic | What was done |
|---|---|
| **Compare** | Procedure + insurer and plan type (or cash) → hospitals ranked by price, with plan ranges, multiple of Medicare (stays) or vs the median, distance from a ZIP code, CMS stars, and the price-data grade. Also a locator map (no basemap), the guarded summary and the safety notice, plus a list of the hospitals *not* in the comparison and why. The URL keeps the selections (shareable). |
| **Charts** | One hue for values, gray for de-emphasis, status colours only with icon and text. Palette validated with the dataviz validator (colour-blind separation 15.9, normal vision 17+, contrast ≥ 3:1 in light and dark). Hover tooltips, and a table view for every chart. Hospitals sharing a price draw one dot with a count, not a pile of overlapping dots. |
| **Accessibility** | Skip link, landmarks, labelled controls, sortable tables with `aria-sort`, focus rings, light/dark (follows the system, with a toggle), reduced motion, forced colours, print. All data text goes into the page as text, never as HTML. |
| **Checked by rendering** | Every page was rendered in headless Edge (the Chrome extension wasn't connected) and screenshotted. That caught and fixed: a table squeezed to half width, repeated legend bands, map label collisions, unreadable stacked dots, and long chart labels running off the edge. The headless browser can't go below 492 px wide, so phone widths (360-390 px) rely on the responsive CSS and haven't been seen. |
| **Stale text** | The insurer page's verdict sentence is computed from the confidence intervals, so it can't go out of date when the data changes. |

---

# Phase 8 findings (2026-11-25)

| Topic | What was built / found |
|---|---|
| **Workflows** | `tests` (every push; includes headless-Chrome page renders), `check` (weekly), `refresh` (monthly, after `check`, or by hand; deploys Pages; opens an issue on problems), `pages` (publish on push). |
| **State between runs** | GitHub runners start empty. `hpc/vault.py` restores and saves fingerprints, extracts, caches (embeddings, summaries) and price history in the **private** vault repo. Full price files are never stored, only their address, date, size and SHA-256. The token is passed to git through an environment-scoped header, never on the command line, and is scrubbed from errors (tested). |
| **No re-downloads for nothing** | `download --trust-state`: a file whose server change markers match the saved fingerprint is skipped even though it isn't on the runner's disk; re-parsing re-downloads only what it needs. |
| **Check without false alarms** | First local run of the weekly check would have triggered a refresh every week: two files (Valleywise, Phoenix Children's) come from a server with no change markers, and Mayo is never downloaded by design. Fixed: those are listed but don't trigger; the monthly refresh re-checks them. Second run: correctly "no change". |
| **Too big for a free runner** | Mayo Clinic Arizona (21.5 GB) is skipped and reported; the 2 GB per-file disk limit leaves room on a 14 GB runner. |
| **Problems → issue** | `hpc/report.py` turns failed steps, failed downloads/parses, hospitals whose file became blocked or disappeared, and audit mismatches into a GitHub issue ("Refresh problems YYYY-MM"; later runs that month comment on it). |
| **What's public** | Code, config, the hospital list (`data/phase0`, `data/registry`) and the site (`site/`, aggregated JSON). Not public: `.env`, pipeline working data, the vault, CMS licensed CPT files, and copies of hospitals' cms-hpt.txt files (they contain staff contact names). Checked before the first push: no keys, tokens, personal emails or local paths. |
| **First run** | The first `refresh` on GitHub downloads everything (≈ 3 GB) and rebuilds the vault from scratch, roughly 30-45 minutes. Later runs only process changed files. |

---

# Website redesign (2026-11-26)

Dark & modern look (navy, glass cards, drifting gradient glows, teal→blue accent) with smooth animations, and a
simpler path to the answer. Same data and pages.

| Change | Detail |
|---|---|
| **Home = guided 3-step finder** | ① procedure (with "Popular" chips) ② how you pay (chips: cash + the 5 largest insurers, "Other insurer…", plan type with an ⓘ explainer) ③ optional ZIP. |
| **Answer first** | Big "Typical price" (count-up), "Most hospitals charge between X and Y", and a range bar (lowest → highest, middle-80% band, median, a dot per hospital). |
| **Hospital cards** | Rank, name, system, price, "53% below typical" in plain words, distance, CMS stars, grade chip, "Lowest price"/"Closest" tags; Cards/Table toggle; map behind "Show on a map". |
| **Plain words** | ⓘ explainers for typical/median, plan type, price-data grade, multiple of Medicare; "About" renamed "How it works". |
| **Animations** | Intro rise, drifting glows, count-ups, range bar grows and dots pop in, card hover lift with glow, button light sweep, chip press, sliding nav underline, scroll reveals with stagger, shrinking blurred header, scroll progress bar, cross-fading results (View Transitions), page-to-page fade, theme switch fade. All off under "reduce motion". |
| **Still true** | One validated data hue for charts (re-validated on the new card surface), table view for every chart, keyboard + screen-reader support, light theme via toggle, no third-party scripts. |
| **Bugs caught while checking** | Info pop-ups widened the page (fixed: removed from layout when closed, clamped when open); hero lost its side padding on narrow screens; ~100 overlapping dots on the pooled range bar (now band + median only past 40 prices); headless captures froze mid-animation (renderer now forces reduced motion for checks). |

## Plain-language pass (2026-11-26)

The user found the redesign still confusing ("what and why I should open this site?"). Changes:

- **Home explains why in five seconds**: a headline built from live data ("The same knee MRI costs $230 at one Phoenix hospital and $2,080 at another. Same insurance. Same scan."), a real example card, "Who is this for?" cards, then the checker.
- **Checker asks plain questions**: "I have insurance" / "I'll pay myself"; plan types in human words ("Through work or bought myself", "Medicare plan from a private company", "AHCCCS (Arizona Medicaid)").
- **Answer is a sentence**: "With UnitedHealthcare, a knee MRI usually costs about $544 at a Phoenix hospital", plus a "What will I actually pay?" box and "What to do next" steps with a copyable billing code.
- **Glossary everywhere** (`FRIENDLY`, `planWords`, `vsUsual`, `plainText` in `common.js`): median → usual price, commercial → through work or bought yourself, grade → price list quality, suspect/rejected → looks odd/removed, CPT/DRG → billing code. AI summaries get the same word swaps on display (numbers untouched). Technical detail kept in "Details for the curious" folds.
- **Nav**: Check a price · Procedures · Hospitals · Insurance companies · Can I trust this? · About.
- **Premium finish**: film grain, cursor spotlight on cards, gradient hairline on the hero card, tighter headings, sticky "Check a price" button on phones.
