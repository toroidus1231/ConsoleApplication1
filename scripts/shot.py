"""Screenshot the running demo dashboard with headless Chromium."""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
OUT = "/tmp/epms"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=2)
        page = ctx.new_page()
        # Pre-seed the API key so the ApiKeyGate passes straight through.
        page.add_init_script("window.localStorage.setItem('api_key','demo-key')")

        page.goto(BASE + "/", wait_until="load")
        page.wait_for_selector("text=Facility Overview", timeout=20000)
        page.wait_for_selector("text=Infrastructure", timeout=20000)  # health loaded
        page.wait_for_timeout(5000)  # let SSE activity accumulate
        page.screenshot(path=f"{OUT}_dashboard.png", full_page=True)
        print("wrote dashboard")

        # EPMS device fleet view.
        page.click("text=Devices")
        page.wait_for_timeout(2500)
        page.screenshot(path=f"{OUT}_devices.png", full_page=True)
        print("wrote devices")

        # An EPMS power-meter detail (live registers).
        try:
            page.click("text=CM2000-A3-Main")
            page.wait_for_timeout(2500)
            page.screenshot(path=f"{OUT}_device_detail.png", full_page=True)
            print("wrote device_detail")
        except Exception as e:  # noqa: BLE001
            print("device_detail skipped:", e)

        browser.close()


if __name__ == "__main__":
    sys.exit(main())
