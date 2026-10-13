"""Record a captioned actual-data UI tour. Requires Playwright + Chromium and ffmpeg.

Start the application on 127.0.0.1:8000 first. This script does not validate
field accuracy. It requires a completed actual-data run and retains coverage limitations.
"""
from pathlib import Path
import subprocess
import time

from playwright.sync_api import sync_playwright


root = Path(__file__).resolve().parent.parent
output = root / "docs" / "qa" / "recording"
output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    # Load the completed actual-data case before starting the recording.
    setup = browser.new_page()
    setup.goto("http://127.0.0.1:8000", wait_until="networkidle")
    setup.wait_for_function("document.querySelector('#stat-water').textContent !== '—'", timeout=60000)
    assert setup.evaluate("state.run.mode") == "live", "Record only a completed actual-data run"
    storage = setup.context.storage_state()
    setup.close()
    context = browser.new_context(viewport={"width":1280,"height":900}, storage_state=storage,
                                  record_video_dir=str(output), record_video_size={"width":1280,"height":900})
    page = context.new_page()
    page.goto("http://127.0.0.1:8000", wait_until="networkidle")
    page.wait_for_function("document.querySelector('#stat-water').textContent !== '—'")
    started = time.monotonic()
    def caption(text):
        page.evaluate("""text => {
          let el = document.querySelector('#demo-caption');
          if (!el) { el = document.createElement('div'); el.id = 'demo-caption'; document.body.append(el); }
          el.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:99999;padding:16px 30px;background:#163d34f5;color:#eef4e7;font:15px/1.6 Arial;box-shadow:0 -2px 15px #132f201a;pointer-events:none';
          el.textContent = text;
        }""",text)
    def until(seconds):
        remaining=seconds-(time.monotonic()-started)
        if remaining > 0: page.wait_for_timeout(remaining*1000)
    caption("FLOOD LENS — actual Sentinel observations, pre-event OSM and Copernicus DEM for the August 2026 Nepal case. This is an educational prototype; mapping limits remain substantial.")
    until(16)
    algorithm = page.evaluate("state.run.provenance.water_model.algorithm")
    caption(f"Choose an area and flood date. The default provider processes original Sentinel GRD from a public mirror. Water detection for this saved run: {algorithm}. Training is managed outside the application.")
    page.locator("#advanced").evaluate("e => e.open = true")
    until(34)
    page.locator("#advanced").evaluate("e => e.open = false")
    page.locator("#map-view").scroll_into_view_if_needed()
    coverage = page.evaluate("({eligible: state.run.stats.valid_coverage_pct, optical: state.run.stats.optical_clear_pct})")
    caption(f"Before / after radar observations are paired by compatible orbit geometry. Eligible coverage: {coverage['eligible']}%; paired clear optical coverage: {coverage['optical']}%. Unobserved areas remain uncertain.")
    page.locator("#before-button").click(); until(43)
    page.locator("#after-button").click(); until(53)
    page.locator('[data-layer="debris"]').uncheck(); until(58)
    page.locator('[data-layer="debris"]').check(); until(64)
    caption("Road access compares the baseline network with exposed segments removed. Intact alternatives stay connected. Pre-existing disconnection is reported separately; closures are assumptions requiring field checks.")
    page.get_by_role("tab",name="Road access").click(); until(83)
    page.get_by_role("tab",name="Impact map").click()
    page.locator(".copilot-panel").scroll_into_view_if_needed()
    caption("Ask the map: a locally trained English/Nepali intent model selects the topic. Templates fill every number and settlement name from the saved run and expose the evidence paths.")
    page.get_by_role("button",name="Which settlements are cut off?").click()
    page.wait_for_selector(".chat-answer"); until(100)
    page.locator("#language").select_option("ne")
    page.get_by_role("button",name="What infrastructure is exposed?").click()
    page.wait_for_function("document.querySelectorAll('.chat-answer').length === 2"); until(114)
    page.locator("#language").select_option("en")
    page.locator("#question").fill("How many people died?")
    page.locator("#ask-button").click()
    caption("Unsupported questions return no estimate. These input maps cannot establish deaths, injuries, missing people or population. Exposure is not confirmed structural damage.")
    until(127)
    run_id=page.evaluate("localStorage.getItem('flood-lens-run')")
    page.goto(f"http://127.0.0.1:8000/api/analyses/{run_id}/report?language=ne", wait_until="networkidle")
    caption("One-page situation reports are generated from the same facts in English and Nepali. Browser printing preserves Nepali script. Reports retain provenance, uncertainty and required data attribution.")
    until(142)
    page.goto("http://127.0.0.1:8000",wait_until="networkidle")
    page.wait_for_function("document.querySelector('#stat-water').textContent !== '—'")
    page.locator("#map-view").scroll_into_view_if_needed()
    caption("The optional downhill tool follows D8 DEM terrain and lists settlements near the path. It does not simulate hydraulic depth, velocity or flood travel time.")
    page.locator("#trace-toggle").click()
    # Click near the north/center of the actual synthetic raster.
    page.evaluate("""() => {
      const c=state.run.bbox;
      map.fire('click',{latlng:L.latLng(c[3]-(c[3]-c[1])*.18,(c[0]+c[2])/2)});
    }""")
    until(158)
    page.get_by_role("tab",name="Data & method").click()
    caption("Sentinel-1/2, Copernicus DEM and historical OSM supply the analysis. External model weights and training are supplied separately. Official EMSR927 products are evaluation-only; inspect their class definitions before interpreting overlap.")
    until(173)
    caption("This tour demonstrates the saved run, not field accuracy. Review the displayed reference comparison, model limitations and observation coverage. Independent Himalayan validation and field review remain necessary.")
    until(181)
    video=page.video
    page.close(); context.close()
    source=Path(video.path())
    browser.close()
destination=root / "docs" / "demo-actual.mp4"
subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(source),"-t","180","-c:v","libx264","-preset","fast","-crf","24","-pix_fmt","yuv420p","-movflags","+faststart",str(destination)],check=True)
print(f"Captioned three-minute ACTUAL-DATA demo: {destination}",flush=True)
