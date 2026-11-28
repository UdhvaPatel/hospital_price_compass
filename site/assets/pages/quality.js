import { shell, loadJSON, el, gradeBadge, labelBadge, table, errorBox, hero, icon, infoTip, fold } from "../common.js";
import { reveal } from "../motion.js";

const content = document.getElementById("content");

async function main() {
  const meta = await shell();
  const [q, hospitals] = await Promise.all([loadJSON("data/quality.json"), loadJSON("data/hospitals.json")]);
  const c = meta.counts;
  hero({ eyebrow: "Can I trust this?", title: "Can you trust these prices?",
    lead: `Mostly, yes. Every price comes straight from the hospital's own published price list. Those lists can contain mistakes, so we check every price and remove the ones that can't be real. ${c.hospitals_with_prices} of ${c.hospitals_in_scope} Phoenix hospitals had a list we could use.` });
  const sect = (eyebrow, title, ...body) => el("section", { "data-reveal": "" }, el("p", { class: "eyebrow" }, eyebrow), el("h2", {}, title), ...body);
  const nodes = [
    el("section", {}, el("div", { class: "grid three", "data-stagger": "" },
      [["good", c.good_prices, "prices passed every check: these are the prices you see"], ["suspect", c.suspect_prices, "prices look unusual: left out of comparisons"],
        ["rejected", c.rejected_prices, "prices can't be real (like $1 placeholders): removed"]].map(([lab, v, sub]) =>
        el("div", { class: "card stat lift" }, labelBadge(lab), el("div", { class: "value", "data-count": v }, v.toLocaleString("en-US")), el("div", { class: "label" }, sub))))),
    sect("The checks", "What we look for",
      el("div", {}, table([{ key: "text", label: "Problem we look for", render: (r) => r.text[0].toUpperCase() + r.text.slice(1) },
        { key: "severity", label: "What happens", render: (r) => labelBadge(r.severity), sortValue: (r) => r.severity },
        { key: "count", label: "Prices caught", num: true, render: (r) => r.count.toLocaleString("en-US") }],
      q.rules, { caption: "Our checks", sortKey: "count", sortDir: "desc" }))),
  ];
  if (q.audit) {
    nodes.push(sect("Double-checked", "We re-read prices from the original files",
      el("div", { class: "card answer" },
        el("div", {}, el("div", { class: "big grad" }, `${q.audit.passed}/${q.audit.sampled_prices}`), el("p", { class: "sub" }, "random prices matched the hospitals' files exactly")),
        el("p", { class: "muted" }, `To make sure we didn't make mistakes, we picked ${q.audit.sampled_prices} random prices from ${q.audit.hospitals} hospitals and read them again straight from the hospitals' own files, using a separate, simple program. `,
          "Every detail matched: billing code, insurance company, plan, price and description."))));
  }
  const scored = hospitals.filter((h) => h.in_scope).sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  nodes.push(sect("Hospital scores", "Which hospitals publish a good price list?",
    el("p", { class: "muted small" }, "A score out of 100 for how complete, up to date and usable each hospital's price list is. It says nothing about the quality of medical care. ",
      infoTip("Out of 100: says where its list is (15), list can be downloaded (15), official layout (10), updated within a year (10), includes everything required (15), has prices for this site's procedures (20), prices look real (15). Missing a basic requirement caps the score at 50.")),
    el("div", { class: "card" }, el("ul", { class: "meters", "data-reveal": "" }, scored.map((h) => el("li", {},
      el("a", { href: `hospital.html?ccn=${h.ccn}` }, h.name),
      el("div", { class: "track", role: "img", "aria-label": h.score != null ? `${Math.round(h.score)} out of 100` : "not scored" },
        el("div", { class: "fill", style: `width:${h.score ?? 0}%` })),
      el("span", { class: "num" }, h.score != null ? Math.round(h.score) : "-"),
      gradeBadge(h.grade, h.score)))))));
  nodes.push(sect("Details for the curious", "What we found at each hospital",
    el("div", { class: "card" }, ...scored.filter((h) => h.findings.length).map((h) => el("details", {}, el("summary", {}, `${h.name} (${h.findings.length})`),
      el("ul", { class: "findings" }, h.findings.map((f) => el("li", {}, f))))))));
  const out = hospitals.filter((h) => !h.in_scope);
  nodes.push(sect("Not compared", `${out.length} hospitals aren't included`,
    el("p", { class: "notice" }, icon("info", 20), el("span", {}, "Psychiatric hospitals (they don't do these procedures) and federal hospitals (VA and Indian Health Service), which don't have to publish prices.")),
    el("details", {}, el("summary", {}, "Show the list"), el("ul", { class: "findings" }, out.map((h) => el("li", {}, `${h.name}: ${h.exclusion}`))))));
  content.replaceChildren(...nodes);
  reveal(content);
}

main().catch((e) => errorBox(content, e));
