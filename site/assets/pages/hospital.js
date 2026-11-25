import { shell, loadJSON, el, money, times, gradeBadge, table, errorBox, param } from "../common.js";
import { dotStrip, withTableView } from "../charts.js";

const content = document.getElementById("content");
const POINTS = { index_file: ["Index file (cms-hpt.txt)", 10], index_fields: ["Index file fields", 5],
  machine_access: ["Machine-readable price file", 15], cms_format: ["CMS format", 10], freshness: ["Updated within a year", 10],
  data_elements: ["Required data elements", 15], procedure_coverage: ["Covers this site's procedures", 20], data_validity: ["Valid prices", 15] };

async function main() {
  const meta = await shell();
  const hospitals = await loadJSON("data/hospitals.json");
  const h = hospitals.find((x) => x.ccn === param("ccn"));
  if (!h) return list(hospitals);
  document.title = `${h.name} | Hospital Price Compass`;
  document.getElementById("title").textContent = h.name;
  const procName = Object.fromEntries(meta.procedures.map((p) => [p.id, p.name]));

  const nodes = [el("p", { class: "lead" }, `${h.system} · ${h.address}, ${h.city} ${h.zip}`)];
  nodes.push(el("dl", { class: "facts" },
    el("dt", {}, "Type"), el("dd", {}, h.type),
    el("dt", {}, "Ownership (CMS)"), el("dd", {}, h.ownership),
    el("dt", {}, "CMS star rating"), el("dd", {}, h.star ? `${h.star} of 5 (overall quality, from CMS; not part of this site's scoring)` : "not available"),
    el("dt", {}, "Emergency services"), el("dd", {}, h.emergency ? "Yes" : "No"),
    el("dt", {}, "CMS Certification Number"), el("dd", {}, h.ccn)));
  if (!h.in_scope) {
    nodes.push(el("p", { class: "notice" }, `Not compared on this site: ${h.exclusion}.`));
    return content.replaceChildren(...nodes);
  }

  // ---- price level
  if (h.price_index) {
    const rel = h.price_index >= 1 ? `${Math.round((h.price_index - 1) * 100)}% above` : `${Math.round((1 - h.price_index) * 100)}% below`;
    nodes.push(el("h2", {}, "Price level"),
      el("p", {}, `Across ${h.price_index_n} commercial insurance prices, this hospital is typically `, el("strong", {}, rel),
        " the Phoenix median for the same procedure and plan type ", el("span", { class: "muted" }, `(index ${times(h.price_index)})`), "."));
  }
  // ---- prices by procedure, vs Phoenix
  try {
    const hp = await loadJSON(`data/hospital/${h.ccn}.json`);
    const rows = hp.procedures.filter((p) => p.commercial_median && p.phoenix_commercial_median)
      .map((p) => ({ ...p, name: procName[p.procedure_id] || p.procedure_id, ratio: p.commercial_median / p.phoenix_commercial_median }))
      .sort((a, b) => a.ratio - b.ratio);
    if (rows.length) {
      const chart = dotStrip(rows.map((r) => ({ label: r.name, points: [{ value: r.ratio, label: `${money(r.commercial_median)} vs Phoenix median ${money(r.phoenix_commercial_median)}` }] })),
        { log: true, format: times, ref: 1, refLabel: "Phoenix median", xTitle: "This hospital's commercial price ÷ Phoenix median", rowHeight: 26 });
      const tview = table([
        { key: "name", label: "Procedure", render: (r) => el("a", { href: `index.html?p=${r.procedure_id}` }, r.name) },
        { key: "commercial_median", label: "Commercial (median of insurers)", num: true, render: (r) => money(r.commercial_median) },
        { key: "phoenix_commercial_median", label: "Phoenix median", num: true, render: (r) => money(r.phoenix_commercial_median) },
        { key: "ratio", label: "Ratio", num: true, render: (r) => times(r.ratio) },
        { key: "cash", label: "Cash price", num: true, render: (r) => money(r.cash) }], rows, { caption: "Prices by procedure", sortKey: "ratio" });
      nodes.push(el("h2", {}, "Prices by procedure"),
        el("p", { class: "muted small" }, "Left of the line: cheaper than the Phoenix median for commercial plans; right: more expensive."),
        withTableView("Prices by procedure", chart, tview));
    }
  } catch { nodes.push(el("p", { class: "muted" }, "No usable prices from this hospital's file.")); }

  // ---- compliance
  nodes.push(el("h2", {}, "Price-data quality"),
    el("p", {}, gradeBadge(h.grade, h.score), " ", h.score != null ? `Compliance score ${Math.round(h.score)} out of 100.` : "Not scored."),
    el("p", { class: "muted small" }, "This grades the hospital's published price file (is it findable, complete and clean?), not its quality of care."));
  if (Object.keys(h.points || {}).length) {
    nodes.push(table([{ key: "item", label: "Check" }, { key: "got", label: "Points", num: true }, { key: "max", label: "Out of", num: true }],
      Object.entries(POINTS).map(([k, [label, max]]) => ({ item: label, got: h.points[k] == null ? "-" : Math.round(h.points[k] * 10) / 10, max })),
      { caption: "Score breakdown", sortKey: "item" }));
  }
  if (h.findings.length) nodes.push(el("h3", {}, "What we found"), el("ul", { class: "findings" }, h.findings.map((f) => el("li", {}, f))));
  const f = h.file || {};
  nodes.push(el("h3", {}, "The price file"), el("dl", { class: "facts" },
    el("dt", {}, "Format"), el("dd", {}, f.format ? `${f.format.replace(/_/g, " ")}${f.version ? `, version ${f.version}` : ""}` : "not available"),
    el("dt", {}, "File date"), el("dd", {}, f.date || "-"),
    el("dt", {}, "Hospital's price page"), el("dd", {}, f.page ? el("a", { href: f.page, rel: "noopener" }, "Open the hospital's page") : "-")));
  content.replaceChildren(...nodes);
}

function list(hospitals) {
  const rows = hospitals.filter((h) => h.in_scope);
  content.replaceChildren(
    el("p", { class: "lead" }, `${rows.length} acute-care, children's and critical-access hospitals in Maricopa and Pinal counties.`),
    table([
      { key: "name", label: "Hospital", render: (h) => el("a", { href: `hospital.html?ccn=${h.ccn}` }, h.name) },
      { key: "system", label: "System" },
      { key: "city", label: "City" },
      { key: "price_index", label: "Price level (commercial)", num: true, render: (h) => times(h.price_index) },
      { key: "score", label: "Price-data grade", render: (h) => gradeBadge(h.grade, h.score), sortValue: (h) => -(h.score ?? -1) },
    ], rows, { caption: "Hospitals", sortKey: "name" }),
    el("p", { class: "muted small" }, "Price level: 1.00× = the Phoenix median for the same procedure and plan type. ",
      el("a", { href: "about.html" }, "Psychiatric and federal hospitals"), " are not compared."));
}

main().catch((e) => errorBox(content, e));
