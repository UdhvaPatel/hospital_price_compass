import { shell, loadJSON, el, pct, times, errorBox, hero, icon, fold } from "../common.js";
import { reveal } from "../motion.js";

const content = document.getElementById("content");

async function main() {
  const meta = await shell();
  const stats = await loadJSON("data/stats.json");
  const c = meta.counts, s = stats.summary;
  const mm = Object.fromEntries(stats.medicare_by_plan_type.map((r) => [r.plan_type, r.median]));
  const survive = stats.drivers.filter((d) => d.survives && d.feature !== "system").map((d) => d.description);
  const failed = stats.drivers.filter((d) => !d.survives).map((d) => d.description);
  const p = (...x) => el("p", {}, ...x);

  hero({ eyebrow: "About", title: "Why this site exists",
    lead: "Hospitals charge very different prices for the same thing, and until recently you couldn't see them. Now hospitals must publish their prices, but in huge technical files nobody can read. We turn those files into a simple answer: what will this cost, and where is it cheaper?" });
  const flat = [

    el("h2", {}, "Where the prices come from"),
    p("Since 2021, US hospitals have had to publish their prices, including the prices each insurance company agreed to pay. ",
      `We download each Phoenix-area hospital's price list from its own website and read it. ${c.hospitals_with_prices} of ${c.hospitals_in_scope} hospitals had a list we could use. `,
      "If a website blocks automatic downloads, we respect that and say so instead."),
    p("We also use public information from Medicare (hospital details, star ratings, Medicare prices) and the US Census (locations and ZIP codes)."),

    el("h2", {}, "How we make them easy to compare"),
    el("ul", {},
      el("li", {}, `We pick ${meta.procedures.length} common procedures and find them in every list by their billing code.`),
      el("li", {}, "Hospitals spell insurance company names hundreds of different ways (we found 463); we match them to 57 companies."),
      el("li", {}, "We only compare like with like: a price for a whole hospital stay is never compared with a price for one day."),
      el("li", {}, "We check every price and remove ones that can't be real. The \u201cCan I trust this?\u201d page lists every check."),
      el("li", {}, "We double-checked 500 random prices against the original files: all matched.")),

    el("h2", {}, "What we learned"),
    el("ul", {},
      el("li", {}, `For hospital stays, insurance through work or bought yourself usually pays ${times(mm.commercial)} what Medicare pays; Medicare plans from private companies pay ${times(mm.medicare_advantage)}.`),
      el("li", {}, `Paying yourself is sometimes cheaper: at ${pct(stats.cash_vs_insured.share_cash_below_median_insured)} of hospitals the self-pay price is below the usual insurance price. `,
        "That's often true for scans, rarely for lab tests."),
      el("li", {}, "Hospitals in the same group usually share one price list, so we always say how many hospitals and how many groups a number is based on."),
      el("li", {}, fold("Details for the curious: what explains price differences",
        el("p", { class: "small muted" }, `Tested on hospitals and groups the model hadn't seen, only ${survive.join(", ") || "none"} held up. These did not: ${failed.join("; ")}.`)))),

    el("h2", {}, "What this site can't tell you"),
    el("ul", {},
      el("li", {}, "These are the hospital's own prices. Doctors, anaesthesia and other professionals usually send separate bills."),
      el("li", {}, "What you pay depends on your plan: your deductible, coinsurance, out-of-pocket limit, and whether the hospital is in your network."),
      el("li", {}, "Complications or extra services change the bill. Prices here are for the standard service."),
      el("li", {}, "Hospital price lists can be incomplete or wrong. Our checks catch many problems, but not all."),
      el("li", {}, "Mayo Clinic Arizona's price list (21.5 GB) wasn't processed in this update.")),

    el("h2", {}, "The short summaries"),
    p("Some pages have a short written summary. An AI model (NVIDIA Nemotron) writes it using only our numbers. ",
      "A program checks every summary and throws it away if it contains any number we didn't give it, or any advice or ranking; a fixed template is used instead."),

    el("h2", {}, "Not advice"),
    p("This site is for information only. It is not medical, insurance or financial advice. Ask your insurance company and the hospital for an estimate before a planned procedure.",
      ` Data as of ${meta.as_of}.`)];

  // group the flat list into one glass card per heading, revealed as you scroll
  const ICON_FOR = ["scan", "shield", "chart", "info", "heart", "check"];
  const sections = [];
  for (const node of flat) {
    if (node.tagName === "H2") sections.push(el("section", { "data-reveal": "" }, el("div", { class: "card" },
      el("div", { class: "cat-icon" }, icon(ICON_FOR[sections.length % ICON_FOR.length], 20)), node)));
    else if (sections.length) sections[sections.length - 1].firstChild.append(node);
  }
  content.replaceChildren(el("div", { class: "notice", "data-reveal": "" }, icon("shield", 20), el("div", {}, "Prices here are a guide, not a quote. Always confirm with your insurer and the hospital.", fold("Why your bill may differ", el("p", { class: "small" }, meta.safety_notice)))),
    el("div", { class: "grid two" }, sections));
  reveal(content);
}

main().catch((e) => errorBox(content, e));
