"""Read-only browser smoke for a saved actual run; never starts acquisition."""
import argparse
import json
from pathlib import Path
import shutil
import sys

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/qa/repair-browser"))
    parser.add_argument("--skip-chat", action="store_true")
    parser.add_argument("--offline-chat", action="store_true", help="Exercise repaired chat/list UI using local deterministic evidence, without provider calls")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"run_id": args.run_id, "page_errors": [], "checks": [], "responsive": []}
    with sync_playwright() as playwright:
        options = {"headless": True, "chromium_sandbox": True}
        if shutil.which("chromium"):
            options["executable_path"] = shutil.which("chromium")
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        if args.offline_chat:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from app.copilot import answer
            from unittest.mock import patch
            source = Path(__file__).resolve().parent.parent / 'data' / 'runs' / args.run_id / 'result.json'
            recorded = json.loads(source.read_text())
            def local_reply(route):
                request = route.request.post_data_json
                with patch.dict('os.environ', {'LLM_ENABLED': 'false'}):
                    reply = answer(recorded, request['question'], request.get('language', 'en'), request.get('history', []))
                route.fulfill(json=reply)
            context.route('**/api/analyses/' + args.run_id + '/copilot', local_reply)
        page = context.new_page()
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        acquisitions = []
        page.on("request", lambda request: acquisitions.append(request.url) if request.method == "POST" and request.url.endswith("/api/analyses") else None)
        page.goto(args.base_url.rstrip('/')+'/?run='+args.run_id, wait_until="networkidle", timeout=60000)
        page.wait_for_function("state.run?.mode === 'live'", timeout=60000)
        assert page.evaluate("state.run.id") == args.run_id
        report['checks'].append('Saved analysis can be selected by its shared run URL')
        assert page.locator("#observability").is_visible()
        assert "not operational" in page.locator(".prototype").inner_text()
        run = page.evaluate("({id:state.run.id,stats:state.run.stats,class_availability:state.run.class_availability})")
        if page.evaluate("state.run.acquisition_mode === 'cached_reprocessing'"):
            assert "Cached real inputs" in page.locator("#run-meta").inner_text()
            report_html = context.request.get(args.base_url + f"/api/analyses/{args.run_id}/report?language=en")
            assert report_html.ok and "CACHED REAL INPUTS" in report_html.text()
            report["checks"].append("Cached acquisition is explicit in both dashboard and report")
        for kind in ("water", "debris"):
            if run["class_availability"][kind]["status"] == "unavailable":
                assert run["stats"][kind + "_km2"] is None
                assert page.locator(f'[data-layer="{kind}"]').is_disabled()
        report["checks"].append("Actual run and unavailable classes are labeled; no fabricated zero")
        for width in (360, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(150)
            dimensions = page.evaluate("({viewport:innerWidth,document:document.documentElement.scrollWidth})")
            assert dimensions["document"] <= dimensions["viewport"], dimensions
            report["responsive"].append(dimensions)
            page.screenshot(path=str(args.output / f"real-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.locator("#access-tab").click()
        page.locator("#settlement-table .feature-link").first.click()
        page.wait_for_function('document.getElementById("feature-detail-title").textContent !== "Loading feature evidence…"')
        assert "OSM ID" in page.locator("#feature-detail-content").inner_text()
        assert "Hospital access" in page.locator("#feature-detail-content").inner_text()
        report["checks"].append("Settlement table opens run-bound detail with hospital evidence and route scope")
        if not args.skip_chat:
            page.locator("#language").select_option("ne")
            page.evaluate("ask('अवलोकन क्षेत्र कति छ?')")
            page.wait_for_function("!state.asking && document.querySelector('.chat-answer') !== null", timeout=60000)
            assert page.locator(".chat-answer .fact-link").count() > 0
            page.locator(".chat-answer .fact-link").first.click()
            assert page.locator("#feature-detail").is_visible()
            report["checks"].append("Nepali answer exposes clickable saved-fact citations")
            report["generation"] = page.locator(".generation-status").last.inner_text()
            if args.offline_chat:
                for question, expected in [('What is the radar coverage?', f"{run['stats']['radar_valid_coverage_pct']:.1f}"), ('How many hospitals are mapped?', 'Mapped hospital'), ('What is the water threshold?', f"{recorded['parameters']['water_threshold_db']:.1f}")]:
                    page.locator('#language').select_option('en')
                    page.evaluate('question => ask(question)', question)
                    page.wait_for_function('!state.asking', timeout=60000)
                    assert expected in page.locator('.chat-answer').last.inner_text()
                page.evaluate("ask('List all settlements and their access status.')")
                page.wait_for_function('!state.asking && document.querySelector(".matching-list") !== null', timeout=60000)
                listing = page.locator('.matching-list').last
                listing.locator('summary').click()
                assert listing.locator('tbody tr').count() == run['stats']['settlements_total']
                listing.locator('button').last.click()
                page.wait_for_function('!document.getElementById("feature-detail-title").textContent.includes("Loading")')
                assert 'OSM ID' in page.locator('#feature-detail-content').inner_text()
                for width in (360, 768, 1280):
                    page.set_viewport_size({'width':width,'height':900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(args.output / f'chat-list-{width}.png'), full_page=True)
                report['checks'].append('Repaired radar/inventory/threshold questions, complete settlement list and list-to-map citations')
                report['chat_source'] = 'Local deterministic evidence replay; no Groq calls made by this browser check'
        assert not acquisitions, "Read-only smoke must not start an acquisition"
        assert not report["page_errors"], report["page_errors"]
        browser.close()
    report["limitation"] = "Software/UI checks on a cached actual run; not field accuracy or native Nepali review."
    (args.output / "checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
