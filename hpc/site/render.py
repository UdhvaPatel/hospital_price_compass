"""Render site pages in headless Microsoft Edge (or Chrome) to check them without a browser extension.

    python -m hpc.site.render "index.html?p=mri_knee&i=aetna" [--shot out.png --width 1280 --height 2000]

Prints the visible text of the rendered page (after JavaScript ran). Used by the site tests.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
from pathlib import Path

CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
]


def browser() -> str | None:
    for c in CANDIDATES:
        if os.path.exists(c):
            return c
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        if p := shutil.which(name):
            return p
    return None


def _run(args: list[str], timeout: int = 60, reduced_motion: bool = True) -> str:
    exe = browser()
    if not exe:
        raise RuntimeError("no Edge/Chrome found for headless rendering")
    res = subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                          "--virtual-time-budget=10000",
                          # final state of every animation (headless captures frames mid-animation otherwise)
                          *(["--force-prefers-reduced-motion"] if reduced_motion else []),
                          *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    return res.stdout


def dom(url: str, reduced_motion: bool = True) -> str:
    return _run(["--dump-dom", url], reduced_motion=reduced_motion)


def text(dom_html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", dom_html, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def screenshot(url: str, path: str | Path, width: int = 1280, height: int = 2200, reduced_motion: bool = True) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([f"--screenshot={path}", f"--window-size={width},{height}", url], reduced_motion=reduced_motion)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--base", default="http://127.0.0.1:8765/")
    ap.add_argument("--shot")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=2200)
    ap.add_argument("--chars", type=int, default=3000)
    ap.add_argument("--motion", action="store_true", help="keep animations (frame is captured mid-animation)")
    args = ap.parse_args()
    url = args.base + args.page
    if args.shot:
        print(screenshot(url, args.shot, args.width, args.height, reduced_motion=not args.motion))
    d = dom(url, reduced_motion=not args.motion)
    print(text(d)[:args.chars])
    print(f"\n[rows: {d.count('<tr')} | circles: {d.count('<circle')} | error box: {'role=\"alert\"' in d}]")


if __name__ == "__main__":
    main()
