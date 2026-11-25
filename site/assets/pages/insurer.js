import { shell, loadJSON, el, money, times, table, errorBox, param } from "../common.js";
import { forest, dotStrip, withTableView } from "../charts.js";

const content = document.getElementById("content");

function indexChart(rows, names, emphasis) {
  const data = rows.map((r) => ({ label: names[r.payer_id] || r.payer_id, est: r.index,
    lo: r.ci_low_system ?? r.ci_low, hi: r.ci_high_system ?? r.ci_high,
    emphasis: emphasis ? r.payer_id === emphasis : undefined }));
  const chart = forest(data, { ref: 1, refLabel: "average insurer", format: (v) => v.toFixed(2),
    xTitle: "Price index at the same hospitals for the same procedures (1.00 = average)" });
  const tview = table([
    { key: "label", label: "Insurer" },
    { key: "est", label: "Index", num: true, render: (r) => r.est.toFixed(2) },
    { key: "lo", label: "95% interval (by system)", num: true, render: (r) => `${r.lo.toFixed(2)} to ${r.hi.toFixed(2)}`, sortValue: (r) => r.lo },
  ], data, { caption: "Insurer price index", sortKey: "est" });
  return withTableView("Insurer price index", chart, tview);
}

/** Plain-English verdict computed from the conservative (by-system) intervals, so it never goes stale. */
function verdict(rows, names) {
  const lo = (r) => r.ci_low_system ?? r.ci_low, hi = (r) => r.ci_high_system ?? r.ci_high;
  const n = (r) => names[r.payer_id] || r.payer_id;
  const below = rows.filter((r) => hi(r) < 1).map(n), above = rows.filter((r) => lo(r) > 1).map(n);
  const near = rows.filter((r) => lo(r) <= 1 && hi(r) >= 1 && (lo(r) > 0.97 || hi(r) < 1.03)).map(n);
  const list = (a) => a.length > 1 ? `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}` : a[0];
  const parts = [];
  parts.push(below.length ? `${list(below)} ${below.length > 1 ? "are" : "is"} clearly below average` : "No insurer is clearly below average");
  parts.push(above.length ? `${list(above)} ${above.length > 1 ? "are" : "is"} clearly above` : "none is clearly above");
  let s = `Treating each health system as one price-setter: ${parts.join("; ")}.`;
  if (near.length) s += ` ${list(near)} ${near.length > 1 ? "are" : "is"} borderline (the interval only just reaches 1.00).`;
  if (below.includes(names.oscar || "Oscar Health")) s += " Oscar sells mainly marketplace plans with narrower networks.";
  return s + " The other intervals overlap the average, so those differences may be chance.";
}

async function main() {
  const meta = await shell();
  const ins = await loadJSON("data/insurers.json");
  const names = { ...ins.names, ...Object.fromEntries(meta.insurers.map((i) => [i.id, i.name])) };
  const id = param("id");
  const info = meta.insurers.find((i) => i.id === id);
  const attrs = ins.attrs || {};
  const explain = el("p", { class: "muted small" },
    `Fixed-effects comparison: each insurer's prices are compared with other insurers' prices at the same hospital for the same procedure `,
    `(${attrs.n_obs?.toLocaleString?.() ?? ""} prices, ${attrs.n_hospitals} hospitals, ${attrs.n_systems} price-setting systems), so a cheap hospital can't make its insurers look good. `,
    "Lines are 95% intervals that treat each health system as one price-setter (hospitals in one system usually share one price list). ",
    "Rental networks such as MultiPlan are left out: they rent networks, they don't insure.");

  if (!info) {
    content.replaceChildren(
      el("p", { class: "lead" }, "Which insurers negotiate lower prices with Phoenix hospitals, compared fairly at the same hospitals for the same procedures."),
      el("h2", {}, "Commercial plans"), indexChart(ins.index.commercial, names, null), explain,
      el("p", {}, verdict(ins.index.commercial, names)),
      el("h2", {}, "All insurers"),
      table([{ key: "name", label: "Insurer", render: (i) => el("a", { href: `insurer.html?id=${i.id}` }, i.name) },
        { key: "parent", label: "Part of" }, { key: "hospitals", label: "Hospitals with prices", num: true }],
      meta.insurers.filter((i) => !i.rental_network), { caption: "Insurers in the data", sortKey: "hospitals", sortDir: "desc" }));
    return;
  }
  document.title = `${info.name} | Hospital Price Compass`;
  document.getElementById("title").textContent = info.name;
  const b = ins.by_insurer[id] || { plan_types: {}, commercial_vs_phoenix: [], hospitals: [] };
  const nodes = [el("p", { class: "lead" }, info.parent ? `Part of ${info.parent}. ` : "",
    `Prices at ${b.hospitals.length} Phoenix-area hospitals. Plan types: `,
    Object.entries(b.plan_types).sort((x, y) => y[1] - x[1]).map(([t, n]) => `${meta.plan_types[t] || t} (${n})`).join(", "), ".")];
  if (ins.index.commercial.some((r) => r.payer_id === id)) {
    nodes.push(el("h2", {}, "Compared with other insurers (commercial plans)"), indexChart(ins.index.commercial, names, id), explain);
  }
  if (ins.index.medicare_advantage.some((r) => r.payer_id === id)) {
    nodes.push(el("h2", {}, "Medicare Advantage plans"), indexChart(ins.index.medicare_advantage, names, id),
      el("p", { class: "muted small" }, "Medicare Advantage rates cluster close to Medicare's own rates, so insurers differ little here."));
  }
  const rel = b.commercial_vs_phoenix.filter((r) => r.ratio);
  if (rel.length) {
    const procName = Object.fromEntries(meta.procedures.map((p) => [p.id, p.name]));
    const chart = dotStrip(rel.map((r) => ({ label: procName[r.procedure_id] || r.procedure_id,
      points: [{ value: r.ratio, label: `${money(r.median)} vs ${money(r.phoenix_median)} (all insurers)` }] })),
    { log: true, format: times, ref: 1, refLabel: "all insurers", rowHeight: 26,
      xTitle: `${info.name} median commercial price ÷ median of all insurers` });
    const tview = table([{ key: "name", label: "Procedure", render: (r) => procName[r.procedure_id] || r.procedure_id, sortValue: (r) => procName[r.procedure_id] },
      { key: "median", label: info.name, num: true, render: (r) => money(r.median) },
      { key: "phoenix_median", label: "All insurers", num: true, render: (r) => money(r.phoenix_median) },
      { key: "ratio", label: "Ratio", num: true, render: (r) => times(r.ratio) },
      { key: "hospitals", label: "Hospitals", num: true }], rel, { caption: "Where this insurer pays more or less", sortKey: "ratio" });
    nodes.push(el("h2", {}, "Where it pays more or less"),
      el("p", { class: "muted small" }, "Simple medians across hospitals (not adjusted for which hospitals each insurer contracts with); the index above is the fair comparison."),
      withTableView("Where this insurer pays more or less", chart, tview));
  }
  content.replaceChildren(...nodes);
}

main().catch((e) => errorBox(content, e));
