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
        if workspace["completed_actual_runs"] == 0 and workspace["completed_synthetic_runs"] == 0:
            assert page.evaluate("state.run === null")
            assert page.locator("#map-empty").is_visible()
            assert page.locator("#stat-water").inner_text() == "—"
            assert page.locator("#ask-button").is_disabled()
            assert page.locator("#before-button").is_disabled()
            assert page.locator("#report-link").get_attribute("aria-disabled") == "true"
            assert "No actual observations acquired" in page.locator("#workspace-data-status").inner_text()
            checks["checks"].append("Empty workspace has no fabricated counts; result controls are gated")
            assert page.locator('#region').input_value() == 'custom'
            assert page.locator('#event-date').input_value() == ''
            assert all(page.locator('#'+field).input_value()=='' for field in ('west','south','east','north'))
            assert page.locator('#satellite-provider').input_value() == page.evaluate('state.sources.acquisition_configuration.default_provider')
            checks['checks'].append('Fresh inputs require an explicit area/date; provider default follows shared acquisition configuration')
        assert page.locator("#water-model").count() == 0
        assert page.evaluate("state.sources.copilot.component") == "situation_report_copilot"
        assert "situation-report copilot" in page.locator("#ai-component-status").inner_text()
        assert not workspace["training_data_loading"]
        page.keyboard.press("Tab")
        assert page.locator(".skip-link").evaluate("node => node.matches(':focus-visible') && getComputedStyle(node).opacity === '1'")
        page.locator("#map-tab").focus()
        page.keyboard.press("End")
        assert page.locator("#method-view").is_visible()
        assert page.locator("#method-tab").get_attribute("aria-selected") == "true"
        groups = page.locator(".source-group")
        assert groups.count() == 2
        assert groups.nth(0).locator(".source-card").count() == 4
        assert groups.nth(1).locator(".source-card").count() == 1
        assert "Never prediction inputs" in groups.nth(1).inner_text()
        assert "Kuro Siwo" not in page.locator("#source-inventory").inner_text()
        assert "Selected AI component" in page.locator("#source-inventory").inner_text()
        page.screenshot(path=str(args.output / "data-sources-desktop.png"), full_page=True)
        checks["checks"].append("Keyboard tabs, input/evaluation separation and selected situation-report copilot")
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
        # Explicit synthetic fixture inputs; never fill a production default.
        page.locator('#region').select_option('custom')
        for field,value in {'area-name':'','west':'10','south':'10','east':'10.4','north':'10.4','event-date':'2024-08-21'}.items():
            page.locator('#'+field).fill(value)
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
        assert page.evaluate('state.run.name') == 'Custom flood analysis'
        assert page.locator("#ask-button").is_enabled()
        assert page.locator("#satellite-provider").is_disabled()
        checks["run_id"] = page.evaluate("state.run.id")
        assert page.evaluate("state.run.provenance.mapping.requires_model_weights") is False
        assert page.evaluate("'water_model' in state.run.parameters") is False
        checks["checks"].append("Explicit synthetic preview completes through rule-based mapping without a segmentation model")
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
        assert "Rule-based radar and optical change detection" in page.locator("#provenance-content").inner_text()
        assert "No independent matching reference" in page.locator("#evaluation-status").inner_text()
        disclosure = page.evaluate("""() => {
            const original = state.run;
            renderRun({...original, evaluation: {metrics: {iou: null, f1: 0.1}, limitations: 'Synthetic UI verification fixture.'}});
            const disclosure = document.getElementById('evaluation-status').textContent;
            renderRun(original);
            return disclosure;
        }""")
        assert "Synthetic sanity check" in disclosure
        assert "IoU N/A" in disclosure
        page.locator("#map-tab").click()
        page.wait_for_function("!state.needsMapFit")
        assert page.evaluate("map.getBounds().contains(L.latLngBounds(boundsFor(state.run)))")
        checks['checks'].append('Results rendered while the map tab is hidden fit the selected area when the map reopens')
        page.get_by_role("button", name="Which settlements are cut off?").click()
        page.wait_for_selector(".chat-answer")
        assert "Settlements with modeled loss of access" in page.locator(".chat-answer").last.inner_text()
        assert page.locator(".chat-answer .fact-link").count() > 0
        page.wait_for_function("!state.asking")
        page.locator("#language").select_option("ne")
        page.get_by_role("button", name="What infrastructure is exposed?").click()
        page.wait_for_function("document.querySelectorAll('.chat-answer').length === 2 && !state.asking")
        assert str(page.evaluate("state.run.stats.bridges_exposed")) in page.locator(".chat-answer").last.inner_text()
        checks["checks"].append("Before/after, layer switches, access table, provenance and bilingual map answers")
        assert page.locator('.generation-status').count()==2
        page.locator('.evidence summary').last.click()
        assert checks['run_id'] in page.locator('.evidence').last.inner_text()
        page.locator('#language').select_option('en')
        loading=page.evaluate("""() => { ask('Which roads are exposed and which settlements are isolated?'); return document.querySelector('.chat-loading')?.textContent; }""")
        assert loading=='Checking analysis evidence…'
        page.wait_for_function("document.querySelectorAll('.chat-answer').length === 3 && !state.asking")
        compound=page.locator('.chat-answer').last.inner_text()
        assert 'Potential infrastructure exposure' in compound and 'Modeled settlement connectivity' in compound
        page.evaluate("ask('How many people died?')")
        page.wait_for_function("document.querySelectorAll('.chat-answer').length === 4 && !state.asking")
        assert 'unavailable' in page.locator('.chat-answer').last.inner_text().lower()
        assert page.evaluate('state.history.length')==6
        page.locator('#language').select_option('ne')
        page.locator('#generate-report').click()
        page.wait_for_function("!state.asking && document.querySelectorAll('.report-downloads a').length >= 3")
        pdf_url=page.locator('.report-downloads a').filter(has_text='PDF').last.get_attribute('href')
        assert checks['run_id'] in pdf_url
        pdf=context.request.get(base+pdf_url)
        assert pdf.ok and pdf.body().startswith(b'%PDF')
        page.screenshot(path=str(args.output/'copilot-report-ne.png'),full_page=True)
        checks['checks'].append('Loading indicator, expanded run-bound evidence, compound query, unsupported casualty request, bounded history and Nepali PDF download')
        report = context.request.get(base + f"/api/analyses/{checks['run_id']}/report?language=en")
        assert report.ok
        assert "synthetic" in report.text().lower()
        for attribution in ("Contains modified Copernicus Sentinel data", "OpenStreetMap contributors", "Copernicus WorldDEM-30"):
            assert attribution in report.text()
        page.reload(wait_until="networkidle")
        if workspace["completed_actual_runs"]:
            page.wait_for_function("state.run?.mode === 'live'")
            checks["checks"].append("Reload prefers a saved actual analysis over synthetic data")
        else:
            page.wait_for_function("state.run?.mode === 'demo'")
            assert page.evaluate("state.run.id") == checks["run_id"]
            checks["checks"].append("Demo-only workspace restores the explicitly chosen labeled demo")
        restored_id = page.evaluate("state.run.id")
        restored_badge = page.locator("#map-badge").inner_text()
        page.locator("#advanced").evaluate("node => node.open = true")
        original_west = page.locator("#west").input_value()
        page.locator("#west").fill("85.5")
        page.locator("#run-button").click()
        page.wait_for_function("!state.busy && !document.getElementById('form-error').hidden")
        assert page.evaluate("state.run.id") == restored_id
        assert page.locator("#map-badge").inner_text() == restored_badge
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
