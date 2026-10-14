"""Record the current Copilot interface into a captioned three-minute video.

Defaults to real data. --preview explicitly permits a labeled synthetic walkthrough.
No model keys or private browser profiles are recorded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--run-id')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    with httpx.Client(base_url=base, timeout=20) as client:
        selected = client.get(('/api/analyses/' + args.run_id if args.run_id else '/api/analyses/latest-real') + '?view=dashboard')
        if not selected.is_success:
            parser.error('A completed real analysis is required. Select an existing demo UUID with --preview only for a labeled software walkthrough.')
        run = selected.json()
        health = client.get('/api/health').json()
    if run.get('mode') == 'demo' and not args.preview:
        parser.error('Synthetic runs require --preview and cannot be represented as actual observations.')
    if run.get('mode') not in ('live', 'demo') or run.get('provenance', {}).get('mapping', {}).get('type') != 'rule_based':
        parser.error('Select a saved analysis from the current Copilot route, not the archived segmentation pipeline.')
    for executable in ('ffmpeg', 'ffprobe'):
        if not shutil.which(executable):
            parser.error(executable + ' is required for video export.')
    destination = args.output or ROOT / 'output/video' / ('copilot-preview.mp4' if run['mode'] == 'demo' else 'copilot-demo.mp4')
    destination.parent.mkdir(parents=True, exist_ok=True)
    recordings = ROOT / 'output/qa/copilot-recording'
    recordings.mkdir(parents=True, exist_ok=True)
    synthetic = run['mode'] == 'demo'
    label = ('SYNTHETIC SOFTWARE PREVIEW - no real disaster observations' if synthetic else
             'CACHED REAL CDSE INPUTS - no new acquisition - educational prototype')
    if not synthetic and run.get('acquisition_mode') != 'cached_reprocessing':
        label = 'SAVED REAL SATELLITE INPUTS - no new acquisition - educational prototype'
    coverage = run['stats'].get('water_eligible_coverage_pct')
    coverage_label = f'{coverage:g}%' if isinstance(coverage, (int, float)) else 'unavailable'
    segments, replies, reports, page_errors, acquisitions = [], [], [], [], []
    with sync_playwright() as playwright:
        options = {'headless': True, 'chromium_sandbox': True}
        if shutil.which('chromium'):
            options['executable_path'] = shutil.which('chromium')
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={'width': 1280, 'height': 900},
                                      record_video_dir=str(recordings), record_video_size={'width': 1280, 'height': 900})
        context.add_init_script('localStorage.setItem("flood-lens-run",' + json.dumps(run['id']) + ')')
        capture_started = time.monotonic()
        page = context.new_page()
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        page.on('request', lambda request: acquisitions.append(request.url)
                if request.method == 'POST' and request.url.endswith('/api/analyses') else None)
        page.goto(base, wait_until='networkidle', timeout=60000)
        page.wait_for_function('state.run !== null', timeout=30000)
        if page.evaluate('state.run.id') != run['id']:
            # Restore the requested saved run without creating a new analysis.
            page.evaluate('async id => renderRun(await api(`/api/analyses/${id}?view=dashboard`))', run['id'])
        if page.evaluate('state.run.id') != run['id']:
            raise RuntimeError('The recording does not match the selected analysis.')
        started = time.monotonic()
        leading_trim = started - capture_started

        def caption(text):
            elapsed = round(time.monotonic() - started, 2)
            segments.append({'capture_seconds': elapsed, 'caption': text})
            print(f'{elapsed:6.1f}s {text}', flush=True)
            page.evaluate("""([label,text]) => {
                if (!document.getElementById('recording-style')) {
                    const style=document.createElement('style'); style.id='recording-style';
                    style.textContent='html{scroll-behavior:auto!important}body{padding-top:40px!important;padding-bottom:125px!important}*{scroll-margin-top:55px;scroll-margin-bottom:130px}';
                    document.head.append(style);
                }
                let banner = document.getElementById('demo-banner');
                if (!banner) { banner=document.createElement('div'); banner.id='demo-banner'; document.body.append(banner); }
                banner.style.cssText='position:fixed;top:0;left:0;right:0;z-index:999999;padding:10px 24px;background:#173d35;color:white;font:700 14px/1.4 Arial;pointer-events:none';
                banner.textContent=label;
                let box=document.getElementById('demo-caption');
                if (!box) { box=document.createElement('div'); box.id='demo-caption'; document.body.append(box); }
                box.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:999999;padding:16px 28px;background:#173d35fa;color:#f6f8f1;font:17px/1.5 Arial;pointer-events:none';
                box.textContent=text;
            }""", [label, text])

        def until(seconds):
            remaining = seconds - (time.monotonic() - started)
            while remaining > 0:
                page.wait_for_timeout(min(remaining, 20) * 1000)
                remaining = seconds - (time.monotonic() - started)

        def ask(question, language):
            page.locator('#language').select_option(language)
            page.locator('#question').fill(question)
            with page.expect_response(lambda response: response.url.endswith('/copilot') and response.request.method == 'POST', timeout=45000) as observed:
                page.locator('#ask-button').click()
            page.wait_for_function('!state.asking', timeout=45000)
            result = observed.value.json()
            if result.get('run_id') != run['id'] or result.get('language') != language:
                raise RuntimeError('Recorded answer belongs to a different run or language.')
            replies.append({'question': question, **result})
            print(f"       Engine: {result['generation']['mode']} ({result['generation'].get('reason') or 'validated'})", flush=True)
            page.locator('#conversation').scroll_into_view_if_needed()
            page.locator('#conversation').evaluate('''element => {
                const questions=element.querySelectorAll('.chat-question');
                const latest=questions[questions.length-1];
                if (latest) element.scrollTop += latest.getBoundingClientRect().top - element.getBoundingClientRect().top - 10;
            }''')
            return result

        provider = health.get('llm', {})
        caption('Flood Lens: a situation-report copilot using saved satellite maps and pre-event OpenStreetMap. This is a recorded real-input walkthrough; it does not show a new acquisition.')
        until(5)
        page.locator('#map-panel' if page.locator('#map-panel').count() else '.map-panel').scroll_into_view_if_needed()
        page.locator('#before-button').click()
        caption('Before and after Sentinel-1 observations use compatible radar geometry. Historical OSM and source dates stay attached to this analysis.')
        until(10)
        page.locator('#after-button').click()
        page.locator('#observability').scroll_into_view_if_needed()
        caption(f'Only {coverage_label} of this area is eligible for water assessment. Debris is unavailable where paired optical observations are missing. A mapped zero does not establish no flooding across the area.')
        until(20)
        page.locator('#copilot-form').scroll_into_view_if_needed()
        caption('Ask a compound English question. The copilot retrieves mapped water and observation coverage. The displayed engine identifies Groq or the deterministic fallback actually used.')
        ask('How much new water was detected, and how much of the area was eligible?', 'en')
        until(37)
        page.locator('.chat-answer .fact-link').first.click()
        page.locator('#feature-detail').scroll_into_view_if_needed()
        caption('Clicking a citation opens the exact fact, source path and method. Area-wide measurements refer to the selected analysis, rather than an invented map location.')
        until(48)
        page.locator('#close-feature-detail').click()
        page.locator('#access-tab').click()
        page.locator('#access-filter').select_option('uncertain_access')
        page.locator('#access-summary').scroll_into_view_if_needed()
        caption('Access results distinguish uncertain, unassessed and baseline-disconnected settlements. A missing observed route does not automatically mean a village was cut off by flooding.')
        until(59)
        if page.locator('#settlement-table .feature-link').count():
            page.locator('#settlement-table .feature-link').first.click()
            page.wait_for_function('document.getElementById("feature-detail-title").textContent !== "Loading feature evidence…"')
            page.locator('#feature-detail').scroll_into_view_if_needed()
        caption('Settlement details link modeled routes, destination-specific hospital evidence and unverified connectors. Water below a bridge does not establish bridge failure or a confirmed road closure.')
        until(74)
        page.locator('#close-feature-detail').click()
        page.locator('#copilot-form').scroll_into_view_if_needed()
        caption('The same saved facts are available in Nepali. Names, values and uncertainty remain tied to this run. Native-speaker terminology review is still pending.')
        until(85)
        ask('अवलोकन क्षेत्र कति छ?', 'ne')
        until(105)
        page.locator('.evidence summary').last.click()
        page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')
        caption('The evidence panel exposes fact IDs and canonical values. Matching saved facts verifies numerical consistency; it does not verify physical mapping accuracy.')
        until(114)
        result = ask('How many people died?', 'en')
        if result.get('intent') != 'unsupported' or result.get('facts'):
            raise RuntimeError('The unsupported-statistic refusal was not preserved.')
        page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')
        caption('Casualty and population statistics cannot be established from these inputs. The copilot refuses the request and supplies no invented estimate.')
        until(124)
        page.locator('#language').select_option('ne')
        with page.expect_response(lambda response: response.url.endswith('/report') and response.request.method == 'POST', timeout=45000) as generated:
            page.locator('#generate-report').click()
        page.wait_for_function('!state.asking && document.querySelector(".report-downloads a") !== null', timeout=45000)
        report = generated.value.json()
        if report.get('run_id') != run['id'] or report.get('language') != 'ne':
            raise RuntimeError('The saved Nepali report could not be generated for this run.')
        reports.append({'language': 'ne', 'status': report['status'], 'links': report['links'], 'generation': report['generation']})
        page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')
        caption('Generate a fixed-format Nepali situation report from the selected facts. HTML, PDF and JSON preserve the snapshot, source dates, partial coverage and attribution.')
        until(132)
        report_url = report['links']['html']
        page.goto(base + report_url, wait_until='networkidle')
        caption('The one-page report explicitly marks cached real inputs, incomplete observations and unavailable debris. Nepali text uses bundled Devanagari fonts; the report needs independent field and language review.')
        until(143)
        page.goto(base, wait_until='networkidle')
        page.wait_for_function('state.run !== null', timeout=30000)
        page.locator('#method-tab').click()
        page.locator('.evaluation-note').scroll_into_view_if_needed()
        evaluation = run.get('evaluation', {})
        if evaluation.get('status') == 'unavailable' and evaluation.get('reference_classes') == ['Landslide']:
            caption('EMSR927 is evaluation-only. Its landslide references have no eligible debris observations here, so accuracy is N/A. A class mismatch or empty comparison is never presented as a perfect score.')
        else:
            caption('Published reference maps stay separate from prediction inputs. Evaluation needs matching classes and eligible observations; missing or empty comparisons cannot establish mapping accuracy.')
        until(158)
        credits = 'Contains modified Copernicus Sentinel data 2026. Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. © OpenStreetMap contributors.'
        page.locator('.page-footer').scroll_into_view_if_needed()
        caption('Educational prototype, not operational. Field verification and native Nepali review remain necessary. The copilot uses a hosted model; this application trains no model.')
        until(166)
        caption(credits)
        until(181)
        if page_errors or acquisitions:
            raise RuntimeError('The recording had a page error or unexpectedly started a new acquisition.')
        captured_seconds = time.monotonic() - started
        video = page.video
        page.close()
        context.close()
        source = Path(video.path())
        browser.close()
    duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                             '-of', 'default=noprint_wrappers=1:nokey=1', str(source)], text=True).strip())
    usable_duration = max(1, duration - leading_trim)
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-ss', str(leading_trim), '-i', str(source),
                    '-vf', f'setpts={180 / usable_duration:.10f}*PTS,fps=25,tpad=stop_mode=clone:stop_duration=2,setpts=N/(25*TB)',
                    '-frames:v', '4500', '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(destination)], check=True)
    actual_duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                                    '-of', 'default=noprint_wrappers=1:nokey=1', str(destination)], text=True).strip())
    if abs(actual_duration - 180) > .001:
        raise RuntimeError('The exported recording does not have the required three-minute duration.')
    manifest = {'run_id': run['id'], 'data_mode': run['mode'], 'synthetic_preview': synthetic, 'duration_seconds': 180,
                'acquisition_mode': run.get('acquisition_mode'), 'analysis_status': run.get('analysis_status'),
                'captioned': True, 'narration': False, 'actual_duration_seconds': actual_duration,
                'llm_configuration': provider, 'segments': segments,
                'captured_seconds': round(captured_seconds, 3), 'leading_trim_seconds': round(leading_trim, 3),
                'playback_time_scale': 180 / usable_duration, 'page_errors': page_errors, 'new_acquisitions': acquisitions,
                'replies': replies, 'reports': reports, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                'attributions': [credits], 'limitations': 'Captioned saved-run walkthrough edited to three minutes, not a new acquisition, runtime benchmark, field-accuracy validation or native Nepali sign-off. Actual response-engine labels remain visible.'}
    destination.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'video': str(destination.resolve()), 'data_mode': run['mode'], 'bytes': destination.stat().st_size}, indent=2))


if __name__ == '__main__':
    main()
