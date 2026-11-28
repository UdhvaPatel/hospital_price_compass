import { shell, loadJSON, el, money, miles, haversineMiles, gradeBadge, table, errorBox, param, icon, infoTip,
  FRIENDLY, procPhrase, planWords, vsUsual, fold, plainText } from "../common.js";
import { locatorMap, rangeBar } from "../charts.js";
import { swap, reveal, countAll } from "../motion.js";

const $ = (id) => document.getElementById(id);
const POPULAR = [
  ["mri_knee", "Knee MRI"], ["colonoscopy", "Colonoscopy"], ["er_visit_level_4", "ER visit"],
  ["mammogram_screening", "Mammogram"], ["ct_head", "Head CT"], ["vaginal_delivery", "Giving birth"],
];
const SHORT = { uhc: "UnitedHealthcare", bcbs_az: "Blue Cross AZ", aetna: "Aetna", cigna: "Cigna", humana: "Humana" };
const state = { proc: "mri_knee", mode: "ins", payer: "uhc", ptype: "commercial", zip: "", sort: "price", view: "cards" };
const usual = (vals) => { const v = [...vals].sort((a, b) => a - b), i = (v.length - 1) / 2; return (v[Math.floor(i)] + v[Math.ceil(i)]) / 2; };
const pctl = (vals, f) => { const v = [...vals].sort((a, b) => a - b), i = (v.length - 1) * f, lo = Math.floor(i), hi = Math.ceil(i); return v[lo] + (v[hi] - v[lo]) * (i - lo); };

/** "Abrazo Central Campus" or, when several share the price, "7 HonorHealth hospitals". */
function whereText(rows, price, H) {
  const at = rows.filter((r) => Math.abs(r.price - price) < 0.005).map((r) => H[r.ccn]);
  if (at.length === 1) return at[0]?.name || "one hospital";
  const sys = new Set(at.map((h) => h?.system));
  return sys.size === 1 && !sys.has("Independent") ? `${at.length} ${[...sys][0]} hospitals` : `${at.length} hospitals`;
}

async function main() {
  const meta = await shell();
  const [hospitals, zips] = await Promise.all([loadJSON("data/hospitals.json"), loadJSON("data/zips.json")]);
  const H = Object.fromEntries(hospitals.map((h) => [h.ccn, h]));
  const procById = Object.fromEntries(meta.procedures.map((p) => [p.id, p]));
  const insName = Object.fromEntries(meta.insurers.map((i) => [i.id, i.name]));
  const pi = param("i");
  Object.assign(state, {
    proc: procById[param("p")] ? param("p") : state.proc,
    mode: pi === "cash" ? "self" : "ins", payer: pi && pi !== "cash" ? pi : state.payer,
    ptype: param("t") || state.ptype, zip: param("zip") || "",
    sort: param("sort") === "distance" ? "distance" : "price", view: param("view") === "table" ? "table" : "cards",
  });

  await renderHero(meta, H, procById, insName);
  renderWho();

  // ---- question 1: what do you need?
  const cats = [...new Set(meta.procedures.map((p) => p.category))];
  $("proc").replaceChildren(...cats.map((c) => el("optgroup", { label: c },
    meta.procedures.filter((p) => p.category === c).map((p) => el("option", { value: p.id }, p.name)))));
  $("proc").value = state.proc;
  for (const [id, label] of POPULAR) {
    if (!procById[id]) continue;
    const c = el("button", { type: "button", class: "chip", "data-proc": id }, label);
    c.addEventListener("click", () => { state.proc = id; $("proc").value = id; update(); });
    $("popular").append(c);
  }
  $("proc").addEventListener("change", () => { state.proc = $("proc").value; update(); });

  // ---- question 2: how are you paying?
  $("pay-ins").addEventListener("click", () => { state.mode = "ins"; update(); });
  $("pay-self").addEventListener("click", () => { state.mode = "self"; update(); });
  const others = meta.insurers.filter((i) => !i.top && !i.rental_network).sort((a, b) => a.name.localeCompare(b.name));
  const chip = (id, label) => {
    const c = el("button", { type: "button", class: "chip", "data-payer": id }, label);
    c.addEventListener("click", () => { state.payer = id; update(); });
    return c;
  };
  const otherSel = el("select", { id: "other", "aria-label": "Another insurance company", style: "width:auto;min-height:2.3rem;padding:.35rem .7rem;font-size:.88rem;border-radius:999px" },
    el("option", { value: "" }, "Another company…"), others.map((i) => el("option", { value: i.id }, i.name)));
  otherSel.addEventListener("change", () => { if (otherSel.value) { state.payer = otherSel.value; update(); } });
  $("payer-chips").replaceChildren(...meta.top_insurers.map((id) => chip(id, SHORT[id] || insName[id])), otherSel);

  $("zip").value = state.zip;
  $("zip").addEventListener("input", () => { const z = $("zip").value; if (/^\d{5}$/.test(z) || !z) { state.zip = z; update(); } });
  $("finder").addEventListener("submit", (e) => e.preventDefault());
  stickyCta();

  let first = true;
  async function update() {
    const data = await loadJSON(`data/procedure/${state.proc}.json`);
    const types = state.mode === "self" ? [] : [...new Set(data.prices.filter((r) => r.payer_id === state.payer).map((r) => r.plan_type))]
      .sort((a, b) => (a === "commercial" ? -1 : b === "commercial" ? 1 : a.localeCompare(b)));
    if (state.mode === "ins" && !types.includes(state.ptype)) state.ptype = types[0] || "commercial";
    syncControls(meta, types, insName);
    const params = new URLSearchParams({ p: state.proc, i: state.mode === "self" ? "cash" : state.payer });
    if (state.mode === "ins") params.set("t", state.ptype);
    if (state.zip) params.set("zip", state.zip);
    if (state.sort !== "price") params.set("sort", state.sort);
    if (state.view !== "cards") params.set("view", state.view);
    history.replaceState(null, "", "?" + params + location.hash);
    const build = () => results(meta, data, procById[state.proc], H, zips, insName, update);
    if (first) { $("results").replaceChildren(...build()); reveal($("results")); countAll($("results")); first = false; }
    else await swap($("results"), build);
  }
  renderTrust(meta);
  await update();
}

// ------------------------------------------------------------------ hero: a real example, from today's data
async function renderHero(meta, H, procById, insName) {
  let pick = null;
  for (const [proc, payer] of [["mri_knee", "uhc"], ["ct_head", "aetna"], ["colonoscopy", "bcbs_az"], ["mammogram_screening", "uhc"]]) {
    if (!procById[proc]) continue;
    const d = await loadJSON(`data/procedure/${proc}.json`);
    const rows = d.prices.filter((r) => r.payer_id === payer && r.plan_type === "commercial");
    if (rows.length >= 5) { pick = { proc, payer, rows }; break; }
  }
  if (!pick) return;
  const vals = pick.rows.map((r) => r.price);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const [phrase, noun] = FRIENDLY.proc[pick.proc] || [procById[pick.proc].name, "procedure"];
  $("title").replaceChildren(`The same ${phrase.replace(/^an? /, "")} costs `, el("span", { class: "grad price" }, money(lo)),
    " at one Phoenix hospital and ", el("span", { class: "grad price" }, money(hi)), " at another.");
  $("tagline").textContent = `Same insurance. Same ${noun}.`;
  const name = insName[pick.payer] || pick.payer;
  const card = $("example");
  card.replaceChildren(
    el("div", { class: "ex-head" }, el("div", {}, el("div", { class: "ex-title" }, `${procById[pick.proc].name}`),
      el("div", { class: "ex-sub" }, `${name} · insurance through work or bought yourself`)),
      el("span", { class: "tag quiet" }, `${pick.rows.length} hospitals`)),
    rangeBar(pick.rows.map((r) => ({ value: r.price, label: H[r.ccn]?.name || r.ccn })), { compact: true }),
    el("div", { class: "ex-foot" },
      el("div", {}, el("b", {}, money(lo)), `Lowest: ${whereText(pick.rows, lo, H)}`),
      el("div", {}, el("b", {}, money(hi)), `Highest: ${whereText(pick.rows, hi, H)}`)),
    el("p", { class: "ex-gap" }, "That's a difference of ", el("strong", {}, money(hi - lo)), ` for the same ${noun}. `,
      el("a", { href: `?p=${pick.proc}&i=${pick.payer}&t=commercial#results` }, "See every hospital →")));
}

function renderWho() {
  const who = [
    ["calendar", "Planning a scan, surgery or birth", "Compare hospitals before you book. The same procedure can cost several times more across town."],
    ["card", "Have a high deductible?", "Until you reach your deductible, you pay most of the price yourself, so the hospital you choose really matters."],
    ["heart", "No insurance?", "See each hospital's self-pay price so you know what to expect, and what to ask for, before you go."],
  ];
  $("who-cards").replaceChildren(...who.map(([ic, title, text]) => el("div", { class: "card lift" },
    el("div", { class: "cat-icon" }, icon(ic === "calendar" ? "pin" : ic, 20)), el("h3", {}, title), el("p", { class: "muted" }, text))));
  reveal($("who-cards").parentElement);
}

function renderTrust(meta) {
  $("trust").replaceChildren(
    el("div", {}, el("div", { class: "big-n grad", "data-count": meta.counts.good_prices }, meta.counts.good_prices.toLocaleString("en-US")),
      el("div", { class: "muted small" }, "prices checked and kept")),
    el("p", { style: "flex:1;min-width:16rem;margin:0" }, "Every price comes from the hospital's own published price list. ",
      "We remove the broken ones (like $1 placeholders) and double-check a sample against the original files."),
    el("a", { class: "btn", href: "quality.html" }, "Can I trust this? ", icon("arrow", 16)));
}

function stickyCta() {
  const cta = $("sticky-cta"), check = $("check");
  if (!("IntersectionObserver" in window)) return;
  let heroGone = false, checkVisible = false;
  const set = () => cta.classList.toggle("show", heroGone && !checkVisible);
  new IntersectionObserver(([e]) => { heroGone = !e.isIntersecting; set(); }).observe(document.querySelector(".hero"));
  new IntersectionObserver(([e]) => { checkVisible = e.isIntersecting; set(); }, { threshold: 0.05 }).observe(check);
}

// ------------------------------------------------------------------ controls
function syncControls(meta, types, insName) {
  document.querySelectorAll("[data-proc]").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.proc === state.proc)));
  $("pay-ins").setAttribute("aria-pressed", String(state.mode === "ins"));
  $("pay-self").setAttribute("aria-pressed", String(state.mode === "self"));
  $("ins-questions").hidden = state.mode !== "ins";
  document.querySelectorAll("[data-payer]").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.payer === state.payer)));
  const other = $("other");
  if (other) other.value = meta.top_insurers.includes(state.payer) ? "" : state.payer;
  const row = $("plan-row");
  if (state.mode !== "ins") { row.replaceChildren(); return; }
  if (!types.length) { row.replaceChildren(el("p", { class: "small muted" }, `${insName[state.payer] || "This company"} hasn't published a price for this yet.`)); return; }
  const sel = el("select", { id: "ptype", "aria-labelledby": "kind-label" }, types.map((t) => el("option", { value: t }, planWords(t, meta.plan_types))));
  sel.value = state.ptype;
  sel.addEventListener("change", () => { state.ptype = sel.value; $("proc").dispatchEvent(new Event("change")); });
  row.replaceChildren(el("div", { class: "lbl", id: "kind-label" }, "What kind of insurance? ",
    infoTip("The same company sells different kinds of plans, and each pays the hospital a different price. Most people under 65 have insurance through work or bought it themselves.", "Why does this matter?")), sel);
}

// ------------------------------------------------------------------ the answer
function results(meta, data, proc, H, zips, insName, update) {
  const self = state.mode === "self";
  const rows = self ? data.cash.map((r) => ({ ccn: r.ccn, price: r.cash, n_plans: 1 }))
    : data.prices.filter((r) => r.payer_id === state.payer && r.plan_type === state.ptype);
  const phrase = procPhrase(proc.id, proc.name);
  const company = insName[state.payer] || state.payer;
  if (!rows.length) {
    return [el("div", { class: "card", "data-reveal": "" }, el("h3", {}, "No published prices for this choice yet"),
      el("p", { class: "muted" }, self ? "Hospitals haven't published a self-pay price for this." :
        `${company} hasn't published a price for ${phrase} with this kind of insurance. Try another kind of insurance, another company, or "I'll pay myself".`))];
  }
  const vals = rows.map((r) => r.price);
  const mid = usual(vals), lo = Math.min(...vals), hi = Math.max(...vals);
  const you = zips[state.zip] || null;
  const enriched = rows.map((r) => {
    const h = H[r.ccn] || {};
    return { ...r, name: h.name, system: h.system, star: h.star, grade: h.grade, score: h.score, approx: h.location_approx,
      rel: r.price / mid, distance: you && h.lat ? haversineMiles(you, [h.lat, h.lon]) : null };
  });
  const who = self ? "If you pay yourself" : `With ${company}`;
  const nodes = [];

  // ---- the answer in one sentence
  nodes.push(el("div", { "data-reveal": "" },
    el("p", { class: "eyebrow" }, "Your answer"),
    el("p", { class: "headline" }, `${who}, ${phrase} usually costs about `,
      el("span", { class: "amt grad", "data-count": mid.toFixed(2), "data-format": "money" }, money(mid)), " at a Phoenix hospital."),
    el("p", { class: "subline" }, "Prices range from ", el("strong", {}, money(lo)), ` (${whereText(rows, lo, H)}) to `,
      el("strong", {}, money(hi)), ` (${whereText(rows, hi, H)}).`,
      vals.length >= 3 ? [" Most hospitals charge between ", el("strong", {}, money(pctl(vals, 0.1))), " and ", el("strong", {}, money(pctl(vals, 0.9))), "."] : "",
      ` Based on prices published by ${rows.length} hospitals.`)));
  nodes.push(el("div", { class: "card", style: "margin-top:1rem", "data-reveal": "" },
    rangeBar(enriched.map((r) => ({ value: r.price, label: r.name })))));

  // ---- what will I pay + what to do next
  const payText = self
    ? ["This is the hospital's published price if you pay yourself. Many hospitals also offer payment plans or financial help: ask before your visit."]
    : [`This is the total price ${company} agreed to pay the hospital. You usually pay part of it: until you reach your deductible you may pay all of it, `,
      "after that a share (coinsurance) or a flat copay. ", el("strong", {}, `${company} can tell you your exact share.`)];
  const steps = el("ol", { class: "next-steps" },
    el("li", {}, el("div", {}, el("b", {}, "Pick a hospital"), " from the list below: compare price, distance and your own preferences.")),
    el("li", {}, el("div", {}, el("b", {}, self ? "Call the hospital's billing office" : `Call ${company}`), self ? " and ask for the self-pay price for this billing code:" : " and ask what your share would be. Give them this billing code:",
      el("div", {}, codePill(proc.code)))),
    el("li", {}, el("div", {}, el("b", {}, "Ask the hospital for a written estimate"), " before you book. You have a right to one if you're paying yourself.")));
  nodes.push(el("div", { class: "pay-box", style: "margin-top:1rem", "data-reveal": "" },
    el("div", { class: "card" }, el("h3", {}, icon("card", 20), "What will I actually pay?"), el("p", { class: "muted" }, ...payText),
      fold("Why your bill may differ", el("p", { class: "muted small" }, meta.safety_notice))),
    el("div", { class: "card" }, el("h3", {}, icon("check", 20), "What to do next"), steps)));

  // ---- every hospital
  const sortBy = state.sort === "distance" && you ? "distance" : "price";
  const sorted = [...enriched].sort((a, b) => sortBy === "distance" ? (a.distance ?? 1e9) - (b.distance ?? 1e9) : a.price - b.price);
  const closest = you ? [...enriched].filter((r) => r.distance != null).sort((a, b) => a.distance - b.distance)[0]?.ccn : null;
  const seg = (name, opts) => el("div", { class: "seg", role: "group", "aria-label": name }, opts.map(([value, label, key]) => {
    const b = el("button", { type: "button", "aria-pressed": String(state[key] === value) }, label);
    b.addEventListener("click", () => { state[key] = value; update(); });
    return b;
  }));
  nodes.push(el("div", { class: "toolbar", style: "margin-top:2.25rem", "data-reveal": "" },
    el("div", {}, el("h2", { style: "margin:0" }, sortBy === "distance" ? "Every hospital, closest first" : "Every hospital, cheapest first"),
      el("p", { class: "muted small", style: "margin:.2rem 0 0" }, `${rows.length} hospitals · "price list" shows how complete each hospital's published prices are (not the quality of care).`)),
    el("div", { style: "display:flex;gap:.5rem;flex-wrap:wrap" },
      you ? seg("Sort by", [["price", "Cheapest", "sort"], ["distance", "Closest", "sort"]]) : "",
      seg("View", [["cards", "Cards", "view"], ["table", "Table", "view"]]))));
  const maxPrice = hi;
  if (state.view === "table") {
    const cols = [
      { key: "name", label: "Hospital", render: (r) => el("span", {}, el("a", { href: `hospital.html?ccn=${r.ccn}` }, r.name), el("br"), el("span", { class: "muted small" }, r.system)) },
      { key: "price", label: "Price", num: true, render: (r) => el("div", { class: "bar-cell" }, el("div", { class: "bar", style: `width:${Math.max(4, (70 * r.price) / maxPrice)}px`, "aria-hidden": "true" }), el("span", { class: "val" }, money(r.price))) },
      { key: "rel", label: "Compared with the usual price", render: (r) => vsUsual(r.rel) },
    ];
    if (you) cols.push({ key: "distance", label: "Distance", num: true, render: (r) => (r.distance == null ? "-" : miles(r.distance) + (r.approx ? "*" : "")) });
    cols.push({ key: "star", label: "Medicare star rating", num: true, render: (r) => (r.star ? `${r.star} / 5` : "n/a") },
      { key: "score", label: "Price list", render: (r) => gradeBadge(r.grade, r.score), sortValue: (r) => -(r.score ?? -1) });
    nodes.push(el("div", { "data-reveal": "" }, table(cols, enriched, { caption: `${rows.length} hospitals`, sortKey: sortBy })));
  } else {
    nodes.push(el("ol", { class: "hlist" }, sorted.map((r, i) => el("li", { class: `hcard card lift${r.price === lo ? " first" : ""}`, "data-reveal": "", style: `--i:${Math.min(i, 10)}` },
      el("div", { class: "rank", "aria-hidden": "true" }, String(i + 1)),
      el("div", {},
        el("a", { class: "name", href: `hospital.html?ccn=${r.ccn}` }, r.name),
        el("div", { class: "sys" }, r.system),
        el("div", { class: "meta" },
          r.price === lo ? el("span", { class: "tag" }, "Lowest price") : "",
          r.ccn === closest ? el("span", { class: "tag quiet" }, "Closest") : "",
          r.distance != null ? el("span", {}, icon("pin", 14), ` ${miles(r.distance)} away${r.approx ? " (approx.)" : ""}`) : "",
          r.star ? el("span", { title: "Medicare's overall quality rating for the hospital" }, `★ ${r.star}/5 Medicare rating`) : "",
          gradeBadge(r.grade, r.score),
          r.n_plans > 1 ? el("span", {}, `${r.n_plans} plans, ${money(r.plan_min)}-${money(r.plan_max)}`) : "")),
      el("div", { class: "price" }, el("div", { class: "amt" }, money(r.price)), el("div", { class: "rel" }, vsUsual(r.rel))),
      el("div", { class: "mini", "aria-hidden": "true" }, el("i", { style: `width:${Math.max(3, (100 * r.price) / maxPrice)}%;--i:${Math.min(i, 12)}` }))))));
  }

  // ---- extras, folded away
  const extras = el("div", { class: "card", style: "margin-top:1.25rem", "data-reveal": "" });
  const mapFold = fold("Show these hospitals on a map");
  mapFold.addEventListener("toggle", () => {
    if (mapFold.open && !mapFold.dataset.drawn) {
      mapFold.dataset.drawn = "1";
      mapFold.querySelector(".fold-body").append(locatorMap(enriched.map((r) => ({ lat: H[r.ccn]?.lat, lon: H[r.ccn]?.lon, value: r.price, label: r.name,
        href: `hospital.html?ccn=${r.ccn}`, approx: r.approx })), { you }));
    }
  });
  extras.append(mapFold);
  const view = self ? "cash" : state.ptype === "commercial" ? state.payer : null;
  const s = view && data.summaries[view];
  if (s) extras.append(fold("A short summary of these prices", el("p", { class: "muted" }, plainText(s.text)),
    el("p", { class: "small muted" }, "Written by an AI model using only the numbers above, then checked automatically.")));
  const shown = new Set(rows.map((r) => r.ccn));
  const missing = Object.values(H).filter((h) => h.in_scope && !shown.has(h.ccn));
  if (missing.length) {
    const why = (h) => (h.status === "usable" && h.grade === "not scored") ? "its price list wasn't processed in this update" : ({
      usable: "it hasn't published a usable price for this", blocked: "its website blocks automatic downloads of the price list",
      not_listed: "we couldn't find its price list", not_found: "we couldn't find its price list" }[h.status] || h.status);
    extras.append(fold(`${missing.length} hospitals not in this list, and why`, el("ul", { class: "findings" },
      missing.sort((a, b) => a.name.localeCompare(b.name)).map((h) => el("li", {}, el("a", { href: `hospital.html?ccn=${h.ccn}` }, h.name), `: ${why(h)}`)))));
  }
  nodes.push(extras);
  return nodes;
}

function codePill(code) {
  const btn = el("button", { type: "button", class: "copy", "aria-label": `Copy billing code ${code}` }, "Copy");
  btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(String(code)); btn.textContent = "Copied ✓"; }
    catch { btn.textContent = "Select it"; }
    setTimeout(() => { btn.textContent = "Copy"; }, 1800);
  });
  return el("span", { class: "code-pill" }, `Billing code ${code}`, btn);
}

main().catch((e) => errorBox(document.getElementById("results"), e));
