// Shared helpers for every page. All text from data goes in via textContent (never innerHTML).

const cache = new Map();
export function loadJSON(path) {
  if (!cache.has(path)) {
    cache.set(path, fetch(path).then((r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    }));
  }
  return cache.get(path);
}

export const param = (name) => new URLSearchParams(location.search).get(name);

/** el("a", {href: "x", class: "y"}, "text", childNode, ...) - children as text or nodes. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
export const svg = (tag, attrs = {}, ...children) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, v);
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
};

// ---------- formatting ----------
export function money(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return "-";
  return Math.abs(x) >= 100
    ? "$" + Math.round(x).toLocaleString("en-US")
    : "$" + x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
export const times = (x) => (x === null || x === undefined ? "-" : `${x.toFixed(2)}×`);
export const pct = (x) => (x === null || x === undefined ? "-" : `${Math.round(100 * x)}%`);
export const miles = (x) => (x === null || x === undefined ? "-" : `${x < 10 ? x.toFixed(1) : Math.round(x)} mi`);

export function haversineMiles(a, b) {
  const R = 3958.8, rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// ---------- badges: status colour + icon + text ----------
export function gradeBadge(grade, score) {
  const cls = { A: "good", B: "good", C: "warning", D: "serious", F: "critical" }[grade] || "neutral";
  const text = grade && grade !== "not scored" ? `Price list: ${grade}` : "Price list: not checked";
  return el("span", { class: `badge ${cls}`, title: score != null ? `How complete and clean the hospital's price list is: ${Math.round(score)} out of 100 (not a rating of medical care)` : "Not checked yet" },
    el("span", { class: "dot", "aria-hidden": "true" }), text);
}
export function labelBadge(label) {
  const map = { good: ["good", "Looks right"], suspect: ["warning", "Looks odd"], rejected: ["critical", "Removed"] };
  const [cls, text] = map[label] || ["neutral", label || "-"];
  return el("span", { class: `badge ${cls}` }, el("span", { class: "dot", "aria-hidden": "true" }), text);
}

// ---------- page shell ----------
const NAV = [
  ["index.html", "Check a price"],
  ["procedure.html", "Procedures"],
  ["hospital.html", "Hospitals"],
  ["insurer.html", "Insurance companies"],
  ["quality.html", "Can I trust this?"],
  ["about.html", "About"],
];

// ---------- plain words (used on every page instead of industry terms) ----------
export const FRIENDLY = {
  plan: {
    commercial: "Through work or bought myself", exchange: "Marketplace plan (healthcare.gov)",
    medicare_advantage: "Medicare plan from a private company", medicaid: "AHCCCS (Arizona Medicaid)",
    medicare_supplement: "Medicare supplement (Medigap)", medicare: "Original Medicare", tricare: "TRICARE (military)",
    va: "VA Community Care", workers_comp: "Workers' comp (injured at work)", rental_network: "Discount network",
    other_government: "Other government plan", all_products: "Any of this company's plans", unknown: "Plan type not stated",
    self_pay: "Paying myself",
  },
  // how a procedure reads inside a sentence ("... usually costs about $544")
  proc: {
    mri_knee: ["a knee MRI", "scan"], mri_brain: ["a brain MRI", "scan"], mri_lumbar_spine: ["a lower-back MRI", "scan"],
    ct_head: ["a head CT scan", "scan"], ct_abdomen_pelvis_contrast: ["a belly and pelvis CT scan (with dye)", "scan"],
    ct_abdomen_pelvis: ["a belly and pelvis CT scan", "scan"], mammogram_screening: ["a screening mammogram", "scan"],
    xray_chest: ["a chest X-ray", "X-ray"], ultrasound_abdomen: ["a belly ultrasound", "scan"],
    ultrasound_pregnancy: ["an early pregnancy ultrasound", "scan"], colonoscopy: ["a colonoscopy", "procedure"],
    colonoscopy_biopsy: ["a colonoscopy with biopsy", "procedure"], colonoscopy_polyp_removal: ["a colonoscopy with polyp removal", "procedure"],
    upper_gi_endoscopy: ["an upper endoscopy with biopsy", "procedure"], cataract_surgery: ["cataract surgery", "surgery"],
    knee_arthroscopy: ["knee arthroscopy", "surgery"], gallbladder_removal: ["gallbladder removal surgery", "surgery"],
    hernia_repair: ["a hernia repair", "surgery"], knee_replacement: ["a knee replacement (the surgery itself)", "surgery"],
    hip_replacement: ["a hip replacement (the surgery itself)", "surgery"],
    joint_replacement_inpatient: ["a hip or knee replacement hospital stay", "stay"], vaginal_delivery: ["a vaginal birth hospital stay", "stay"],
    c_section: ["a C-section hospital stay", "stay"], er_visit_level_3: ["a moderate ER visit", "visit"],
    er_visit_level_4: ["a serious ER visit", "visit"], er_visit_level_5: ["a very serious ER visit", "visit"],
    lab_basic_metabolic: ["a basic blood test (BMP)", "test"], lab_comprehensive_metabolic: ["a full blood chemistry test (CMP)", "test"],
    lab_lipid_panel: ["a cholesterol test", "test"], lab_cbc: ["a complete blood count", "test"], lab_tsh: ["a thyroid test", "test"],
    lab_hba1c: ["a diabetes (A1c) test", "test"], lab_psa: ["a prostate (PSA) test", "test"], ecg: ["a heart tracing (ECG)", "test"],
  },
};
export const procPhrase = (id, fallback) => (FRIENDLY.proc[id] || [fallback ? fallback.toLowerCase() : "this"])[0];
export const planWords = (t, plan_types = {}) => FRIENDLY.plan[t] || plan_types[t] || t;
/** "36% less than usual", "about the usual price", "2.1× the usual price" */
export function vsUsual(r) {
  if (r == null) return "";
  if (r < 0.97) return `${Math.round((1 - r) * 100)}% less than usual`;
  if (r <= 1.03) return "about the usual price";
  return r < 2 ? `${Math.round((r - 1) * 100)}% more than usual` : `${r.toFixed(1)}× the usual price`;
}
/** Swap industry words in generated summaries for plain ones (numbers untouched). */
export function plainText(s) {
  return String(s)
    .replace(/\s*\((?:MS-)?DRG \d+\)/g, "")
    .replace(/commercial \(employer or individual\) plans/gi, "plans through work or bought yourself")
    .replace(/commercial plans/gi, "plans through work or bought yourself")
    .replace(/\bthe median price\b/gi, "the usual price").replace(/\bmedian price\b/gi, "usual price").replace(/\bMedian\b/g, "Usual").replace(/\bmedian\b/g, "usual")
    .replace(/\bcash price\b/gi, "self-pay price");
}
/** A fold: summary line, contents hidden until opened ("Details for the curious"). */
export function fold(summary, ...children) {
  return el("details", { class: "fold" }, el("summary", {}, summary), el("div", { class: "fold-body" }, ...children));
}

// ---------- icons (inline SVG, stroke = currentColor) ----------
const ICONS = {
  compass: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.5 6.5-2 5-5 2 2-5 5-2z",
  sun: "M12 4V2m0 20v-2m8-8h2M2 12h2m13.66-5.66 1.41-1.41M4.93 19.07l1.41-1.41m0-11.32L4.93 4.93m14.14 14.14-1.41-1.41M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "M6 6l12 12M18 6 6 18",
  info: "M12 16v-5m0-3h.01M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z",
  shield: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm9 16-4.3-4.3",
  card: "M3 7h18v10H3zM3 10h18",
  pin: "M12 21s-7-6.2-7-11a7 7 0 1 1 14 0c0 4.8-7 11-7 11zm0-8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  check: "M5 12.5 10 17l9-10",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  chart: "M4 20V10m6 10V4m6 16v-7m4 7H2",
  scan: "M4 8V5a1 1 0 0 1 1-1h3m8 0h3a1 1 0 0 1 1 1v3m0 8v3a1 1 0 0 1-1 1h-3m-8 0H5a1 1 0 0 1-1-1v-3M8 12h8",
  heart: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z",
  flask: "M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3",
  bed: "M3 18V8m0 6h18v4M21 14v-2a3 3 0 0 0-3-3h-7v5M7 11.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z",
  siren: "M7 18v-6a5 5 0 0 1 10 0v6M5 21h14M12 3v2M4.2 6.2l1.4 1.4m12.8-1.4-1.4 1.4",
  scalpel: "M14 4l6 6-9.5 9.5a2 2 0 0 1-2.8 0L5 16.8a2 2 0 0 1 0-2.8L14 4z",
  building: "M4 21V5l8-3 8 3v16M9 21v-5h6v5M9 9h.01M15 9h.01M9 13h.01M15 13h.01",
};
export function icon(name, size = 18) {
  const s = svg("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
    "stroke-width": name === "compass" ? 0 : 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
  s.append(svg("path", { d: ICONS[name] || ICONS.info, fill: name === "compass" ? "currentColor" : "none" }));
  return s;
}

/** A small ⓘ button that explains a term in plain words (click, focus or hover). */
export function infoTip(text, label = "What does this mean?") {
  const pop = el("span", { class: "pop", role: "tooltip" }, text);
  const btn = el("button", { type: "button", "aria-label": label, "aria-expanded": "false" }, "i");
  const box = el("span", { class: "info" }, btn, pop);
  const place = () => {                                   // above the button, clamped inside the viewport
    const r = btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), innerWidth - w - 12) + "px";
    pop.style.top = (r.top - h - 10 < 8 ? r.bottom + 10 : r.top - h - 10) + "px";
  };
  const set = (open) => {
    box.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", String(open));
    if (open) place();
  };
  addEventListener("scroll", () => box.classList.contains("open") && set(false), { passive: true });
  btn.addEventListener("click", (e) => { e.stopPropagation(); set(!box.classList.contains("open")); });
  box.addEventListener("mouseenter", () => set(true));
  box.addEventListener("mouseleave", () => set(false));
  btn.addEventListener("blur", () => set(false));
  btn.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
  return box;
}

/** Fill a page hero: eyebrow, title, lead (elements #eyebrow, #title, #lead in the page). */
export function hero({ eyebrow, title, lead } = {}) {
  if (eyebrow != null) document.getElementById("eyebrow")?.replaceChildren(eyebrow);
  if (title != null) document.getElementById("title")?.replaceChildren(title);
  if (lead != null) document.getElementById("lead")?.replaceChildren(...[lead].flat());
}

export async function shell() {
  document.documentElement.classList.remove("no-js");
  const meta = await loadJSON("data/meta.json");
  for (const pr of meta.procedures) pr.name = pr.name.replace(/\s*\((?:MS-)?DRG \d+\)/, "");   // billing codes are shown separately
  const page = location.pathname.split("/").pop() || "index.html";
  const nav = el("nav", { class: "main", id: "main-nav", "aria-label": "Main" },
    el("ul", {}, NAV.map(([href, text]) => el("li", {}, el("a", { href, "aria-current": href === page ? "page" : null }, text)))));
  const menuBtn = el("button", { class: "icon-btn menu-btn", type: "button", "aria-label": "Open menu", "aria-expanded": "false", "aria-controls": "main-nav" }, icon("menu"));
  menuBtn.addEventListener("click", () => {
    const open = !nav.classList.contains("open");
    nav.classList.toggle("open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
    menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    menuBtn.replaceChildren(icon(open ? "close" : "menu"));
  });
  const header = el("header", { class: "site" },
    el("div", { class: "bar" },
      el("a", { class: "brand", href: "index.html", "aria-label": "Hospital Price Compass, Phoenix: home" },
        el("span", { class: "mark" }, icon("compass", 18)), el("span", {}, "Hospital Price Compass ", el("small", {}, "Phoenix"))),
      nav, menuBtn, themeToggle()));
  document.body.prepend(el("div", { class: "progress", "aria-hidden": "true" }), el("a", { class: "skip", href: "#main" }, "Skip to content"), header);
  document.body.append(el("footer", { class: "site" }, el("div", { class: "wrap" },
    el("div", {},
      el("p", {}, el("span", { class: "asof" }, `Data as of ${meta.as_of}`)),
      el("p", {}, meta.safety_notice)),
    el("div", {},
      el("p", { class: "mission" }, "Hospital prices shouldn't be a surprise. We put every Phoenix hospital's published prices side by side, in plain words."),
      el("p", { class: "fine" }, `Hospital price files dated ${meta.file_dates[0]} to ${meta.file_dates[1]}. Sources: hospital price `,
        "transparency files (45 CFR 180), CMS Hospital General Information, CMS FY 2026 inpatient payment rule. ",
        "Summaries are written by an AI model and checked so they contain only computed numbers."),
      el("p", {}, el("a", { href: "about.html" }, "About this site, sources and limitations"))))));
  const slot = document.getElementById("asof-top");
  if (slot) slot.replaceChildren(el("span", { class: "asof" }, `Data as of ${meta.as_of}`));
  const { scrollChrome, reveal, spotlight } = await import("./motion.js");
  scrollChrome();
  reveal();
  spotlight();
  // layout self-check used by the automated tests: viewport width vs widest content
  const record = () => {
    const W = document.documentElement.clientWidth;
    document.documentElement.dataset.layout = `${innerWidth}x${document.documentElement.scrollWidth}`;
    // name the element that sticks out furthest (helps the tests explain an overflow)
    let worst = null, right = W;
    for (const n of document.body.querySelectorAll("*")) {
      const r = n.getBoundingClientRect();
      if (r.width && r.right > right + 1) { right = r.right; worst = n; }
    }
    if (worst) document.documentElement.dataset.overflow = `${worst.tagName.toLowerCase()}.${[...worst.classList].join(".")} ${Math.round(right)}px`;
    else delete document.documentElement.dataset.overflow;
  };
  addEventListener("load", () => setTimeout(record, 500));
  setTimeout(record, 2500);
  return meta;
}

function themeToggle() {
  const fromUrl = new URLSearchParams(location.search).get("theme");   // ?theme=light|dark (testing, sharing)
  if (fromUrl === "light" || fromUrl === "dark") localStorage.setItem("theme", fromUrl);
  const saved = localStorage.getItem("theme");
  document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";   // dark by default
  const btn = el("button", { class: "icon-btn theme-toggle", type: "button" });
  const label = () => {
    const dark = document.documentElement.dataset.theme === "dark";
    btn.replaceChildren(icon(dark ? "sun" : "moon"));
    btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    btn.setAttribute("aria-pressed", String(!dark));
  };
  btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    const apply = () => { document.documentElement.dataset.theme = next; localStorage.setItem("theme", next); label(); };
    if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) document.startViewTransition(apply);
    else apply();
  });
  label();
  return btn;
}

// ---------- tooltip ----------
let tip;
export function tooltip() {
  if (!tip) { tip = el("div", { class: "tooltip", role: "status" }); document.body.append(tip); }
  return {
    show(evtOrNode, value, label) {
      tip.replaceChildren(el("div", { class: "tv" }, value), label ? el("div", { class: "tl" }, label) : "");
      tip.style.display = "block";
      let x, y;
      if (evtOrNode.clientX !== undefined) { x = evtOrNode.clientX; y = evtOrNode.clientY; }
      else { const r = evtOrNode.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top; }
      const w = tip.offsetWidth, h = tip.offsetHeight;
      tip.style.left = Math.min(Math.max(8, x + 12), innerWidth - w - 8) + "px";
      tip.style.top = Math.max(8, y - h - 12) + "px";
    },
    hide() { tip.style.display = "none"; },
  };
}

// ---------- sortable table ----------
/** columns: [{key, label, num?, render?(row) -> node|string, sortValue?(row)}] */
export function table(columns, rows, { caption, sortKey, sortDir = "asc", id } = {}) {
  let key = sortKey, dir = sortDir;
  const tbody = el("tbody");
  const ths = columns.map((c) => {
    const th = el("th", { scope: "col", class: c.num ? "r" : null });
    if (c.sortable === false) { th.textContent = c.label; return th; }
    const b = el("button", { type: "button" }, c.label, el("span", { "aria-hidden": "true", class: "arrow" }));
    b.addEventListener("click", () => { dir = key === c.key && dir === "asc" ? "desc" : "asc"; key = c.key; render(); });
    th.append(b);
    return th;
  });
  function render() {
    const col = columns.find((c) => c.key === key) || columns[0];
    const val = (r) => (col.sortValue ? col.sortValue(r) : r[col.key]);
    const sorted = [...rows].sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      const cmp = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb));
      return dir === "asc" ? cmp : -cmp;
    });
    columns.forEach((c, i) => {
      ths[i].setAttribute("aria-sort", c.key === key ? (dir === "asc" ? "ascending" : "descending") : "none");
      const arrow = ths[i].querySelector(".arrow");
      if (arrow) arrow.textContent = c.key === key ? (dir === "asc" ? "▲" : "▼") : "";
    });
    tbody.replaceChildren(...sorted.map((r) => el("tr", {}, columns.map((c) => {
      const v = c.render ? c.render(r) : r[c.key];
      return el("td", { class: c.num ? "r" : null }, v === null || v === undefined ? "-" : v);
    }))));
  }
  render();
  return el("div", { class: "table-wrap", id },
    el("table", {}, caption ? el("caption", {}, caption) : "", el("thead", {}, el("tr", {}, ths)), tbody));
}

export function errorBox(container, err) {
  container.replaceChildren(el("div", { class: "notice", role: "alert" },
    "Sorry, this page couldn't load its data. ", String(err && err.message ? err.message : err)));
}
