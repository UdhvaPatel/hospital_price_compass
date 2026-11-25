import { shell, loadJSON, el, money, times, miles, haversineMiles, gradeBadge, table, errorBox, param } from "../common.js";
import { locatorMap } from "../charts.js";

const $ = (id) => document.getElementById(id);

async function main() {
  const meta = await shell();
  const [hospitals, zips] = await Promise.all([loadJSON("data/hospitals.json"), loadJSON("data/zips.json")]);
  const H = Object.fromEntries(hospitals.map((h) => [h.ccn, h]));
  const procById = Object.fromEntries(meta.procedures.map((p) => [p.id, p]));
  const insName = Object.fromEntries(meta.insurers.map((i) => [i.id, i.name]));

  // ---- procedure select, grouped by category
  const cats = [...new Set(meta.procedures.map((p) => p.category))];
  $("proc").replaceChildren(...cats.map((c) => el("optgroup", { label: c },
    meta.procedures.filter((p) => p.category === c).map((p) => el("option", { value: p.id }, p.name)))));
  // ---- payer select: cash, the five default insurers, then the rest
  const others = meta.insurers.filter((i) => !i.top && !i.rental_network).sort((a, b) => a.name.localeCompare(b.name));
  $("payer").replaceChildren(
    el("option", { value: "cash" }, "Cash (self-pay) price"),
    el("optgroup", { label: "Largest insurers" }, meta.top_insurers.map((id) => el("option", { value: id }, insName[id] || id))),
    el("optgroup", { label: "Other insurers" }, others.map((i) => el("option", { value: i.id }, i.name))));

  // ---- initial state from the URL
  $("proc").value = param("p") || "mri_knee";
  $("payer").value = param("i") || "uhc";
  $("zip").value = param("zip") || "";
  $("sort").value = param("sort") || "price";
  let wantedType = param("t") || "commercial";

  async function update() {
    const pid = $("proc").value, payer = $("payer").value;
    const data = await loadJSON(`data/procedure/${pid}.json`);
    // plan types available for this insurer & procedure
    const types = payer === "cash" ? ["cash"]
      : [...new Set(data.prices.filter((r) => r.payer_id === payer).map((r) => r.plan_type))]
        .sort((a, b) => (a === "commercial" ? -1 : b === "commercial" ? 1 : a.localeCompare(b)));
    $("ptype").replaceChildren(...(types.length ? types : ["none"]).map((t) =>
      el("option", { value: t }, t === "cash" ? "Cash price" : t === "none" ? "No prices for this insurer" : meta.plan_types[t] || t)));
    $("ptype").disabled = payer === "cash" || !types.length;
    $("ptype").value = types.includes(wantedType) ? wantedType : types[0] || "none";
    const ptype = $("ptype").value;

    const params = new URLSearchParams({ p: pid, i: payer, t: ptype });
    if ($("zip").value) params.set("zip", $("zip").value);
    if ($("sort").value !== "price") params.set("sort", $("sort").value);
    history.replaceState(null, "", "?" + params);

    render(meta, data, procById[pid], payer, ptype, H, zips, insName);
  }

  for (const id of ["proc", "payer", "sort"]) $(id).addEventListener("change", () => update().catch((e) => errorBox($("results"), e)));
  $("ptype").addEventListener("change", () => { wantedType = $("ptype").value; update().catch((e) => errorBox($("results"), e)); });
  $("zip").addEventListener("input", () => { if (/^\d{5}$/.test($("zip").value) || !$("zip").value) update(); });
  $("controls").addEventListener("submit", (e) => e.preventDefault());
  await update();
}

function render(meta, data, proc, payer, ptype, H, zips, insName) {
  const out = document.getElementById("results");
  const isCash = payer === "cash";
  const rows = isCash
    ? data.cash.map((r) => ({ ccn: r.ccn, price: r.cash, n_plans: 1 }))
    : data.prices.filter((r) => r.payer_id === payer && r.plan_type === ptype);
  const zip = document.getElementById("zip").value;
  const you = zips[zip] || null;
  const median = (() => { const v = rows.map((r) => r.price).sort((a, b) => a - b); return v.length ? v[Math.floor((v.length - 1) / 2)] / 2 + v[Math.ceil((v.length - 1) / 2)] / 2 : null; })();
  const enriched = rows.map((r) => {
    const h = H[r.ccn] || {};
    return { ...r, name: h.name, system: h.system, star: h.star, grade: h.grade, score: h.score,
      vs_typical: median ? r.price / median : null,
      distance: you && h.lat ? haversineMiles(you, [h.lat, h.lon]) : null, approx: h.location_approx };
  });
  const sortBy = document.getElementById("sort").value === "distance" && you ? "distance" : "price";
  const whoPays = isCash ? "Cash (self-pay) price" : `${insName[payer] || payer} · ${meta.plan_types[ptype] || ptype}`;

  const nodes = [el("h2", {}, `${proc.name} · ${whoPays}`),
    el("p", { class: "muted small" }, `${proc.code_type} ${proc.code} · `, el("a", { href: `procedure.html?id=${proc.id}` }, "All insurers for this procedure"))];

  // summary written from computed numbers (only for the 5 default insurers' commercial plans and cash)
  const view = isCash ? "cash" : ptype === "commercial" ? payer : null;
  const s = view && data.summaries[view];
  if (s) nodes.push(el("p", { class: "summary" }, s.text));
  nodes.push(el("p", { class: "notice" }, meta.safety_notice));

  if (!rows.length) {
    nodes.push(el("p", {}, "No hospital publishes a usable price for this choice. Try another plan type or insurer, or the cash price."));
    out.replaceChildren(...nodes);
    return;
  }
  const prices = rows.map((r) => r.price);
  const systems = new Set(enriched.map((r) => r.system === "Independent" ? r.name : r.system));
  const tiles = [
    ["Median price", money(median), `across ${rows.length} hospitals`],
    ["Lowest", money(Math.min(...prices)), ""],
    ["Highest", money(Math.max(...prices)), ""],
    ["Hospitals", String(rows.length), `in ${systems.size} health systems`],
  ];
  const mm = rows.map((r) => r.medicare_multiple).filter((v) => v != null).sort((a, b) => a - b);
  if (mm.length >= 2) tiles.push(["Median vs Medicare", times(mm[Math.floor(mm.length / 2)]), "the Medicare base rate for this stay"]);
  nodes.push(el("div", { class: "tiles" }, tiles.map(([l, v, sub]) =>
    el("div", { class: "tile" }, el("div", { class: "label" }, l), el("div", { class: "value" }, v), sub ? el("div", { class: "sub" }, sub) : ""))));
  if (zip && !you) nodes.push(el("p", { class: "notice" }, `ZIP ${zip} isn't in the Phoenix-area list; distances aren't shown.`));

  const maxPrice = Math.max(...prices);
  const cols = [
    { key: "name", label: "Hospital", render: (r) => el("span", {}, el("a", { href: `hospital.html?ccn=${r.ccn}` }, r.name),
      el("br"), el("span", { class: "muted small" }, r.system)) },
    { key: "price", label: "Price", num: true, render: (r) => el("div", { class: "bar-cell" },
      el("div", { class: "bar", style: `width:${Math.max(2, (70 * r.price) / maxPrice)}px`, "aria-hidden": "true" }),
      el("span", { class: "val" }, money(r.price))) },
    { key: "n_plans", label: "Plans", num: true, render: (r) => r.n_plans > 1 ? el("span", { title: `${money(r.plan_min)} to ${money(r.plan_max)}` },
      `${r.n_plans} (${money(r.plan_min)}-${money(r.plan_max)})`) : "1" },
    proc.family === "drg" && !isCash
      ? { key: "medicare_multiple", label: "vs Medicare", num: true, render: (r) => times(r.medicare_multiple) }
      : { key: "vs_typical", label: "vs median", num: true, render: (r) => times(r.vs_typical) },
  ];
  if (you) cols.push({ key: "distance", label: "Distance", num: true, render: (r) => r.distance == null ? "-" : miles(r.distance) + (r.approx ? "*" : "") });
  cols.push({ key: "star", label: "CMS stars", num: true, render: (r) => r.star ? `${r.star} / 5` : "n/a" },
    { key: "score", label: "Price-data grade", render: (r) => gradeBadge(r.grade, r.score), sortValue: (r) => -(r.score ?? -1) });

  const tableNode = table(cols, enriched, { caption: `${rows.length} hospitals, ${sortBy === "distance" ? "nearest first" : "lowest price first"}`, sortKey: sortBy });
  const mapNode = locatorMap(enriched.map((r) => ({ lat: H[r.ccn]?.lat, lon: H[r.ccn]?.lon, value: r.price, label: r.name,
    href: `hospital.html?ccn=${r.ccn}`, approx: r.approx })), { you });
  nodes.push(el("div", {},
    el("section", { "aria-label": "Ranked hospitals" }, tableNode,
      el("p", { class: "muted small" }, "Plans: when an insurer lists several plans of this type, the price shown is their median; the range is in brackets. ",
        "\"vs median\" compares with the middle price in this list. ",
        you ? "* location approximate (ZIP centre). " : "",
        "Grade = how complete and clean the hospital's price file is, not the quality of care.")),
    el("section", { "aria-label": "Map", class: "card map-card" }, el("h3", {}, "Where they are"), mapNode)));

  // hospitals missing from this comparison, so absence isn't mistaken for "no charge"
  const shown = new Set(rows.map((r) => r.ccn));
  const missing = Object.values(H).filter((h) => h.in_scope && !shown.has(h.ccn));
  if (missing.length) {
    const why = (h) => (h.status === "usable" && h.grade === "not scored") ? "price file not processed in this update" : ({
      usable: "no usable price for this choice", blocked: "price file blocks automated access",
      not_listed: "price file not found for this hospital", not_found: "no price file found" }[h.status] || h.status);
    nodes.push(el("details", {}, el("summary", {}, `${missing.length} hospitals not in this comparison, and why`),
      el("ul", {}, missing.sort((a, b) => a.name.localeCompare(b.name)).map((h) =>
        el("li", {}, el("a", { href: `hospital.html?ccn=${h.ccn}` }, h.name), `: ${why(h)}`)))));
  }
  out.replaceChildren(...nodes);
}

main().catch((e) => errorBox(document.getElementById("results"), e));
