import { shell, loadJSON, el, money, times, gradeBadge, table, errorBox, param, hero, icon, infoTip, fold } from "../common.js";
const level = (r) => r >= 1.03 ? `${Math.round((r - 1) * 100)}% more` : r <= 0.97 ? `${Math.round((1 - r) * 100)}% less` : "about the same";
import { dotStrip, withTableView } from "../charts.js";
import { reveal } from "../motion.js";

const content = document.getElementById("content");
const POINTS = { index_file: ["Says where its price list is (cms-hpt.txt)", 10], index_fields: ["That pointer is filled in properly", 5],
  machine_access: ["Price list can be downloaded", 15], cms_format: ["Uses the official layout", 10], freshness: ["Updated within the last year", 10],
  data_elements: ["Includes everything required", 15], procedure_coverage: ["Has prices for this site's procedures", 20], data_validity: ["Prices look real", 15] };

async function main() {
  const meta = await shell();
  const hospitals = await loadJSON("data/hospitals.json");
  const h = hospitals.find((x) => x.ccn === param("ccn"));
  if (!h) return list(hospitals);
  document.title = `${h.name} | Hospital Price Compass`;
  hero({ eyebrow: h.system, title: h.name, lead: [`${h.address}, ${h.city} ${h.zip}. `, h.price_index ? `Overall, its insurance prices are ${level(h.price_index)} than the usual Phoenix price.` : ""] });
  const procName = Object.fromEntries(meta.procedures.map((p) => [p.id, p.name]));
  const nodes = [];

  const facts = el("dl", { class: "facts" },
    el("dt", {}, "Type"), el("dd", {}, h.type),
    el("dt", {}, "Owned by"), el("dd", {}, h.ownership),
    el("dt", {}, "Medicare star rating"), el("dd", {}, h.star ? `★ ${h.star} of 5 ` : "not available", h.star ? infoTip("Medicare's overall rating of the hospital's quality of care. We show it for context; it doesn't affect anything else on this site.") : ""),
    el("dt", {}, "Emergency room"), el("dd", {}, h.emergency ? "Yes" : "No"),
    el("dt", {}, "Medicare ID"), el("dd", {}, h.ccn));
  if (!h.in_scope) {
    nodes.push(el("section", { "data-reveal": "" }, el("div", { class: "card" }, facts, el("p", { class: "notice" }, icon("info", 20), el("span", {}, `We don't compare this hospital: ${h.exclusion}.`)))));
    content.replaceChildren(...nodes); reveal(content); return;
  }

  // ---- headline cards: price level + grade
  const tiles = [];
  if (h.price_index) {
    const pctDiff = Math.round(Math.abs(h.price_index - 1) * 100);
    tiles.push(el("div", { class: "card stat" }, el("div", { class: "label" }, "Compared with other Phoenix hospitals ", infoTip("We compare each of its insurance prices (plans through work or bought yourself) with the usual Phoenix price for the same procedure, then average.")),
      el("div", { class: "value grad" }, h.price_index >= 1 ? `+${pctDiff}%` : `−${pctDiff}%`),
      el("div", { class: "label" }, `${h.price_index >= 1 ? "more expensive" : "cheaper"} than usual (based on ${h.price_index_n} prices)`)));
  }
  tiles.push(el("div", { class: "card stat" }, el("div", { class: "label" }, "Price list quality ", infoTip("How complete and clean the hospital's published price list is. It is not a rating of medical care.")),
    el("div", { class: "value" }, h.score != null ? String(Math.round(h.score)) : "–", el("span", { class: "muted", style: "font-size:1rem" }, h.score != null ? " / 100" : "")),
    el("div", {}, gradeBadge(h.grade, h.score))));
  tiles.push(el("div", { class: "card stat" }, facts));
  nodes.push(el("section", {}, el("div", { class: "grid three", "data-stagger": "" }, tiles)));

  // ---- prices by procedure vs Phoenix
  try {
    const hp = await loadJSON(`data/hospital/${h.ccn}.json`);
    const rows = hp.procedures.filter((p) => p.commercial_median && p.phoenix_commercial_median)
      .map((p) => ({ ...p, name: procName[p.procedure_id] || p.procedure_id, ratio: p.commercial_median / p.phoenix_commercial_median }))
      .sort((a, b) => a.ratio - b.ratio);
    if (rows.length) {
      const chart = dotStrip(rows.map((r) => ({ label: r.name, points: [{ value: r.ratio, label: `${money(r.commercial_median)} here vs usual ${money(r.phoenix_commercial_median)}` }] })),
        { log: true, format: times, ref: 1, refLabel: "usual Phoenix price", xTitle: "This hospital's price compared with the usual Phoenix price", rowHeight: 26 });
      const tview = table([
        { key: "name", label: "Procedure", render: (r) => el("a", { href: `index.html?p=${r.procedure_id}` }, r.name) },
        { key: "commercial_median", label: "Here (insurance)", num: true, render: (r) => money(r.commercial_median) },
        { key: "phoenix_commercial_median", label: "Usual in Phoenix", num: true, render: (r) => money(r.phoenix_commercial_median) },
        { key: "ratio", label: "Compared", render: (r) => level(r.ratio) },
        { key: "cash", label: "Paying yourself", num: true, render: (r) => money(r.cash) }], rows, { caption: "Prices by procedure", sortKey: "ratio" });
      nodes.push(el("section", { "data-reveal": "" }, el("p", { class: "eyebrow" }, "Procedure by procedure"), el("h2", {}, "Is it cheaper or more expensive than usual?"),
        el("p", { class: "muted small" }, "Dots left of the line: cheaper than the usual Phoenix price. Right of the line: more expensive."),
        el("div", { class: "card" }, withTableView("Prices by procedure", chart, tview))));
    }
  } catch { nodes.push(el("p", { class: "muted" }, "We couldn't use any prices from this hospital's price list.")); }

  // ---- compliance details
  const qual = [el("p", { class: "eyebrow" }, "Price list quality"), el("h2", {}, "How good is its published price list?")];
  const qcard = el("div", { class: "card" });
  if (Object.keys(h.points || {}).length) qcard.append(table([{ key: "item", label: "What we check" }, { key: "got", label: "Points", num: true }, { key: "max", label: "Out of", num: true }],
    Object.entries(POINTS).map(([k, [label, max]]) => ({ item: label, got: h.points[k] == null ? "-" : Math.round(h.points[k] * 10) / 10, max })), { caption: "How the score adds up", sortKey: "item" }));
  if (h.findings.length) qcard.append(el("h3", { style: "margin-top:1.25rem" }, "What we found"), el("ul", { class: "findings" }, h.findings.map((f) => el("li", {}, f))));
  const f = h.file || {};
  qcard.append(fold("Details for the curious: the price list file", el("dl", { class: "facts" },
    el("dt", {}, "Format"), el("dd", {}, f.format ? `${f.format.replace(/_/g, " ")}${f.version ? `, version ${f.version}` : ""}` : "not available"),
    el("dt", {}, "File date"), el("dd", {}, f.date || "-"),
    el("dt", {}, "Hospital's price page"), el("dd", {}, f.page ? el("a", { href: f.page, rel: "noopener" }, "Open the hospital's page") : "-"))));
  nodes.push(el("section", { "data-reveal": "" }, ...qual, qcard));
  content.replaceChildren(...nodes);
  reveal(content);
}

function list(hospitals) {
  const rows = hospitals.filter((h) => h.in_scope);
  hero({ eyebrow: "Hospitals", title: "Which hospitals cost more?", lead: `${rows.length} Phoenix-area hospitals (Maricopa and Pinal counties), with how their prices compare with the usual Phoenix price and how good their published price list is.` });
  content.replaceChildren(el("section", { "data-reveal": "" },
    table([
      { key: "name", label: "Hospital", render: (h) => el("span", {}, el("a", { href: `hospital.html?ccn=${h.ccn}` }, h.name), el("br"), el("span", { class: "muted small" }, h.city)) },
      { key: "system", label: "Hospital group" },
      { key: "price_index", label: "Prices vs usual", num: true, render: (h) => h.price_index ? level(h.price_index) : "–" },
      { key: "score", label: "Price list", render: (h) => gradeBadge(h.grade, h.score), sortValue: (h) => -(h.score ?? -1) },
    ], rows, { caption: "Hospitals", sortKey: "name" }),
    el("p", { class: "muted small" }, "Prices vs usual: its insurance prices compared with the usual Phoenix price for the same procedures. ",
      el("a", { href: "about.html" }, "Psychiatric and federal hospitals"), " aren't compared.")));
  reveal(content);
}

main().catch((e) => errorBox(content, e));
