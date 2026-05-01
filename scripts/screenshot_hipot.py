"""Capture the rewired hipot panel."""
import asyncio, os
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "screenshots"

async def main():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1100})
        await ctx.add_init_script("try{localStorage.setItem('api_key','demo')}catch(e){}")
        page = await ctx.new_page()
        await page.goto("http://localhost:8080/hipot/mv-main-A",
                        wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "07-hipot.png"), full_page=True)
        print("done")

asyncio.run(main())
