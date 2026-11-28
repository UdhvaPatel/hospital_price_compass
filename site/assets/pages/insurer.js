import { shell, loadJSON, el, money, times, table, errorBox, param, hero, icon, fold, planWords } from "../common.js";
const vs = (r) => r >= 1.03 ? `${Math.round((r - 1) * 100)}% more` : r <= 0.97 ? `${Math.round((1 - r) * 100)}% less` : "about the same";
import { reveal } from "../motion.js";
import { forest, dotStrip, withTableView } from "../charts.js";

const content = document.getElementById("content");

function indexChart(rows, names, emphasis) {
  const data = rows.map((r) => ({ label: names[r.payer_id] || r.payer_id, est: r.index,
    lo: r.ci_low_system ?? r.ci_low, hi: r.ci_high_system ?? r.ci_high,
    emphasis: emphasis ? r.payer_id === emphasis : undefined }));
  const chart = forest(data, { ref: 1, refLabel: "average company", format: (v) => v.toFixed(2),
    xTitle: "Prices at the same hospitals for the same procedures (1.00 = average; 0.90 = 10% less)" });
  const tview = table([
    { key: "label", label: "Insurance company" },
    { key: "est", label: "Compared with average", render: (r) => `${vs(r.est)} (${r.est.toFixed(2)})`, sortValue: (r) => r.est },
    { key: "lo", label: "Likely range", num: true, render: (r) => `${r.lo.toFixed(2)} to ${r.hi.toFixed(2)}`, sortValue: (r) => r.lo },
  ], data, { caption: "Insurance company prices", sortKey: "est" });
  return withTableView("Insurance company prices", chart, tview);
}

/** Plain-English verdict computed from the conservative (by-system) intervals, so it never goes stale. */
function verdict(rows, names) {
  const lo = (r) => r.ci_low_system ?? r.ci_low, hi = (r) => r.ci_high_system ?? r.ci_high;
  const n = (r) => names[r.payer_id] || r.payer_id;
  const below = rows.filter((r) => hi(r) < 1).map(n), above = rows.filter((r) => lo(r) > 1).map(n);
  const near = rows.filter((r) => lo(r) <= 1 && hi(r) >= 1 && (lo(r) > 0.97 || hi(r) < 1.03)).map(n);
  const list = (a) => a.length > 1 ? `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}` : a[0];
  const parts = [];
  parts.push(below.length ? `${list(below)} clearly ${below.length > 1 ? "get" : "gets"} lower prices than average` : "No company clearly gets lower prices than average");
  parts.push(above.length ? `${list(above)} clearly ${above.length > 1 ? "pay" : "pays"} more` : "none clearly pays more");
  let s = `${parts.join("; ")}.`;
  if (near.length) s += ` ${list(near)} ${near.length > 1 ? "are" : "is"} close to the line, so we can't be sure.`;
  if (below.includes(names.oscar || "Oscar Health")) s += " Oscar mostly sells marketplace plans with fewer hospitals to choose from.";
  return s + " For the rest, the differences are small enough that they could be chance.";
}

async function main() {
  const meta = await shell();
  const ins = await loadJSON("data/insurers.json");
  const names = { ...ins.names, ...Object.fromEntries(meta.insurers.map((i) => [i.id, i.name])) };
  const id = param("id");
  const info = meta.insurers.find((i) => i.id === id);
  const attrs = ins.attrs || {};
  const explain = fold("Details for the curious: how we compare fairly", el("p", { class: "muted small" },
    `Fixed-effects comparison: each insurer's prices are compared with other insurers' prices at the same hospital for the same procedure `,
    `(${attrs.n_obs?.toLocaleString?.() ?? ""} prices, ${attrs.n_hospitals} hospitals, ${attrs.n_systems} price-setting systems), so a cheap hospital can't make its insurers look good. `,
    "Lines are 95% intervals that treat each health system as one price-setter (hospitals in one system usually share one price list). ",
    "Rental networks such as MultiPlan are left out: they rent networks, they don't insure."));

  const sect = (eyebrow, title, ...body) => el("section", { "data-reveal": "" }, el("p", { class: "eyebrow" }, eyebrow), el("h2", {}, title), ...body);
  if (!info) {
    hero({ eyebrow: "Insurance companies", title: "Does your insurance company get you a better price?",
      lead: "Each insurance company agrees its own prices with each hospital. We compare them at the same hospitals for the same procedures, so it's a fair fight." });
    content.replaceChildren(
      sect("The answer", "Plans through work or bought yourself", el("p", { class: "summary" }, verdict(ins.index.commercial, names)),
        el("div", { class: "card" }, indexChart(ins.index.commercial, names, null)), explain),
      sect("Pick a company", "All insurance companies", el("div", { class: "grid auto", "data-stagger": "" },
        meta.insurers.filter((i) => !i.rental_network).sort((a, b) => b.hospitals - a.hospitals).map((i) =>
          el("a", { class: "card lift", href: `insurer.html?id=${i.id}` }, el("div", { class: "cat-icon" }, icon("card", 20)),
            el("h3", {}, i.name), el("p", { class: "muted small" }, `${i.parent ? `${i.parent} · ` : ""}has prices at ${i.hospitals} hospitals`))))));
    reveal(content);
    return;
  }
  document.title = `${info.name} | Hospital Price Compass`;
  const b = ins.by_insurer[id] || { plan_types: {}, commercial_vs_phoenix: [], hospitals: [] };
  hero({ eyebrow: info.parent ? `Part of ${info.parent}` : "Insurance company", title: info.name,
    lead: `Has published prices at ${b.hospitals.length} Phoenix-area hospitals, for these kinds of insurance: ` +
      Object.entries(b.plan_types).sort((x, y) => y[1] - x[1]).map(([t]) => planWords(t, meta.plan_types).toLowerCase()).join(", ") + "." });
  const nodes = [];
  const mine = ins.index.commercial.find((r) => r.payer_id === id);
  if (mine) {
    const diff = Math.round(Math.abs(mine.index - 1) * 100);
    nodes.push(sect("The short answer", `${info.name} ${diff < 2 ? "pays about the same as other companies" : `pays about ${diff}% ${mine.index < 1 ? "less" : "more"} than the average company`}`,
      el("p", { class: "muted" }, "For plans through work or bought yourself, at the same hospitals for the same procedures. ", verdict(ins.index.commercial, names)),
      el("div", { class: "card" }, indexChart(ins.index.commercial, names, id)), explain));
  }
  if (ins.index.medicare_advantage.some((r) => r.payer_id === id)) {
    nodes.push(sect("Medicare plans from private companies", "Medicare Advantage plans", el("div", { class: "card" }, indexChart(ins.index.medicare_advantage, names, id)),
      el("p", { class: "muted small" }, "These plans pay close to what Medicare itself pays, so companies differ little here.")));
  }
  const rel = b.commercial_vs_phoenix.filter((r) => r.ratio);
  if (rel.length) {
    const procName = Object.fromEntries(meta.procedures.map((p) => [p.id, p.name]));
    const chart = dotStrip(rel.map((r) => ({ label: procName[r.procedure_id] || r.procedure_id,
      points: [{ value: r.ratio, label: `${money(r.median)} vs ${money(r.phoenix_median)} for all companies` }] })),
    { log: true, format: times, ref: 1, refLabel: "all companies", rowHeight: 26, xTitle: `${info.name}'s usual price compared with all companies` });
    const tview = table([{ key: "name", label: "Procedure", render: (r) => procName[r.procedure_id] || r.procedure_id, sortValue: (r) => procName[r.procedure_id] },
      { key: "median", label: info.name, num: true, render: (r) => money(r.median) },
      { key: "phoenix_median", label: "All companies", num: true, render: (r) => money(r.phoenix_median) },
      { key: "ratio", label: "Compared", render: (r) => vs(r.ratio) },
      { key: "hospitals", label: "Hospitals", num: true }], rel, { caption: "Where this insurer pays more or less", sortKey: "ratio" });
    nodes.push(sect("Procedure by procedure", "Where it pays more or less",
      el("p", { class: "muted small" }, "A rough guide: these are usual prices across whichever hospitals each company works with. The comparison above is the fairer one."),
      el("div", { class: "card" }, withTableView("Where this insurer pays more or less", chart, tview))));
  }
  content.replaceChildren(...nodes);
  reveal(content);
}

main().catch((e) => errorBox(content, e));
