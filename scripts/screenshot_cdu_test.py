"""Walk through commissioning a Supermicro CDU on the platform AS-IS.
Captures what an operator sees at each step — including the failures."""

import asyncio, os
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "screenshots"

ATTEMPTS = [
    # 1) The dashboard. Operator opens the platform.
    ("cdu-01-dashboard",        "/"),
    # 2) Single-line. Operator looks for cooling distribution.
    ("cdu-02-sld",              "/sld"),
    # 3) Devices list. Operator filters for cooling/CDU.
    ("cdu-03-devices",          "/devices"),
    # 4) Discovery — try to scan for a Supermicro CDU subnet.
    ("cdu-04-discovery",        "/discovery"),
    # 5) Direct equipment URL guess — what pages exist?
    ("cdu-05-cdu-route",        "/cdu/sm-cdu-1"),
    ("cdu-06-cooling-route",    "/cooling/sm-cdu-1"),
    ("cdu-07-chiller-route",    "/chiller/chiller-1"),
    # 6) Active Tests page — try to launch a CDU test.
    ("cdu-08-active-tests",     "/tests"),
    # 7) Punchlist — see what categories exist.
    ("cdu-09-punchlist",        "/punchlist"),
    # 8) Import — try to upload a Supermicro CDU spec.
    ("cdu-10-import",           "/import"),
    # 9) Reports — what report types exist for cooling.
    ("cdu-11-reports",          "/reports"),
]


async def main():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1100})
        await ctx.add_init_script("try{localStorage.setItem('api_key','demo')}catch(e){}")
        page = await ctx.new_page()
        for name, path in ATTEMPTS:
            url = "http://localhost:8080" + path
            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print(f"  {name}.png  {path}")
        await browser.close()

asyncio.run(main())
