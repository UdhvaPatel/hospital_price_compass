import { shell, loadJSON, el, money, times, pct, table, errorBox, param, hero, icon, infoTip, fold, procPhrase, planWords, plainText } from "../common.js";
import { dotStrip, withTableView, rangeBar } from "../charts.js";
import { reveal } from "../motion.js";

const content = document.getElementById("content");
const CAT_ICON = { Imaging: "scan", Procedures: "scalpel", Surgery: "scalpel", "Hospital stays": "bed", Emergency: "siren", "Lab tests": "flask", Tests: "heart" };
const median = (a) => { const v = [...a].sort((x, y) => x - y); return v.length ? (v[Math.floor((v.length - 1) / 2)] + v[Math.ceil((v.length - 1) / 2)]) / 2 : null; };

async function main() {
  const meta = await shell();
  const id = param("id");
  const proc = meta.procedures.find((p) => p.id === id);
  if (!proc) return list(meta);
  const [data, hospitals] = await Promise.all([loadJSON(`data/procedure/${id}.json`), loadJSON("data/hospitals.json")]);
  const H = Object.fromEntries(hospitals.map((h) => [h.ccn, h]));
  const name = Object.fromEntries(meta.insurers.map((i) => [i.id, i.name]));
  document.title = `${proc.name} | Hospital Price Compass`;
  hero({ eyebrow: proc.category, title: proc.name,
    lead: [`What ${procPhrase(id, proc.name)} costs at Phoenix hospitals, with each of the big insurance companies and if you pay yourself. Billing code ${proc.code} (useful when you call your insurer).`,
      el("span", { style: "display:block;margin-top:1.1rem" }, el("a", { class: "btn primary", href: `index.html?p=${id}#results` }, "See every hospital's price ", icon("arrow", 16)))] });

  const views = [...meta.top_insurers.map((pid) => ({ key: pid, label: name[pid] || pid,
    rows: data.prices.filter((r) => r.payer_id === pid && r.plan_type === "commercial") })),
  { key: "cash", label: "Paying yourself", rows: data.cash.map((r) => ({ ccn: r.ccn, price: r.cash })) }].filter((v) => v.rows.length);

  const all = views.flatMap((v) => v.rows);
  const nodes = [];
  if (all.length) {
    nodes.push(el("section", { "data-reveal": "" }, el("div", { class: "card answer" },
      el("div", {}, el("div", { class: "big-label" }, "Usual price ", infoTip("Half of the hospital prices below are lower than this and half are higher. It covers the five biggest insurance companies (plans through work or bought yourself) and self-pay prices.")),
        el("div", { class: "big grad", "data-count": median(all.map((r) => r.price)).toFixed(2), "data-format": "money" }, money(median(all.map((r) => r.price)))),
        el("p", { class: "sub" }, `${new Set(all.map((r) => r.ccn)).size} Phoenix hospitals publish a price for this.`)),
      rangeBar(all.map((r) => ({ value: r.price, label: H[r.ccn]?.name || r.ccn }))))));
  }
  nodes.push(el("div", { class: "notice", "data-reveal": "" }, icon("shield", 20), el("div", {}, "These are the prices hospitals publish. What you pay depends on your plan: ask your insurer for your share.",
    fold("Why your bill may differ", el("p", { class: "small" }, meta.safety_notice)))));

  // ---- distribution by insurer
  const chart = dotStrip(views.map((v) => ({ label: v.label, median: median(v.rows.map((r) => r.price)),
    points: v.rows.map((r) => ({ value: r.price, label: H[r.ccn]?.name || r.ccn })) })),
  { xTitle: "Price at each hospital (insurance through work or bought yourself; the last row is self-pay)" });
  const ccns = [...new Set(views.flatMap((v) => v.rows.map((r) => r.ccn)))];
  const trows = ccns.map((c) => Object.fromEntries([["name", H[c]?.name || c], ["ccn", c], ...views.map((v) => [v.key, v.rows.find((r) => r.ccn === c)?.price ?? null])]));
  const tview = table([{ key: "name", label: "Hospital", render: (r) => el("a", { href: `hospital.html?ccn=${r.ccn}` }, r.name) },
    ...views.map((v) => ({ key: v.key, label: v.label, num: true, render: (r) => money(r[v.key]) }))], trows, { caption: "Price at each hospital", sortKey: "name" });
  nodes.push(el("section", { "data-reveal": "" },
    el("p", { class: "eyebrow" }, "By insurance company"), el("h2", {}, "Does your insurance company matter?"),
    el("p", { class: "muted small" }, "Each dot is one hospital's price. A number above a dot means several hospitals share that exact price (usually one hospital group). The small line is the usual price."),
    el("div", { class: "card" }, withTableView("Price distribution", chart, tview))));

  // ---- plain English
  const cards = views.filter((v) => data.summaries[v.key]).map((v) => el("div", { class: "card lift" }, el("h3", {}, v.label), el("p", { class: "muted" }, plainText(data.summaries[v.key].text))));
  if (cards.length) nodes.push(el("section", {}, el("div", { "data-reveal": "" }, el("p", { class: "eyebrow" }, "In short"), el("h2", {}, "What the prices say")),
    el("div", { class: "grid two", "data-stagger": "" }, cards)));

  // ---- Medicare (hospital stays)
  if (proc.family === "drg" && data.medicare_rate) {
    const byType = {};
    for (const r of data.prices) if (r.medicare_multiple != null) (byType[r.plan_type] ||= []).push(r.medicare_multiple);
    const rows = Object.entries(byType).filter(([, v]) => v.length >= 3).map(([t, v]) => ({ type: planWords(t, meta.plan_types), n: v.length, median: median(v) }));
    nodes.push(el("section", { "data-reveal": "" }, el("p", { class: "eyebrow" }, "For comparison"), el("h2", {}, "How this compares with what Medicare pays"),
      el("div", { class: "card" },
        el("p", {}, "Medicare pays about ", el("strong", {}, money(data.medicare_rate)), " for this stay in Phoenix (its 2026 base rate). Here is how many times that amount each kind of insurance usually pays. ",
          infoTip("The base rate leaves out extra payments for teaching hospitals, low-income patients and unusually expensive cases.")),
        table([{ key: "type", label: "Kind of insurance" }, { key: "median", label: "Usually pays", num: true, render: (r) => `${times(r.median)} what Medicare pays` },
          { key: "n", label: "Prices", num: true }], rows, { caption: "Compared with Medicare", sortKey: "median" }))));
  }
  // ---- cash vs insured
  const cv = data.cash_vs_insured;
  if (cv && cv.pairs >= 3) {
    nodes.push(el("section", { "data-reveal": "" }, el("p", { class: "eyebrow" }, "Paying yourself or using insurance?"), el("h2", {}, "Is paying yourself ever cheaper?"),
      el("div", { class: "grid three" },
        el("div", { class: "card stat" }, el("div", { class: "value grad" }, pct(cv.share_cash_below_median_insured)), el("div", { class: "label" }, `of ${cv.pairs} hospitals charge less for self-pay than the usual insurance price`)),
        el("div", { class: "card stat" }, el("div", { class: "value" }, money(cv.median_cash)), el("div", { class: "label" }, "usual self-pay price")),
        el("div", { class: "card stat" }, el("div", { class: "value" }, money(cv.median_insured)), el("div", { class: "label" }, "usual insurance price (through work or bought yourself)"))),
      el("p", { class: "muted small" }, "Paying yourself may not count toward your deductible. Check with your insurer first.")));
  }
  content.replaceChildren(...nodes);
  reveal(content);
}

function list(meta) {
  hero({ eyebrow: "Procedures", title: "What do you need?", lead: `${meta.procedures.length} common scans, tests, surgeries and hospital stays that people can plan ahead for. Pick one to see what it costs at every Phoenix hospital.` });
  const cats = [...new Set(meta.procedures.map((p) => p.category))];
  content.replaceChildren(...cats.map((c) => el("section", {},
    el("div", { "data-reveal": "" }, el("h2", {}, c)),
    el("div", { class: "grid auto", "data-stagger": "" }, meta.procedures.filter((p) => p.category === c).map((p) =>
      el("a", { class: "card lift", href: `procedure.html?id=${p.id}` },
        el("div", { class: "cat-icon" }, icon(CAT_ICON[c] || "heart", 20)),
        el("h3", {}, p.name), el("p", { class: "muted small" }, `Billing code ${p.code}`)))))));
  reveal(content);
}

main().catch((e) => errorBox(content, e));
