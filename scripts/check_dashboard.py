"""Check the dashboard in an isolated browser using generated demo inputs.

Run against a local server with an isolated FLOOD_LENS_DATA directory. This
check never requests an actual satellite analysis or downloads training data.
"""
import argparse
import json
from pathlib import Path
import shutil
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/qa")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    args.output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(base + "/api/health", timeout=30) as response:
        assert json.load(response)["status"] == "ok"
    checks = {"page_errors": [], "responsive": [], "checks": []}
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    launch_options = {"headless": True, "chromium_sandbox": True}
    if chromium:
        launch_options["executable_path"] = chromium
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda error: checks["page_errors"].append(str(error)))
        page.goto(base, wait_until="networkidle", timeout=60000)
        page.wait_for_function("state.sources !== null")
        workspace = page.evaluate("state.sources.workspace")
        if workspace["completed_actual_runs"] == 0:
            assert page.evaluate("state.run === null")
            assert page.locator("#map-empty").is_visible()
            assert page.locator("#stat-water").inner_text() == "—"
            assert page.locator("#ask-button").is_disabled()
            assert page.locator("#before-button").is_disabled()
            assert page.locator("#report-link").get_attribute("aria-disabled") == "true"
            assert "No actual observations acquired" in page.locator("#workspace-data-status").inner_text()
            checks["checks"].append("Empty workspace has no fabricated counts; result controls are gated")
        ready = page.evaluate("state.sources.water_model.status === 'ready'")
        assert page.locator('#water-model option[value="external"]').is_disabled() == (not ready)
        assert not workspace["training_data_loading"]
        page.keyboard.press("Tab")
        assert page.locator(".skip-link").evaluate("node => node.matches(':focus-visible') && getComputedStyle(node).opacity === '1'")
        page.locator("#map-tab").focus()
        page.keyboard.press("End")
        assert page.locator("#method-view").is_visible()
        assert page.locator("#method-tab").get_attribute("aria-selected") == "true"
        groups = page.locator(".source-group")
        assert groups.count() == 3
        assert groups.nth(0).locator(".source-card").count() == 4
        assert groups.nth(1).locator(".source-card").count() == 2
        assert groups.nth(2).locator(".source-card").count() == 1
        assert "Not loaded" in groups.nth(1).inner_text()
        assert "Never prediction inputs" in groups.nth(2).inner_text()
        page.screenshot(path=str(args.output / "data-sources-desktop.png"), full_page=True)
        checks["checks"].append("Keyboard tabs and separate live/training/evaluation source groups")
        page.locator("#map-tab").click()

        def responsive(stage):
            for width in (360, 768, 1280, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(200)
                sizes = page.evaluate("({viewport:innerWidth,document:document.documentElement.scrollWidth})")
                assert sizes["document"] <= sizes["viewport"], sizes
                checks["responsive"].append({"stage": stage, **sizes})
                if width in (360, 1280):
                    page.screenshot(path=str(args.output / f"dashboard-{stage}-{width}.png"), full_page=True)

        responsive("initial")
        page.set_viewport_size({"width": 1280, "height": 900})
        # Submit synthetic mode even when testing a workspace with a saved run.
        if page.locator("#map-empty").is_visible():
            page.locator("#preview-demo").click()
        else:
            page.locator("#mode").select_option("demo")
            page.locator("#advanced").evaluate("node => node.open = true")
            page.locator("#resolution").select_option("100")
            page.locator("#run-button").click()
        page.wait_for_function("state.run?.mode === 'demo' && !state.busy", timeout=90000)
        assert page.locator("#form-error").is_hidden()
        assert page.locator("#map-empty").is_hidden()
        assert page.locator("#map-badge").inner_text() == "DEMO DATA"
        assert "Synthetic demonstration" in page.locator("#notice").inner_text()
        assert "Synthetic fixture" in page.locator("#dataset-status").inner_text()
        assert page.locator("#stat-water").inner_text() == page.evaluate("state.run.stats.water_km2.toFixed(2)")
        assert page.locator("#ask-button").is_enabled()
        assert page.locator("#satellite-provider").is_disabled()
        assert page.locator("#water-model").is_disabled()
        checks["run_id"] = page.evaluate("state.run.id")
        checks["checks"].append("Explicit synthetic preview completes and keeps input/model disclosure accurate")
        responsive("synthetic")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.locator("#before-button").click()
        assert page.evaluate("state.epoch") == "before"
        page.locator("#after-button").click()
        assert page.evaluate("state.epoch") == "after"
        page.locator('[data-layer="debris"]').uncheck()
        assert page.evaluate("!map.hasLayer(state.layers.debris)")
        page.locator('[data-layer="debris"]').check()
        page.locator("#access-tab").click()
        assert page.locator("#access-view").is_visible()
        assert page.locator("#settlement-table tr").count() == page.evaluate("state.run.layers.settlements.features.length")
        page.screenshot(path=str(args.output / "road-access-desktop.png"), full_page=True)
        page.locator("#method-tab").click()
        assert page.locator("#evidence-mode").inner_text() == "Synthetic inputs"
        assert "None for this run" in page.locator("#provenance-content").inner_text()
        assert "No official reference" in page.locator("#evaluation-status").inner_text()
        disclosure = page.evaluate("""() => {
            const original = state.run;
            renderRun({...original, evaluation: {metrics: {iou: null, f1: 0.1}, limitations: 'Synthetic UI verification fixture.'}});
            const disclosure = document.getElementById('evaluation-status').textContent;
            renderRun(original);
            return disclosure;
        }""")
        assert "Synthetic sanity check" in disclosure
        assert "IoU undefined" in disclosure
        page.locator("#map-tab").click()
        page.get_by_role("button", name="Which settlements are cut off?").click()
        page.wait_for_selector(".chat-answer")
        count = str(page.evaluate("state.run.stats.settlements_cut_off"))
        assert count + " settlements" in page.locator(".chat-answer").last.inner_text()
        page.wait_for_function("!state.asking")
        page.locator("#language").select_option("ne")
        page.get_by_role("button", name="What infrastructure is exposed?").click()
        page.wait_for_function("document.querySelectorAll('.chat-answer').length === 2 && !state.asking")
        assert str(page.evaluate("state.run.stats.bridges_exposed")) in page.locator(".chat-answer").last.inner_text()
        checks["checks"].append("Before/after, layer switches, access table, provenance and bilingual map answers")
        report = context.request.get(base + f"/api/analyses/{checks['run_id']}/report?language=en")
        assert report.ok
        assert "synthetic" in report.text().lower()
        for attribution in ("Contains modified Copernicus Sentinel data 2026", "OpenStreetMap contributors", "Copernicus WorldDEM-30"):
            assert attribution in report.text()
        page.reload(wait_until="networkidle")
        page.wait_for_function("state.run?.mode === 'demo'")
        assert page.evaluate("state.run.id") == checks["run_id"]
        assert page.locator("#map-badge").inner_text() == "DEMO DATA"
        checks["checks"].append("Report preserves synthetic labels/attribution; reload restores explicitly chosen demo")
        page.locator("#advanced").evaluate("node => node.open = true")
        original_west = page.locator("#west").input_value()
        page.locator("#west").fill("85.5")
        page.locator("#run-button").click()
        page.wait_for_function("!state.busy && !document.getElementById('form-error').hidden")
        assert page.evaluate("state.run.id") == checks["run_id"]
        assert page.locator("#map-badge").inner_text() == "DEMO DATA"
        assert page.locator("#run-button").is_enabled()
        assert page.locator("#ask-button").is_enabled()
        page.locator("#west").fill(original_west)
        checks["checks"].append("Invalid bounds show an error, preserve the previous labeled result and release controls")
        browser.close()
    assert not checks["page_errors"], checks["page_errors"]
    (args.output / "browser-checks.json").write_text(json.dumps(checks, indent=2))
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
