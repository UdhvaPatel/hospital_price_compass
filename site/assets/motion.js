// Motion: scroll reveals, count-ups, header + progress bar, cross-fading result swaps.
// Everything checks prefers-reduced-motion and shows the final state instantly when it is set.

export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Fade/slide elements in as they scroll into view. Children of [data-stagger] get increasing delays. */
let observer;
export function reveal(root = document) {
  const items = root.querySelectorAll("[data-reveal]:not(.is-visible)");
  root.querySelectorAll("[data-stagger]").forEach((parent) =>
    [...parent.children].forEach((c, i) => { c.style.setProperty("--i", i); if (!c.hasAttribute("data-reveal")) c.setAttribute("data-reveal", ""); }));
  const all = root.querySelectorAll("[data-reveal]:not(.is-visible)");
  if (reducedMotion() || !("IntersectionObserver" in window)) { all.forEach((e) => e.classList.add("is-visible")); return; }
  observer ||= new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("is-visible");
      e.target.querySelectorAll("[data-count]").forEach(countUp);
      if (e.target.hasAttribute("data-count")) countUp(e.target);
      observer.unobserve(e.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  (all.length ? all : items).forEach((e) => observer.observe(e));
}

/** Count a number up from 0. The element's data-count holds the final value, data-format "money"|"int". */
export function countUp(node) {
  if (node.dataset.counted) return;
  node.dataset.counted = "1";
  const end = Number(node.dataset.count);
  const fmt = node.dataset.format === "money"
    ? (v) => (end >= 100 ? "$" + Math.round(v).toLocaleString("en-US") : "$" + v.toFixed(2))
    : (v) => Math.round(v).toLocaleString("en-US");
  if (reducedMotion() || !Number.isFinite(end)) { node.textContent = fmt(end); return; }
  const dur = 1100, t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), eased = 1 - Math.pow(1 - k, 3);
    node.textContent = fmt(end * eased);
    if (k < 1) requestAnimationFrame(step); else node.textContent = fmt(end);
  };
  requestAnimationFrame(step);
}

/** Count-up for numbers already on screen (e.g. the answer card after a new search). */
export function countAll(root) { root.querySelectorAll("[data-count]").forEach(countUp); }

/** Shrink + blur the sticky header after scrolling; drive the top progress bar. */
export function scrollChrome() {
  const header = document.querySelector("header.site");
  const bar = document.querySelector(".progress");
  let ticking = false;
  const update = () => {
    const y = scrollY, h = document.documentElement.scrollHeight - innerHeight;
    header?.classList.toggle("scrolled", y > 12);
    if (bar) bar.style.setProperty("--p", h > 0 ? Math.min(1, y / h).toFixed(4) : 0);
    ticking = false;
  };
  addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  update();
}

/** Replace a container's content with a cross-fade (View Transitions API when available). */
export async function swap(container, build) {
  const apply = () => { container.replaceChildren(...build()); reveal(container); countAll(container); };
  if (document.startViewTransition && !reducedMotion()) {
    await document.startViewTransition(apply).finished.catch(() => {});
  } else {
    apply();
  }
}

/** Premium "spotlight": a soft light follows the pointer across cards (.card, .spot). */
export function spotlight(root = document) {
  if (reducedMotion() || !matchMedia("(hover: hover)").matches) return;
  root.addEventListener("pointermove", (e) => {
    const card = e.target.closest?.(".card, .spot");
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - r.left}px`);
    card.style.setProperty("--my", `${e.clientY - r.top}px`);
  }, { passive: true });
}
