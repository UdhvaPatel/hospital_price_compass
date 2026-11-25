import { shell, loadJSON, el, money, times, pct, table, errorBox, param } from "../common.js";
import { dotStrip, withTableView } from "../charts.js";

const content = document.getElementById("content");

async function main() {
  const meta = await shell();
  const id = param("id");
  if (!id || !meta.procedures.find((p) => p.id === id)) return list(meta);
  const proc = meta.procedures.find((p) => p.id === id);
  const [data, hospitals] = await Promise.all([loadJSON(`data/procedure/${id}.json`), loadJSON("data/hospitals.json")]);
  const H = Object.fromEntries(hospitals.map((h) => [h.ccn, h]));
  const name = Object.fromEntries(meta.insurers.map((i) => [i.id, i.name]));
  document.title = `${proc.name} | Hospital Price Compass`;
  document.getElementById("title").textContent = proc.name;

  const nodes = [el("p", { class: "lead" }, `${proc.code_type} ${proc.code} · ${proc.category}. `,
    el("a", { href: `index.html?p=${id}` }, "Compare hospitals for this procedure"), "."),
  el("p", { class: "notice" }, meta.safety_notice)];

  // ---- distribution: one row per default insurer (commercial) + cash
  const views = [...meta.top_insurers.map((pid) => ({ key: pid, label: name[pid] || pid,
    rows: data.prices.filter((r) => r.payer_id === pid && r.plan_type === "commercial") })),
  { key: "cash", label: "Cash price", rows: data.cash.map((r) => ({ ccn: r.ccn, price: r.cash })) }].filter((v) => v.rows.length);
  const med = (a) => { const v = a.map((r) => r.price).sort((x, y) => x - y); return v.length ? (v[Math.floor((v.length - 1) / 2)] + v[Math.ceil((v.length - 1) / 2)]) / 2 : null; };
  const chart = dotStrip(views.map((v) => ({ label: v.label, median: med(v.rows),
    points: v.rows.map((r) => ({ value: r.price, label: H[r.ccn]?.name || r.ccn })) })),
  { xTitle: "Price at each hospital (commercial plans; cash for the last row)" });
  // table view: hospitals x views
  const ccns = [...new Set(views.flatMap((v) => v.rows.map((r) => r.ccn)))];
  const trows = ccns.map((c) => Object.fromEntries([["name", H[c]?.name || c], ["ccn", c],
    ...views.map((v) => [v.key, v.rows.find((r) => r.ccn === c)?.price ?? null])]));
  const tview = table([{ key: "name", label: "Hospital", render: (r) => el("a", { href: `hospital.html?ccn=${r.ccn}` }, r.name) },
    ...views.map((v) => ({ key: v.key, label: v.label, num: true, render: (r) => money(r[v.key]) }))], trows,
  { caption: "Price at each hospital", sortKey: "name" });
  nodes.push(el("h2", {}, "How prices spread across hospitals"),
    el("p", { class: "muted small" }, "Each dot is a hospital price; a number above a dot counts hospitals sharing that exact price (often one system's price list). The vertical tick is the median."),
    withTableView("Price distribution", chart, tview));

  // ---- summaries per view
  nodes.push(el("h2", {}, "In plain English"));
  const cards = views.filter((v) => data.summaries[v.key]).map((v) => el("div", { class: "card" },
    el("h3", {}, v.label), el("p", {}, data.summaries[v.key].text)));
  nodes.push(el("div", { class: "grid two" }, cards));

  // ---- Medicare comparison (hospital stays)
  if (proc.family === "drg" && data.medicare_rate) {
    const byType = {};
    for (const r of data.prices) if (r.medicare_multiple != null) (byType[r.plan_type] ||= []).push(r.medicare_multiple);
    const rows = Object.entries(byType).filter(([, v]) => v.length >= 3).map(([t, v]) => {
      v.sort((a, b) => a - b); return { type: meta.plan_types[t] || t, n: v.length, median: v[Math.floor(v.length / 2)] };
    });
    nodes.push(el("h2", {}, "Compared with Medicare"),
      el("p", {}, `Medicare's base rate for this stay in Phoenix is about ${money(data.medicare_rate)} (FY 2026, before add-ons for teaching, low-income patients or outliers).`),
      table([{ key: "type", label: "Plan type" }, { key: "median", label: "Median multiple of Medicare", num: true, render: (r) => times(r.median) },
        { key: "n", label: "Prices", num: true }], rows, { caption: "Negotiated price as a multiple of Medicare", sortKey: "median" }));
  }

  // ---- cash vs insured
  const cv = data.cash_vs_insured;
  if (cv && cv.pairs >= 3) {
    nodes.push(el("h2", {}, "Cash or insurance?"),
      el("p", {}, `At ${pct(cv.share_cash_below_median_insured)} of the ${cv.pairs} hospitals that publish both, the cash price is below the typical commercial insured price `,
        `(median cash ${money(cv.median_cash)} vs median insured ${money(cv.median_insured)}). `,
        "Paying cash may not count toward your deductible; check with your insurer first."));
  }
  content.replaceChildren(...nodes);
}

function list(meta) {
  const cats = [...new Set(meta.procedures.map((p) => p.category))];
  content.replaceChildren(el("p", { class: "lead" }, `${meta.procedures.length} common procedures, chosen from CMS's list of shoppable services.`),
    ...cats.map((c) => el("section", {}, el("h2", {}, c), el("ul", {}, meta.procedures.filter((p) => p.category === c).map((p) =>
      el("li", {}, el("a", { href: `procedure.html?id=${p.id}` }, p.name), el("span", { class: "muted small" }, ` · ${p.code_type} ${p.code}`)))))));
}

main().catch((e) => errorBox(content, e));
