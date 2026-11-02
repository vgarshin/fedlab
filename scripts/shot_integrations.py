"""One-off: screenshot the /integrations demo page (dev stack must be up)."""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:5173")
EMAIL = os.environ.get("EMAIL", "vgarshin@yandex.ru")
OUT = Path("/out") / (sys.argv[1] if len(sys.argv) > 1 else "current")
VIEWS = {"light": dict(viewport={"width": 1440, "height": 900}, color_scheme="light"),
         "dark": dict(viewport={"width": 1440, "height": 900}, color_scheme="dark"),
         "mobile": dict(viewport={"width": 390, "height": 844}, color_scheme="light", device_scale_factor=2)}

def settle(page):
    page.wait_for_load_state("load")
    page.wait_for_timeout(1200)

with sync_playwright() as p:
    browser = p.chromium.launch()
    for view, opts in VIEWS.items():
        ctx = browser.new_context(**opts)
        page = ctx.new_page()
        out = OUT / view
        out.mkdir(parents=True, exist_ok=True)
        page.goto(f"{BASE}/api/auth/dev-login?email={EMAIL}")
        settle(page)
        page.goto(BASE + "/integrations")
        settle(page)
        page.screenshot(path=str(out / "integrations.png"), full_page=True)
        ctx.close()
    browser.close()
print("done:", OUT)
