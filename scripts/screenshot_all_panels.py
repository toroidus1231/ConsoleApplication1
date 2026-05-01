"""Capture every equipment-detail panel after the EvidenceStore rewire."""
import asyncio, os
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "screenshots"

ROUTES = [
    ("04-transformer", "/xfmr/xfmr-A1"),
    ("05-generator",   "/gens/gen-1"),
    ("06-ups",         "/ups/ups-A"),
    ("07-hipot",       "/hipot/mv-main-A"),
    ("08-ats",         "/ats/ats-1"),
]

async def main():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1100})
        await ctx.add_init_script("try{localStorage.setItem('api_key','demo')}catch(e){}")
        page = await ctx.new_page()
        for name, path in ROUTES:
            await page.goto("http://localhost:8080" + path,
                            wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print(f"  {name}.png")
        await browser.close()

asyncio.run(main())
