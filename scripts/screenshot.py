"""Drive the live dev_server UI through a headless browser and capture
PNGs of every major route. Saves to ./screenshots/."""

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://localhost:8080"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
OUT.mkdir(exist_ok=True)

ROUTES = [
    ("01-dashboard",          "/"),
    ("02-sld",                "/sld"),
    ("03-relay-sel",          "/relays/sel-mv-main-A"),
    ("04-transformer",        "/xfmr/xfmr-A1"),
    ("05-generator",          "/gens/gen-1"),
    ("06-ups",                "/ups/ups-A"),
    ("07-hipot",              "/hipot/mv-main-A"),
    ("08-ats",                "/ats/ats-1"),
    ("09-discovery",          "/discovery"),
    ("10-devices",            "/devices"),
    ("11-racks",              "/racks"),
    ("12-power",              "/power"),
    ("13-tests",              "/tests"),
    ("14-test-live",          "/tests/t-0002"),
    ("15-timeline",           "/timeline"),
    ("16-punchlist",          "/punchlist"),
    ("17-checklist",          "/checklist"),
    ("18-attestation",        "/attestation"),
    ("19-reports",            "/reports"),
    ("20-import",             "/import"),
]


async def main():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1600, "height": 1000})
        # Pre-seed the API key so we skip the prompt screen.
        await context.add_init_script("""
            try { window.localStorage.setItem('api_key', 'demo'); } catch(e) {}
        """)
        page = await context.new_page()
        page.on("pageerror", lambda e: print(f"[pageerror] {e}", file=sys.stderr))
        page.on("console", lambda m: m.type == "error" and print(f"[console.{m.type}] {m.text}", file=sys.stderr))

        for name, path in ROUTES:
            url = BASE + path
            try:
                await page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception as e:
                print(f"[warn] {name} {url}: {e}", file=sys.stderr)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception as e2:
                    print(f"[fail] {name}: {e2}", file=sys.stderr)
                    continue
            await page.wait_for_timeout(1200)
            out = OUT / f"{name}.png"
            await page.screenshot(path=str(out), full_page=True)
            print(f"  {out.name}  {url}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
