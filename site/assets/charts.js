// Charts: one hue for values, gray for de-emphasis, thin marks, hover tooltips, and every chart
// paired with a table view so nothing is reachable only by pointer.
import { el, svg, tooltip, money } from "./common.js";

const tip = () => tooltip();

// ---- row labels: measure the real text, size the label column to fit (max 45% of the chart),
// and truncate with an ellipsis beyond that (full text in a <title> for hover / screen readers)
let ctx;
function textWidth(s, weight = 400) {
  ctx ||= document.createElement("canvas").getContext("2d");
  ctx.font = `${weight} 12px system-ui, -apple-system, "Segoe UI", sans-serif`;
  return ctx.measureText(s).width;
}
function labelColumn(labels, W, weight = 400) {
  const need = Math.max(40, ...labels.map((l) => textWidth(l, weight))) + 16;
  return Math.min(need, W * 0.45);
}
function fitLabel(s, maxW, weight = 400) {
  if (textWidth(s, weight) <= maxW) return s;
  let t = s;
  while (t.length > 3 && textWidth(t + "…", weight) > maxW) t = t.slice(0, -1);
  return t.trimEnd() + "…";
}
function rowLabel(text, x, y, cls, maxW) {
  const weight = cls === "strong" ? 600 : 400;
  const shown = fitLabel(text, maxW, weight);
  return svg("text", { class: cls, x, y, "text-anchor": "end" }, shown === text ? null : svg("title", {}, text), shown);
}

function niceLogTicks(lo, hi, max = 6) {
  const ticks = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 2, 5]) {
      const v = m * 10 ** e;
      if (v >= lo * 0.999 && v <= hi * 1.001) ticks.push(v);
    }
  }
  while (ticks.length > max) for (let i = 1; i < ticks.length - 1; i += 1) ticks.splice(i, 1);
  return ticks;
}
function niceLinearTicks(lo, hi, n = 5) {
  const step0 = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
const compact = (v) => (v >= 10000 ? `$${Math.round(v / 1000)}K` : v >= 1000 ? `$${(v / 1000).toFixed(v % 1000 ? 1 : 0)}K` : `$${v}`);

function responsive(container, draw) {
  let last = 0;
  const go = () => {
    const w = Math.max(300, Math.round(container.clientWidth));
    if (w === last) return;
    last = w;
    draw(w);
  };
  go();
  new ResizeObserver(go).observe(container);
}

/** Toggle between a chart and its table view (same data). */
export function withTableView(title, chartNode, tableNode) {
  const btn = el("button", { type: "button", class: "btn view-toggle", "aria-pressed": "false" }, "Show as table");
  const tableBox = el("div", { hidden: true }, tableNode);
  btn.addEventListener("click", () => {
    const showTable = tableBox.hidden;
    tableBox.hidden = !showTable;
    chartNode.hidden = showTable;
    btn.textContent = showTable ? "Show as chart" : "Show as table";
    btn.setAttribute("aria-pressed", String(showTable));
  });
  return el("section", { "aria-label": title }, btn, chartNode, tableBox);
}

/**
 * Dot strip: one row per group, one dot per hospital, median tick, log or linear x.
 * rows: [{label, points: [{value, label}], median?}]
 */
export function dotStrip(rows, { log = true, format = money, xTitle = "", ref = null, refLabel = "", rowHeight = 34 } = {}) {
  const box = el("div", { class: "chart animate" });
  const values = rows.flatMap((r) => r.points.map((p) => p.value)).filter((v) => v > 0);
  if (!values.length) { box.append(el("p", { class: "muted" }, "No prices to plot.")); return box; }
  let lo = Math.min(...values, ref ?? Infinity), hi = Math.max(...values, ref ?? -Infinity);
  if (log) { lo /= 1.25; hi *= 1.25; } else { const pad = (hi - lo) * 0.08 || 1; lo = Math.max(0, lo - pad); hi += pad; }
  const ticks = log ? niceLogTicks(lo, hi) : niceLinearTicks(lo, hi);
  const t = tip();
  responsive(box, (W) => {
    const labelW = labelColumn(rows.map((r) => r.label), W), right = 16, top = 8, axisH = 40;
    const H = top + rows.length * rowHeight + axisH;
    const x = (v) => labelW + ((log ? Math.log(v) - Math.log(lo) : v - lo) / (log ? Math.log(hi) - Math.log(lo) : hi - lo)) * (W - labelW - right);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `${xTitle}. ${rows.length} rows; open the table view for every value.` });
    const g = svg("g", { class: "grid" });
    for (const v of ticks) g.append(svg("line", { x1: x(v), x2: x(v), y1: top, y2: top + rows.length * rowHeight }));
    s.append(g);
    if (ref !== null) {
      s.append(svg("line", { class: "ref", x1: x(ref), x2: x(ref), y1: top - 4, y2: top + rows.length * rowHeight }));
      if (refLabel) s.append(svg("text", { class: "axis", x: x(ref) + 4, y: top + 6 }, refLabel));
    }
    rows.forEach((r, i) => {
      const cy = top + i * rowHeight + rowHeight / 2;
      s.append(rowLabel(r.label, labelW - 10, cy + 4, "label", labelW - 14));
      s.append(svg("line", { class: "baseline", x1: labelW, x2: W - right, y1: cy, y2: cy }));
      // hospitals sharing a price (common: one price list per system) -> ONE dot plus a small count,
      // with every hospital named in the tooltip; stacked dots would overlap into an unreadable chain
      const groups = new Map();
      for (const p of r.points.filter((p) => p.value > 0)) {
        const k = Math.round(x(p.value));
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(p);
      }
      let lastLabelX = -Infinity, n = i * 3;
      for (const [, ps] of [...groups].sort((a, b) => a[0] - b[0])) {
        const px = x(ps[0].value);
        const showCount = ps.length > 1 && px - lastLabelX >= 14;   // skip a count that would collide
        if (showCount) lastLabelX = px;
        const label = ps.length === 1 ? ps[0].label
          : `${ps.length} hospitals: ${ps.slice(0, 4).map((p) => p.label).join(", ")}${ps.length > 4 ? ", …" : ""}`;
        const hit = svg("g", { class: "hit" },
          svg("circle", { cx: px, cy, r: 12, fill: "transparent" }),
          svg("circle", { class: `dot enter${ps[0].deemph ? " deemph" : ""}`, cx: px, cy, r: ps.length > 1 ? 6 : 5, style: `--i:${n++}` }),
          showCount ? svg("text", { class: "axis", x: px, y: cy - 9, "text-anchor": "middle" }, String(ps.length)) : null);
        hit.addEventListener("pointermove", (e) => t.show(e, format(ps[0].value), label));
        hit.addEventListener("pointerleave", () => t.hide());
        s.append(hit);
      }
      if (r.median) s.append(svg("line", { class: "median", x1: x(r.median), x2: x(r.median), y1: cy - 10, y2: cy + 10 }));
    });
    const ax = svg("g", { class: "axis" });
    for (const v of ticks) ax.append(svg("text", { x: x(v), y: top + rows.length * rowHeight + 16, "text-anchor": "middle" },
      format === money ? compact(v) : format(v)));
    if (xTitle) ax.append(svg("text", { x: labelW, y: H - 4 }, xTitle + (log ? " (log scale)" : "")));
    s.append(ax);
    box.replaceChildren(s);
  });
  return box;
}

/** Forest plot: estimate + interval per row, reference line, one row emphasised. */
export function forest(rows, { ref = 1, refLabel = "average", format = (v) => v.toFixed(2), xTitle = "" } = {}) {
  const box = el("div", { class: "chart animate" });
  const lo0 = Math.min(ref, ...rows.map((r) => r.lo ?? r.est)), hi0 = Math.max(ref, ...rows.map((r) => r.hi ?? r.est));
  const pad = (hi0 - lo0) * 0.08;
  const lo = lo0 - pad, hi = hi0 + pad;
  const ticks = niceLinearTicks(lo, hi, 5);
  const t = tip();
  responsive(box, (W) => {
    const labelW = labelColumn(rows.map((r) => r.label), W, 600), right = 20, top = 18, rowH = 30, axisH = 40;
    const H = top + rows.length * rowH + axisH;
    const x = (v) => labelW + ((v - lo) / (hi - lo)) * (W - labelW - right);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${xTitle}. Open the table view for every value.` });
    const g = svg("g", { class: "grid" });
    for (const v of ticks) g.append(svg("line", { x1: x(v), x2: x(v), y1: top, y2: top + rows.length * rowH }));
    s.append(g, svg("line", { class: "ref", x1: x(ref), x2: x(ref), y1: top - 8, y2: top + rows.length * rowH }),
      svg("text", { class: "axis", x: x(ref) + 4, y: top - 6 }, refLabel));
    rows.forEach((r, i) => {
      const cy = top + i * rowH + rowH / 2;
      s.append(rowLabel(r.label, labelW - 10, cy + 4, r.emphasis ? "strong" : "label", labelW - 14));
      const de = r.emphasis === false ? " deemph" : "";
      if (r.lo != null && r.hi != null) s.append(svg("line", { class: `ci enter${de}`, x1: x(r.lo), x2: x(r.hi), y1: cy, y2: cy, style: `--i:${i}` }));
      const hit = svg("g", { class: "hit" },
        svg("rect", { x: labelW, y: cy - rowH / 2, width: W - labelW - right, height: rowH, fill: "transparent" }),
        svg("circle", { class: `dot enter${de}`, cx: x(r.est), cy, r: 5, style: `--i:${i + 2}` }));
      hit.addEventListener("pointermove", (e) => t.show(e, format(r.est), `${r.label}${r.lo != null ? ` · 95% interval ${format(r.lo)} to ${format(r.hi)}` : ""}`));
      hit.addEventListener("pointerleave", () => t.hide());
      s.append(hit);
    });
    const ax = svg("g", { class: "axis" });
    for (const v of ticks) ax.append(svg("text", { x: x(v), y: top + rows.length * rowH + 16, "text-anchor": "middle" }, format(v)));
    if (xTitle) ax.append(svg("text", { x: labelW, y: H - 4 }, xTitle));
    s.append(ax);
    box.replaceChildren(s);
  });
  return box;
}

const CITIES = [
  ["Phoenix", 33.4484, -112.074], ["Mesa", 33.4152, -111.8315], ["Scottsdale", 33.5942, -111.9261],
  ["Glendale", 33.5387, -112.186], ["Chandler", 33.3062, -111.8413], ["Gilbert", 33.3528, -111.789],
  ["Peoria", 33.6806, -112.2374], ["Goodyear", 33.4353, -112.3577], ["Surprise", 33.6292, -112.3679],
  ["Casa Grande", 32.8795, -111.7574], ["Wickenburg", 33.9686, -112.7296], ["Queen Creek", 33.2487, -111.6343],
];

/**
 * Locator map without a basemap (no third-party tiles): hospital dots coloured by price in a
 * 5-step sequential ramp, city names for orientation, optional "you" marker. Dots are links.
 * points: [{lat, lon, value, label, href, approx}]
 */
export function locatorMap(points, { you = null, format = money } = {}) {
  const box = el("div", { class: "chart animate" });
  const pts = points.filter((p) => p.lat && p.lon);
  if (!pts.length) return box;
  const vals = pts.map((p) => p.value).filter((v) => v != null).sort((a, b) => a - b);
  const q = (f) => vals[Math.min(vals.length - 1, Math.floor(f * vals.length))];
  // quintile breakpoints, merged when many hospitals share a price (shared price lists are common)
  const cuts = [...new Set([q(0.2), q(0.4), q(0.6), q(0.8)])].filter((c) => c < vals[vals.length - 1]);
  const nb = cuts.length + 1;
  const step = (i) => [1, 2, 3, 4, 5][Math.round((i * 4) / Math.max(1, nb - 1))];   // spread bins over the ramp
  const bin = (v) => cuts.filter((c) => v > c).length;            // 0..nb-1
  const lats = pts.map((p) => p.lat).concat(you ? [you[0]] : []), lons = pts.map((p) => p.lon).concat(you ? [you[1]] : []);
  const [la0, la1, lo0, lo1] = [Math.min(...lats) - 0.08, Math.max(...lats) + 0.08, Math.min(...lons) - 0.1, Math.max(...lons) + 0.1];
  const kx = Math.cos(((la0 + la1) / 2) * Math.PI / 180);
  const t = tip();
  responsive(box, (W) => {
    const aspect = ((la1 - la0) / ((lo1 - lo0) * kx));
    const H = Math.round(Math.min(560, Math.max(260, W * aspect)));
    const sx = (lon) => ((lon - lo0) / (lo1 - lo0)) * W, sy = (lat) => H - ((lat - la0) / (la1 - la0)) * H;
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": "Map of hospitals coloured by price. The same hospitals and prices are in the table." });
    s.append(svg("rect", { class: "mapbg", x: 0.5, y: 0.5, width: W - 1, height: H - 1, rx: 14 }));
    let k = 0;
    for (const [name, la, lo] of CITIES) {
      if (la < la0 || la > la1 || lo < lo0 || lo > lo1) continue;
      s.append(svg("text", { class: "city", x: sx(lo), y: sy(la) - 11, "text-anchor": "middle" }, name),
        svg("circle", { cx: sx(lo), cy: sy(la), r: 1.5, fill: "var(--axis)" }));
    }
    for (const p of [...pts].sort((a, b) => (a.value ?? 0) - (b.value ?? 0))) {
      const fill = p.value == null ? "var(--deemph)" : `var(--seq-${step(bin(p.value))})`;
      const a = svg("a", { href: p.href, "aria-label": `${p.label}: ${p.value == null ? "no price" : format(p.value)}` },
        svg("circle", { cx: sx(p.lon), cy: sy(p.lat), r: 12, fill: "transparent" }),
        svg("circle", { class: "dot enter", cx: sx(p.lon), cy: sy(p.lat), r: 6, style: `fill:${fill};--i:${k++}` }));
      a.addEventListener("pointermove", (e) => t.show(e, p.value == null ? "No price" : format(p.value),
        p.label + (p.approx ? " (location approximate)" : "")));
      a.addEventListener("pointerleave", () => t.hide());
      a.addEventListener("focus", () => t.show(a, p.value == null ? "No price" : format(p.value), p.label));
      a.addEventListener("blur", () => t.hide());
      s.append(a);
    }
    if (you) {
      const [x, y] = [sx(you[1]), sy(you[0])];
      s.append(svg("path", { class: "you", d: `M${x} ${y - 8} L${x + 8} ${y} L${x} ${y + 8} L${x - 8} ${y} Z` }),
        svg("text", { class: "strong", x: x + 10, y: y + 20 }, "Your ZIP"));
    }
    box.replaceChildren(s);
  });
  const bands = nb === 1 ? [`${money(vals[0])}${vals[0] !== vals[vals.length - 1] ? `-${money(vals[vals.length - 1])}` : ""}`]
    : [...Array(nb).keys()].map((i) => i === 0 ? `up to ${money(cuts[0])}` : i === nb - 1 ? `over ${money(cuts[nb - 2])}`
      : `${money(cuts[i - 1])}-${money(cuts[i])}`);
  const legend = el("div", { class: "legend", "aria-hidden": "true" }, "Price: ",
    ...bands.map((text, i) => el("span", {}, el("span", { class: "sw", style: `background:var(--seq-${step(i)})` }), text)));
  return el("div", {}, box, legend);
}

/**
 * Range bar: lowest -> highest price on a straight (linear) scale, the band where most hospitals fall
 * (10th-90th percentile), the median, and one dot per hospital. HTML, so it reflows on any width.
 * points: [{value, label}]
 */
export function rangeBar(points, { format = money, maxDots = 40, compact = false } = {}) {
  const vals = points.map((p) => p.value).filter((v) => v > 0).sort((a, b) => a - b);
  const box = el("div", { class: "range" });
  if (!vals.length) return box;
  const q = (f) => { const i = (vals.length - 1) * f, lo = Math.floor(i), hi = Math.ceil(i); return vals[lo] + (vals[hi] - vals[lo]) * (i - lo); };
  const min = vals[0], max = vals[vals.length - 1], med = q(0.5), p10 = q(0.1), p90 = q(0.9);
  const span = max - min || 1;
  const pos = (v) => `${(100 * (v - min)) / span}%`;
  const t = tip();
  const track = el("div", { class: "track", role: "img",
    "aria-label": `Prices from ${format(min)} to ${format(max)}; most hospitals between ${format(p10)} and ${format(p90)}; usual price ${format(med)}.` });
  if (vals.length >= 3) track.append(el("div", { class: "band", style: `left:${pos(p10)};width:calc(${pos(p90)} - ${pos(p10)})` }));
  // hospitals sharing a price share a dot
  const groups = new Map();
  for (const p of points.filter((p) => p.value > 0)) {
    const k = p.value.toFixed(2);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(p);
  }
  // past maxDots the dots would pile into an unreadable smear: show the band and median only
  const showDots = groups.size <= maxDots;
  [...groups.values()].sort((a, b) => a[0].value - b[0].value).filter(() => showDots).forEach((ps, i) => {
    const d = el("span", { class: "hdot", style: `left:${pos(ps[0].value)};--i:${i}` });
    const label = ps.length === 1 ? ps[0].label : `${ps.length} hospitals: ${ps.slice(0, 3).map((p) => p.label).join(", ")}${ps.length > 3 ? ", …" : ""}`;
    d.addEventListener("pointermove", (e) => t.show(e, format(ps[0].value), label));
    d.addEventListener("pointerleave", () => t.hide());
    track.append(d);
  });
  track.append(el("div", { class: "median", style: `left:${pos(med)}` }, el("span", {}, `Usual ${format(med)}`)));
  if (compact) { box.append(track); return box; }
  box.append(track, el("div", { class: "ends" },
    el("div", {}, el("strong", {}, format(min)), "lowest"),
    el("div", {}, el("strong", {}, format(max)), "highest")));
  return el("div", {}, box, el("div", { class: "legend-line", "aria-hidden": "true" },
    showDots ? el("span", {}, el("i", { class: "lg-dot" }), "a hospital") : "",
    vals.length >= 3 ? el("span", {}, el("i", { class: "lg-band" }), "where most hospitals are (middle 80%)") : "",
    el("span", {}, el("i", { class: "lg-med" }), "usual price: half charge less, half more")));
}
