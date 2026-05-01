"""Capture switchgear-commissioning HMI views: SLD with elements selected
in both SCHEMATIC and EQUIPMENT views, plus the SEL-751 relays on the MV
incoming and bus-tie breakers."""

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://localhost:8080"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
OUT.mkdir(exist_ok=True)


async def main():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1100})
        await ctx.add_init_script("try{localStorage.setItem('api_key','demo')}catch(e){}")
        page = await ctx.new_page()

        async def goto(path):
            await page.goto(BASE + path, wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(1500)

        # SLD with MV-MAIN-A selected (schematic)
        await goto("/sld")
        # Try to click the MV-MAIN-A card text
        try:
            await page.get_by_text("MV-MAIN-A", exact=False).first.click(timeout=4000)
            await page.wait_for_timeout(800)
        except Exception:
            pass
        await page.screenshot(path=str(OUT / "21-sld-mv-main-A-selected.png"), full_page=True)

        # Toggle to EQUIPMENT view
        try:
            await page.get_by_role("button", name="EQUIPMENT").click(timeout=4000)
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        await page.screenshot(path=str(OUT / "22-sld-equipment-view.png"), full_page=True)

        # XFMR-A1 element on SLD selected
        await goto("/sld")
        try:
            await page.get_by_text("XFMR-A1", exact=False).first.click(timeout=4000)
            await page.wait_for_timeout(800)
        except Exception:
            pass
        await page.screenshot(path=str(OUT / "23-sld-xfmr-A1-selected.png"), full_page=True)

        # Additional relay consoles for incomer + tie
        await goto("/relays/sel-mv-main-B")
        await page.screenshot(path=str(OUT / "24-relay-mv-main-B.png"), full_page=True)

        await goto("/relays/sel-mv-tie")
        await page.screenshot(path=str(OUT / "25-relay-mv-tie.png"), full_page=True)

        # Power graph zoomed: nothing else needed.

        await browser.close()
        print("done")


if __name__ == "__main__":
    asyncio.run(main())
