"""Screenshot every page of the DEV app (needs the dev stack + a demo run), so design work can be reviewed by looking.

    scripts/screenshots.sh [out-subfolder]        -> _design/shots/<out-subfolder>/{light,dark,mobile}/<page>.png
Runs inside the official Playwright image (headless Chromium); logs in with the dev-login shortcut (env=dev only).
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:5173")
RUN = os.environ.get("RUN", "9000")
OUT = Path(os.environ.get("OUT", "/out")) / (sys.argv[1] if len(sys.argv) > 1 else "current")
EMAIL = os.environ.get("EMAIL", "vgarshin@yandex.ru")
ALL_VIEWS = {"light": dict(viewport={"width": 1440, "height": 900}, color_scheme="light"),
         "dark": dict(viewport={"width": 1440, "height": 900}, color_scheme="dark"),
         "mobile": dict(viewport={"width": 390, "height": 844}, color_scheme="light", device_scale_factor=2),
         "laptop": dict(viewport={"width": 1280, "height": 720}, color_scheme="light"), "small": dict(viewport={"width": 1024, "height": 700}, color_scheme="light")}
VIEWS = {k: v for k, v in ALL_VIEWS.items() if k in os.environ.get("VIEWS", "light,dark,mobile").split(",")}
SAMPLE_BATCHES = {"name_rule": "", "batches": [
    {"name": "20261028-1015", "status": "uploaded", "origin": "ui", "registered": True, "created_at": "2026-10-28T07:15:00Z", "files": 2, "bytes": 52428800, "zips": ["export.zip"], "csvs": ["Grades-lab.csv"], "unzipped_submissions": 0, "problems": [], "modified_after_use": False, "usable": True, "used_by": []},
    {"name": "demo-zip", "status": "unregistered", "origin": "outside", "registered": False, "created_at": None, "files": 4, "bytes": 1600000, "zips": ["a.zip", "b.zip"], "csvs": ["Grades-a.csv", "Grades-b.csv"], "unzipped_submissions": 0,
     "problems": ["The batch contains 2 zip files; it must contain exactly one. Create a new batch.", "The batch contains 2 CSV files; it must contain at most one. Create a new batch."], "modified_after_use": False, "usable": False, "used_by": []},
    {"name": "demo-unzipped", "status": "used", "origin": "outside", "registered": True, "created_at": "2026-10-23T15:00:00Z", "files": 6, "bytes": 590000, "zips": [], "csvs": ["Grades-x.csv"], "unzipped_submissions": 5, "problems": [], "modified_after_use": False, "usable": True, "used_by": [9000]},
    {"name": "lab-autumn-1", "status": "used", "origin": "cli", "registered": True, "created_at": "2026-10-27T09:00:00Z", "files": 3, "bytes": 800000, "zips": ["x.zip", "late.zip"], "csvs": ["Grades-x.csv"], "unzipped_submissions": 0,
     "problems": ["The batch contains 2 zip files; it must contain exactly one. Create a new batch.", "Files were added, removed or replaced after a run used this batch. Create a new batch for new files."], "modified_after_use": True, "usable": False, "used_by": [9001]},
    {"name": "20261028-0900", "status": "reserved", "origin": "cli", "registered": True, "created_at": "2026-10-28T06:00:00Z", "files": 0, "bytes": 0, "zips": [], "csvs": [], "unzipped_submissions": 0, "problems": ["The batch is empty: upload the files first."], "modified_after_use": False, "usable": False, "used_by": []}]}
MOBILE_ONLY = {"runs", "run", "team", "analytics"}


def settle(page):
    page.wait_for_load_state("load")        # not "networkidle": the run page keeps a live-progress (SSE) connection open
    page.wait_for_timeout(1200)
    try:                                              # wait until every "Loading…" placeholder is gone (slow S3 round trips)
        page.wait_for_function("!document.body.innerText.includes('Loading…')", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(500)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for view, opts in VIEWS.items():
        ctx = browser.new_context(**opts)
        if os.environ.get("DENSITY"):
            ctx.add_init_script(f"try {{ localStorage.setItem('density', '{os.environ['DENSITY']}') }} catch (e) {{}}")
        page = ctx.new_page()
        out = OUT / view
        out.mkdir(parents=True, exist_ok=True)
        page.goto(BASE + "/")
        settle(page)
        page.screenshot(path=str(out / "00_login.png"), full_page=True)
        if os.environ.get("MOCK_TEAMS"):                   # real-looking list of works (e.g. exported from a production run) for layout checks
            body = Path(os.environ["MOCK_TEAMS"]).read_text()
            ctx.route(f"**/api/runs/{RUN}/teams", lambda route: route.fulfill(status=200, content_type="application/json", body=body))
        page.goto(f"{BASE}/api/auth/dev-login?email={EMAIL}")
        settle(page)
        teams = ctx.request.get(f"{BASE}/api/runs/{RUN}/teams").json()
        team = next((t for t in teams if t.get("grade_total") not in (None, 0)), teams[0])["id"]
        PAGES = os.environ.get("PAGES")
        shots = [("runs", "/"), ("run", f"/runs/{RUN}"), ("team", f"/runs/{RUN}/teams/{team}"), ("analytics", "/analytics"),
                 ("playground", "/playground"), ("prompts", "/prompts"), ("models", "/models"), ("settings", "/settings"),
                 ("users", "/users"), ("developers", "/developers"), ("manual", "/manual")]
        for i, (name, path) in enumerate(shots, start=1):
            if PAGES and name not in PAGES.split(","):
                continue
            if view == "mobile" and name not in MOBILE_ONLY:
                continue
            page.goto(BASE + path)
            settle(page)
            if name == "playground":                       # run one (cached) experiment so the page shows its result state
                page.wait_for_timeout(800)
                btn = page.get_by_role("button", name="Run experiment")
                if btn.is_enabled():
                    btn.click()
                    page.wait_for_function("document.querySelector('.pg-answer') && !document.querySelector('.pg-answer').value.startsWith('Waiting') && document.querySelector('.pg-answer').value.length > 20", timeout=120000)
                    page.wait_for_timeout(500)
            page.screenshot(path=str(out / f"{i:02d}_{name}.png"), full_page=True)
            if name == "run" and view == "light" and page.locator("summary", has_text="Source files").count():          # also show the expanded "Source files" block (styled file buttons)
                page.locator("summary", has_text="Source files").first.click()
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / f"{i:02d}b_run_source.png"), full_page=True)
                if os.environ.get("MOCK_BATCHES"):             # the bucket may be unreachable from the workstation: show the table with sample data
                    import json as _json
                    def mock(route):
                        route.fulfill(status=200, content_type="application/json", body=_json.dumps(SAMPLE_BATCHES))
                    page.route("**/api/batches", mock)
                for tag, label in (("c_source_cli", "Copy with the AWS CLI"), ("d_source_existing", "Existing batches")):
                    page.get_by_role("button", name=label).click()
                    page.wait_for_timeout(2500)
                    page.screenshot(path=str(out / f"{i:02d}{tag}.png"), full_page=True)
        ctx.close()
    browser.close()
print("screenshots in", OUT)
