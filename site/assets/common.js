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
  const text = grade && grade !== "not scored" ? `Grade ${grade}` : "Not scored";
  return el("span", { class: `badge ${cls}`, title: score != null ? `Compliance score ${Math.round(score)} / 100` : "Not scored" },
    el("span", { class: "dot", "aria-hidden": "true" }), text);
}
export function labelBadge(label) {
  const map = { good: ["good", "Good"], suspect: ["warning", "Suspect"], rejected: ["critical", "Rejected"] };
  const [cls, text] = map[label] || ["neutral", label || "-"];
  return el("span", { class: `badge ${cls}` }, el("span", { class: "dot", "aria-hidden": "true" }), text);
}

// ---------- page shell ----------
const NAV = [
  ["index.html", "Compare prices"],
  ["procedure.html", "Procedures"],
  ["hospital.html", "Hospitals"],
  ["insurer.html", "Insurers"],
  ["quality.html", "Data quality"],
  ["about.html", "About"],
];

export async function shell() {
  const meta = await loadJSON("data/meta.json");
  const page = location.pathname.split("/").pop() || "index.html";
  const header = el("header", { class: "site" },
    el("div", { class: "wrap" },
      el("a", { class: "brand", href: "index.html" }, "Hospital Price Compass ", el("span", {}, "Phoenix")),
      el("nav", { class: "main", "aria-label": "Main" },
        el("ul", {}, NAV.map(([href, text]) => el("li", {},
          el("a", { href, "aria-current": href === page ? "page" : null }, text))))),
      themeToggle()));
  document.body.prepend(el("a", { class: "skip", href: "#main" }, "Skip to content"), header);
  const asof = el("p", {}, el("span", { class: "asof" }, `Data as of ${meta.as_of}`),
    ` · hospital price files dated ${meta.file_dates[0]} to ${meta.file_dates[1]}`);
  document.body.append(el("footer", { class: "site" }, el("div", { class: "wrap" },
    asof,
    el("p", {}, meta.safety_notice),
    el("p", { class: "muted" }, "Sources: hospital price transparency files (45 CFR 180), CMS Hospital General Information, ",
      "CMS FY 2026 IPPS final rule. Summaries written by an AI model and checked so they contain only computed numbers. ",
      el("a", { href: "about.html" }, "Method and limitations"), "."))));
  // the data date is also shown near the top of every page
  const slot = document.getElementById("asof-top");
  if (slot) slot.append(el("span", { class: "asof" }, `Data as of ${meta.as_of}`));
  // layout self-check used by the automated tests: viewport width vs widest content
  const record = () => { document.documentElement.dataset.layout = `${innerWidth}x${document.documentElement.scrollWidth}`; };
  addEventListener("load", () => setTimeout(record, 500));
  setTimeout(record, 2500);
  return meta;
}

function themeToggle() {
  const fromUrl = new URLSearchParams(location.search).get("theme");   // ?theme=light|dark (testing, sharing)
  if (fromUrl === "light" || fromUrl === "dark") localStorage.setItem("theme", fromUrl);
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.dataset.theme = saved;
  const btn = el("button", { class: "theme-toggle", type: "button", "aria-pressed": "false" });
  const label = () => {
    const dark = (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
    btn.textContent = dark ? "Light mode" : "Dark mode";
    btn.setAttribute("aria-pressed", String(dark));
  };
  btn.addEventListener("click", () => {
    const dark = btn.getAttribute("aria-pressed") === "true";
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    localStorage.setItem("theme", document.documentElement.dataset.theme);
    label();
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
