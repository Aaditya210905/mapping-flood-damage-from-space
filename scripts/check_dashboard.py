"""Check the saved actual-data dashboard using optional Playwright/Chromium."""
import json
from pathlib import Path
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
output = ROOT / "docs/qa"
output.mkdir(exist_ok=True)
base = "http://127.0.0.1:8000"
with urllib.request.urlopen(base + "/api/health", timeout=30) as response:
    assert json.load(response)["status"] == "ok"
checks = {"page_errors": [], "responsive": [], "checks": []}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.on("pageerror", lambda error: checks["page_errors"].append(str(error)))
    page.goto(base, wait_until="networkidle", timeout=60000)
    page.wait_for_function("state.run?.mode === 'live'", timeout=60000)
    assert page.locator("#stat-water").inner_text() == page.evaluate("state.run.stats.water_km2.toFixed(2)")
    checks["run_id"] = page.evaluate("state.run.id")
    for name in ("Kuro Siwo", "Sen1Floods11", "Copernicus EMSR927"):
        assert name in page.locator("#dataset-status").inner_text()
    checks["checks"].append("Actual case loads by default; source links and external model status displayed")
    for width in (360, 768, 1280, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(200)
        sizes = page.evaluate("({viewport:innerWidth,document:document.documentElement.scrollWidth})")
        assert sizes["document"] <= sizes["viewport"], sizes
        checks["responsive"].append(sizes)
        if width in (360, 1280):
            page.screenshot(path=str(output / f"actual-dashboard-{width}.png"), full_page=True)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.locator("#before-button").click()
    page.locator("#after-button").click()
    page.get_by_role("tab", name="Road access").click()
    assert page.locator("#access-view").is_visible()
    page.get_by_role("tab", name="Data & method").click()
    assert "not a flood-water accuracy score" in page.locator("#evaluation-status").inner_text()
    page.get_by_role("tab", name="Impact map").click()
    page.get_by_role("button", name="Which settlements are cut off?").click()
    page.wait_for_selector(".chat-answer")
    count = str(page.evaluate("state.run.stats.settlements_cut_off"))
    assert count + " settlements" in page.locator(".chat-answer").last.inner_text()
    page.locator("#language").select_option("ne")
    page.get_by_role("button", name="What infrastructure is exposed?").click()
    page.wait_for_function("document.querySelectorAll('.chat-answer').length === 2")
    assert str(page.evaluate("state.run.stats.bridges_exposed")) in page.locator(".chat-answer").last.inner_text()
    checks["checks"].append("Before/after and access/method views; class-mismatch disclosure; map-grounded English/Nepali answers")
    page.goto(base + f"/api/analyses/{checks['run_id']}/report?language=ne", wait_until="networkidle")
    page.pdf(path=str(output / "actual-report-ne.pdf"), format="A4", prefer_css_page_size=True, print_background=True)
    checks["checks"].append("Nepali situation report printed with Chromium")
    browser.close()
assert not checks["page_errors"], checks["page_errors"]
(output / "actual-browser-checks.json").write_text(json.dumps(checks, indent=2))
print(json.dumps(checks, indent=2))
