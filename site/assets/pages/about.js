import { shell, loadJSON, el, pct, times, errorBox } from "../common.js";

const content = document.getElementById("content");

async function main() {
  const meta = await shell();
  const stats = await loadJSON("data/stats.json");
  const c = meta.counts, s = stats.summary;
  const mm = Object.fromEntries(stats.medicare_by_plan_type.map((r) => [r.plan_type, r.median]));
  const survive = stats.drivers.filter((d) => d.survives && d.feature !== "system").map((d) => d.description);
  const failed = stats.drivers.filter((d) => !d.survives).map((d) => d.description);
  const p = (...x) => el("p", {}, ...x);

  content.replaceChildren(
    el("p", { class: "lead" }, "What will a procedure cost, at which Phoenix-area hospital, with your insurance or paying cash? ",
      "This site collects the prices hospitals are required to publish, cleans them, checks them, and compares them fairly."),
    el("p", { class: "notice" }, meta.safety_notice),

    el("h2", {}, "Where the prices come from"),
    p("Since 2021, US hospitals must publish their standard charges, including the rates they negotiate with each insurer, in a ",
      "machine-readable file (45 CFR 180; CMS's standard format since July 2024). We found each hospital's file through the cms-hpt.txt index ",
      `file on its website, downloaded it, and read every format (CSV and JSON). ${c.hospitals_with_prices} of ${c.hospitals_in_scope} hospitals in scope `,
      "had a usable file. We never get around a website's blocks on automated access: blocked files are reported, not scraped."),
    p("Other sources: CMS Hospital General Information (hospital list, star ratings), the CMS FY 2026 inpatient payment rule (Medicare rates, beds, teaching), ",
      "and US Census data (locations and ZIP codes)."),

    el("h2", {}, "How we clean and check them"),
    el("ul", {},
      el("li", {}, `${meta.procedures.length} procedures are matched by billing code (CPT/HCPCS or MS-DRG); a few hospitals list some only by description, matched with checks.`),
      el("li", {}, "Insurer names (463 spellings) are matched to 57 insurers with rules and NVIDIA text embeddings; plan types come from the plan names."),
      el("li", {}, "Percent-of-charges rates are converted to dollars; per-day rates, doctors' fees and inpatient-only contracts are kept apart so only like is compared with like."),
      el("li", {}, "Every price gets a quality label (good, suspect, rejected). Only good prices are compared; the Data quality page lists every rule."),
      el("li", {}, "500 random prices were re-read from the original files with a separate reader: all matched.")),

    el("h2", {}, "What the numbers say"),
    el("ul", {},
      el("li", {}, `Hospital stays: commercial plans pay a median ${times(mm.commercial)} the Medicare base rate; Medicare Advantage ${times(mm.medicare_advantage)}.`),
      el("li", {}, `Cash vs insurance: at ${pct(stats.cash_vs_insured.share_cash_below_median_insured)} of hospital-procedure pairs the cash price is below the typical commercial price. `,
        "It depends on the service: cash often wins for imaging, rarely for lab tests."),
      el("li", {}, `What explains price differences: tested on hospitals and systems the model hadn't seen, only ${survive.join(", ") || "none"} held up. `,
        `These did not: ${failed.join("; ")}.`),
      el("li", {}, `Hospitals in one system usually share one price list: ${c.hospitals_with_prices} hospitals come from ${c.systems} price-setting systems, so every statistic is reported with both counts.`)),

    el("h2", {}, "Limitations"),
    el("ul", {},
      el("li", {}, "These are the hospital's prices for its own (facility) part. Doctors, anaesthesia and other professionals usually bill separately."),
      el("li", {}, "Your cost depends on your plan: deductible, coinsurance, out-of-pocket maximum, and whether the hospital is in your network."),
      el("li", {}, "Complications or extra services change the bill. A price here is for the standard service described."),
      el("li", {}, "Hospitals' files can be incomplete or wrong; quality labels catch many problems but not all."),
      el("li", {}, "Mayo Clinic Arizona's file (21.5 GB) wasn't processed in this update; it will be added in a later refresh."),
      el("li", {}, "Multiples of Medicare are shown for hospital stays only; outpatient prices are compared with other Phoenix hospitals.")),

    el("h2", {}, "Plain-English summaries"),
    p("Summaries are written by an AI model (NVIDIA Nemotron) from the computed numbers only. A number guard rejects any summary containing a number ",
      "that isn't in our data, or advice, rankings or claims about coverage or quality; a fixed template is used instead. The safety notice is never written by the model."),

    el("h2", {}, "Not advice"),
    p("Educational information only, not medical, insurance or financial advice. Ask your insurer and the hospital for an estimate before a planned procedure.",
      ` Data as of ${meta.as_of}.`));
}

main().catch((e) => errorBox(content, e));
