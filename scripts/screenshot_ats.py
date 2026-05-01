"""Re-screenshot the ATS console after the timeline legibility fix."""
import asyncio, os
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "screenshots"

async def main():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1200})
        await ctx.add_init_script("try{localStorage.setItem('api_key','demo')}catch(e){}")
        page = await ctx.new_page()
        await page.goto("http://localhost:8080/ats/ats-1", wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "08-ats.png"), full_page=True)
        # Crop just the timeline card for a focused view
        card = page.locator("text=Transfer timeline").locator("xpath=ancestor::*[contains(@class,'card')][1]")
        await card.screenshot(path=str(OUT / "26-ats-transfer-timeline.png"))
        await browser.close()
        print("done")

asyncio.run(main())
