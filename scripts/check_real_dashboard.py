"""Read-only browser smoke for a saved actual run; never starts acquisition."""
import argparse
import json
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/qa/repair-browser"))
    parser.add_argument("--skip-chat", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"run_id": args.run_id, "page_errors": [], "checks": [], "responsive": []}
    with sync_playwright() as playwright:
        options = {"headless": True, "chromium_sandbox": True}
        if shutil.which("chromium"):
            options["executable_path"] = shutil.which("chromium")
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script('localStorage.setItem("flood-lens-run",' + json.dumps(args.run_id) + ')')
        page = context.new_page()
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        acquisitions = []
        page.on("request", lambda request: acquisitions.append(request.url) if request.method == "POST" and request.url.endswith("/api/analyses") else None)
        page.goto(args.base_url, wait_until="networkidle", timeout=60000)
        page.wait_for_function("state.run?.mode === 'live'", timeout=60000)
        assert page.evaluate("state.run.id") == args.run_id
        assert page.locator("#observability").is_visible()
        assert "not operational" in page.locator(".prototype").inner_text()
        run = page.evaluate("({id:state.run.id,stats:state.run.stats,class_availability:state.run.class_availability})")
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
        assert not acquisitions, "Read-only smoke must not start an acquisition"
        assert not report["page_errors"], report["page_errors"]
        browser.close()
    report["limitation"] = "Software/UI checks on a cached actual run; not field accuracy or native Nepali review."
    (args.output / "checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
