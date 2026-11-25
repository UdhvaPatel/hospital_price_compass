import { shell, loadJSON, el, gradeBadge, labelBadge, table, errorBox } from "../common.js";

const content = document.getElementById("content");

async function main() {
  const meta = await shell();
  const [q, hospitals] = await Promise.all([loadJSON("data/quality.json"), loadJSON("data/hospitals.json")]);
  const c = meta.counts;
  const nodes = [
    el("p", { class: "lead" }, `Every insurer price on this site carries a label, and every hospital's price file gets a compliance score. `,
      `${c.hospitals_in_scope} hospitals are in scope; ${c.hospitals_with_prices} have usable prices.`),
    el("div", { class: "tiles" },
      [["Good", c.good_prices, "passed every check", "good"], ["Suspect", c.suspect_prices, "shown with a warning", "suspect"],
        ["Rejected", c.rejected_prices, "never shown in comparisons", "rejected"]].map(([l, v, sub, lab]) =>
        el("div", { class: "tile" }, el("div", { class: "label" }, labelBadge(lab)), el("div", { class: "value" }, v.toLocaleString("en-US")),
          el("div", { class: "sub" }, `${sub} (comparable insurer prices)`)))),
    el("h2", {}, "What we check"),
    table([{ key: "text", label: "Check", render: (r) => r.text[0].toUpperCase() + r.text.slice(1) },
      { key: "severity", label: "Label", render: (r) => labelBadge(r.severity), sortValue: (r) => r.severity },
      { key: "count", label: "Prices flagged", num: true, render: (r) => r.count.toLocaleString("en-US") }],
    q.rules, { caption: "Quality rules", sortKey: "count", sortDir: "desc" }),
  ];
  if (q.audit) {
    nodes.push(el("h2", {}, "Checked against the original files"),
      el("p", {}, `We re-read ${q.audit.sampled_prices} random prices from ${q.audit.hospitals} hospitals straight from the hospitals' original files, `,
        `with a separate, simple reader. ${q.audit.passed} of ${q.audit.sampled_prices} matched exactly (code, insurer, plan, amount, gross charge, cash price, description).`));
  }
  const scored = hospitals.filter((h) => h.in_scope).sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  nodes.push(el("h2", {}, "Compliance score by hospital"),
    el("p", { class: "muted small" }, "Out of 100: index file and its fields (15), machine-readable price file (15), CMS format (10), updated within a year (10), ",
      "required data elements (15), coverage of this site's procedures (20), valid prices (15). Missing a core requirement (a machine-readable file, ",
      "or any insurer rates) caps the score at 50. This grades the price file, not the quality of care."),
    el("ul", { class: "meters" }, scored.map((h) => el("li", {},
      el("a", { href: `hospital.html?ccn=${h.ccn}` }, h.name),
      el("div", { class: "track", role: "img", "aria-label": h.score != null ? `${Math.round(h.score)} out of 100` : "not scored" },
        el("div", { class: "fill", style: `width:${h.score ?? 0}%` })),
      el("span", { class: "num" }, h.score != null ? Math.round(h.score) : "-"),
      gradeBadge(h.grade, h.score)))));
  nodes.push(el("h2", {}, "Findings"),
    ...scored.filter((h) => h.findings.length).map((h) => el("details", {}, el("summary", {}, `${h.name} (${h.findings.length})`),
      el("ul", { class: "findings" }, h.findings.map((f) => el("li", {}, f))))));
  const out = hospitals.filter((h) => !h.in_scope);
  nodes.push(el("h2", {}, "Not compared"),
    el("p", {}, `${out.length} hospitals in the area aren't compared: psychiatric hospitals (they don't offer these procedures) and federal hospitals `,
      "(VA and Indian Health Service, which are exempt from the price transparency rule)."),
    el("details", {}, el("summary", {}, "List"), el("ul", {}, out.map((h) => el("li", {}, `${h.name}: ${h.exclusion}`)))));
  content.replaceChildren(...nodes);
}

main().catch((e) => errorBox(content, e));
