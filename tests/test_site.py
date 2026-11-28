"""Phase 7 tests: the exported site data, the static pages, and (if a browser is available) every
page rendered headless: it loads, shows the data date and safety notice, and doesn't overflow."""
import functools
import http.server
import json
import re
import shutil
import socket
import subprocess
import threading

import pytest

from hpc.config import ROOT

SITE = ROOT / "site"
DATA = SITE / "data"
PAGES = ["index.html", "procedure.html", "hospital.html", "insurer.html", "quality.html", "about.html"]
needs_data = pytest.mark.skipif(not (DATA / "meta.json").exists(), reason="run python -m hpc.site.build first")


# ------------------------------------------------------------------ exported data

@needs_data
def test_all_json_parses_strictly():
    files = list(DATA.rglob("*.json"))
    assert len(files) > 60
    for f in files:
        txt = f.read_text(encoding="utf-8")
        assert "NaN" not in txt and "Infinity" not in txt, f
        json.loads(txt)


@needs_data
def test_meta_and_cross_references():
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["as_of"])
    assert "not medical, insurance or financial advice" in meta["safety_notice"]
    hospitals = {h["ccn"]: h for h in json.loads((DATA / "hospitals.json").read_text(encoding="utf-8"))}
    for p in meta["procedures"]:
        d = json.loads((DATA / "procedure" / f"{p['id']}.json").read_text(encoding="utf-8"))
        assert d["id"] == p["id"]
        for r in d["prices"][:50] + d["cash"]:
            assert r["ccn"] in hospitals                         # every price points at a known hospital
        for r in d["prices"]:
            assert r["price"] > 0
    insurer_ids = {i["id"] for i in meta["insurers"]}
    assert set(meta["top_insurers"]) <= insurer_ids


@needs_data
def test_site_contains_no_secrets_or_raw_data():
    for f in SITE.rglob("*"):
        if f.is_file():
            txt = f.read_text(encoding="utf-8", errors="ignore")
            assert not re.search(r"nvapi-[A-Za-z0-9_-]{20,}", txt), f"API key in {f}"
            assert f.suffix in {".html", ".css", ".js", ".json"}, f"unexpected file type published: {f}"
    assert not (SITE / ".env").exists()


# ------------------------------------------------------------------ static pages

@pytest.mark.parametrize("page", PAGES)
def test_page_skeleton(page):
    html = (SITE / page).read_text(encoding="utf-8")
    assert re.search(r'<html lang="en"[ >]', html) and 'name="viewport"' in html
    assert 'id="main"' in html and 'href="assets/site.css"' in html
    script = re.search(r'src="(assets/pages/[a-z]+\.js)"', html).group(1)
    assert (SITE / script).exists()


def test_motion_respects_reduced_motion():
    css = (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    block = css.split("@media (prefers-reduced-motion: reduce)")[1].split("@media")[0]
    assert "animation: none" in block and "transition: none" in block and "[data-reveal]" in block
    motion = (SITE / "assets" / "motion.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in motion
    assert 'import("./motion.js")' in (SITE / "assets" / "common.js").read_text(encoding="utf-8")


def test_nav_links_point_at_real_pages():
    js = (SITE / "assets" / "common.js").read_text(encoding="utf-8")
    for href in re.findall(r'\["([a-z]+\.html)",', js):
        assert (SITE / href).exists(), href


def test_no_innerhtml_with_data():
    for f in (SITE / "assets").rglob("*.js"):
        code = f.read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*[+]?=|insertAdjacentHTML", code), f"{f}: use textContent / el()"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_javascript_syntax():
    for f in (SITE / "assets").rglob("*.js"):
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{f}: {r.stderr}"


# ------------------------------------------------------------------ rendered in a headless browser

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/"
    httpd.shutdown()


def _browser_ok():
    from hpc.site.render import browser
    return browser() is not None


RENDERED = [
    ("index.html?p=mri_knee&i=uhc&t=commercial&zip=85004", ["at one Phoenix hospital and", "Who is this for?", "With UnitedHealthcare, a knee MRI usually costs about",
                                                            "What will I actually pay?", "What to do next", "Billing code 73721",
                                                            "cheapest first", "Show these hospitals on a map", "Can I trust this?"]),
    ("index.html?p=lab_comprehensive_metabolic&i=cash", ["If you pay yourself", "hospitals not in this list"]),
    ("index.html?p=mri_knee&i=uhc&view=table", ["Compared with the usual price", "Price list"]),
    ("procedure.html", ["34 common scans, tests, surgeries"]),
    ("procedure.html?id=joint_replacement_inpatient", ["Does your insurance company matter?", "what Medicare pays", "Is paying yourself ever cheaper?"]),
    ("hospital.html", ["Which hospitals cost more?", "Prices vs usual"]),
    ("hospital.html?ccn=030024", ["Compared with other Phoenix hospitals", "cheaper or more expensive than usual", "How the score adds up"]),
    ("hospital.html?ccn=030103", ["wasn't processed"]),
    ("insurer.html", ["Does your insurance company get you a better price?", "average company", "All insurance companies"]),
    ("insurer.html?id=cigna", ["Medicare Advantage plans", "Where it pays more or less"]),
    ("quality.html", ["What we look for", "We re-read prices from the original files", "Which hospitals publish a good price list?"]),
    ("about.html", ["Why this site exists", "What this site can't tell you", "Not advice"]),
]


@needs_data
@pytest.mark.skipif(not _browser_ok(), reason="no Edge/Chrome for headless rendering")
@pytest.mark.parametrize("page,expected", RENDERED)
def test_rendered_page(server, page, expected):
    from hpc.site.render import dom, text
    d = dom(server + page)
    t = text(d)
    assert 'role="alert"' not in d, f"error box on {page}"
    assert "Data as of" in t                                         # data date on every page
    assert "not medical, insurance or financial advice" in t          # safety notice on every page
    for e in expected:
        assert e in t, f"{page}: missing '{e}'"
    m = re.search(r'data-layout="(\d+)x(\d+)"', d)
    assert m, "layout self-check didn't run"
    culprit = re.search(r'data-overflow="([^"]*)"', d)
    assert int(m.group(2)) <= int(m.group(1)),         f"{page}: content wider than the window ({m.group(0)}; widest element: {culprit.group(1) if culprit else '?'})"
