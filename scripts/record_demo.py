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
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.models import ATTRIBUTIONS

# Final playback cues match the supplied three-minute voiceover. Each captured
# scene is edited independently so API latency cannot shift later sections.
PLAYBACK_CUES = [0, 6, 12, 21, 36, 45, 57, 69, 99, 108, 120, 123, 135, 147, 156, 180]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--run-id')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-llm', action='store_true',
                        help='Reject the take if a supported answer uses the fallback.')
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
    destination = args.output or ROOT / 'output/video' / ('copilot-preview.mp4' if run['mode'] == 'demo' else 'flooline-repaired-demo.mp4')
    destination.parent.mkdir(parents=True, exist_ok=True)
    recordings = ROOT / 'output/qa/copilot-recording'
    recordings.mkdir(parents=True, exist_ok=True)
    synthetic = run['mode'] == 'demo'
    label = ('SYNTHETIC SOFTWARE PREVIEW - no real disaster observations' if synthetic else
             'CACHED REAL CDSE INPUTS - no new acquisition - educational prototype')
    if not synthetic and run.get('acquisition_mode') != 'cached_reprocessing':
        label = 'SAVED REAL SATELLITE INPUTS - no new acquisition - educational prototype'
    coverage = run['stats'].get('water_eligible_coverage_pct')
    coverage_label = f'{coverage:.1f}%' if isinstance(coverage, (int, float)) else 'unavailable'
    segments, replies, reports, page_errors, acquisitions, failed_responses = [], [], [], [], [], []
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
        page.on('response', lambda response: failed_responses.append({'url': response.url, 'status': response.status})
                if response.status >= 400 else None)
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
                    style.textContent='html{scroll-behavior:auto!important}body{padding-top:40px!important;padding-bottom:125px!important}*{scroll-margin-top:55px;scroll-margin-bottom:130px}.observability{background:#eff5f4!important;border-color:#b7cec7!important;color:#274e44!important}.recording-coverage-title{display:block;font-size:10px;letter-spacing:.08em;margin-bottom:5px}.recording-coverage-line{display:block;margin:3px 0}';
                    document.head.append(style);
                }
                // Presentation only: retain the saved measurements and every
                // material limitation while making data status easier to read.
                const coverage=document.getElementById('observability');
                if (coverage && !coverage.dataset.recordingSummary && typeof state !== 'undefined' && state.run) {
                    coverage.dataset.recordingSummary='true';
                    const stats=state.run.stats, availability=state.run.class_availability || {};
                    const add=(text,cls) => {const line=document.createElement('span');line.className=cls;line.textContent=text;coverage.append(line);};
                    coverage.replaceChildren();
                    const partial=state.run.analysis_status === 'partial' || ['water','debris'].some(kind => ['partial','unavailable'].includes(availability[kind]?.status));
                    add('OBSERVATION COVERAGE'+(partial ? ' · PARTIAL DATA' : ''),'recording-coverage-title');
                    add(`Water: ${numberLabel(availability.water?.coverage_pct ?? stats.water_eligible_coverage_pct,1)}% eligible for assessment. Debris: ${availability.debris?.status === 'unavailable' ? 'not assessed; no usable paired optical observations' : numberLabel(availability.debris?.coverage_pct ?? stats.debris_eligible_coverage_pct,1)+'% eligible for assessment'}.`,'recording-coverage-line');
                    add(`Road access: ${numberLabel(stats.settlements_access_uncertain)} settlements uncertain; ${numberLabel(stats.settlements_unassessed)} unassessed. Results apply only to assessed areas; other areas remain unknown.`,'recording-coverage-line');
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

        def check_screen():
            page.wait_for_timeout(300)
            if page.locator('.error:visible').count():
                raise RuntimeError('A visible error would appear in the recording.')

        def until(seconds):
            remaining = seconds - (time.monotonic() - started)
            while remaining > 0:
                page.wait_for_timeout(min(remaining, 20) * 1000)
                remaining = seconds - (time.monotonic() - started)

        def ask(question, language):
            page.locator('#language').select_option(language)
            page.locator('#question').fill(question)
            with page.expect_response(lambda response: response.url.endswith('/copilot') and response.request.method == 'POST', timeout=70000) as observed:
                page.locator('#ask-button').click()
            page.wait_for_function('!state.asking', timeout=70000)
            result = observed.value.json()
            if result.get('run_id') != run['id'] or result.get('language') != language:
                raise RuntimeError('Recorded answer belongs to a different run or language.')
            replies.append({'question': question, **result})
            if result.get('intent') == 'unsupported' or not result.get('facts'):
                raise RuntimeError('A supported demo question did not receive evidence; discard this take.')
            if args.require_llm and result['generation']['mode'] != 'llm':
                raise RuntimeError('The supplied narration requires a genuine Groq answer; discard this take: '+str(result['generation'].get('reason')))
            if result.get('report_error'):
                raise RuntimeError('The answer contains a report-generation error.')
            print(f"       Engine: {result['generation']['mode']} ({result['generation'].get('reason') or 'validated'})", flush=True)
            page.locator('#conversation').scroll_into_view_if_needed()
            page.locator('#conversation').evaluate('''element => {
                const questions=element.querySelectorAll('.chat-question');
                const latest=questions[questions.length-1];
                if (latest) element.scrollTop += latest.getBoundingClientRect().top - element.getBoundingClientRect().top - 10;
            }''')
            check_screen()
            return result

        provider = health.get('llm', {})
        caption('Flooline: a flood-mapping copilot. Saved real satellite inputs and pre-event OpenStreetMap; this walkthrough does not acquire new imagery.')
        until(5)
        page.locator('#map-panel' if page.locator('#map-panel').count() else '.map-panel').scroll_into_view_if_needed()
        page.locator('#before-button').click()
        caption('Before and after Sentinel-1 observations use compatible radar geometry. Historical OSM and source dates stay attached to this analysis.')
        until(10)
        page.locator('#after-button').click()
        page.locator('#observability').scroll_into_view_if_needed()
        caption(f'Water assessment covers {coverage_label} of this area. Debris needs usable paired optical observations. Results describe assessed areas; other areas remain unknown.')
        until(20)
        page.locator('#copilot-form').scroll_into_view_if_needed()
        caption('Ask a compound English question. The copilot retrieves mapped water and observation coverage. The displayed engine identifies Groq or the deterministic fallback actually used.')
        compound=ask('How much new water was detected, and how much of the area was eligible?', 'en')
        if not {'water_km2','water_eligible_coverage_pct'}.issubset({f['field_name'] for f in compound['facts']}):
            raise RuntimeError('The compound demo answer omitted water or eligible coverage; discard this take.')
        until(37)
        caption('Clicking a citation opens the exact fact, source path and method. Area-wide measurements refer to the selected analysis, rather than an invented map location.')
        page.locator('.chat-answer .fact-link').first.click()
        page.locator('#feature-detail').scroll_into_view_if_needed()
        check_screen()
        until(48)
        caption('Access results distinguish uncertain, unassessed and baseline-disconnected settlements. A missing observed route does not automatically mean a village was cut off by flooding.')
        page.locator('#close-feature-detail').click()
        page.locator('#access-tab').click()
        page.locator('#access-filter').select_option('uncertain_access')
        page.locator('#access-summary').scroll_into_view_if_needed()
        until(59)
        caption('Settlement details link modeled routes, destination-specific hospital evidence and unverified connectors. Water below a bridge does not establish bridge failure or a confirmed road closure.')
        if page.locator('#settlement-table .feature-link').count():
            page.locator('#settlement-table .feature-link').first.click()
            page.wait_for_function('document.getElementById("feature-detail-title").textContent !== "Loading feature evidence…"')
            page.locator('#feature-detail').scroll_into_view_if_needed()
        if 'unavailable' in page.locator('#feature-detail-title').inner_text().lower():
            raise RuntimeError('Settlement feature evidence failed to load.')
        check_screen()
        until(74)
        caption('Switch to Nepali and ask about observation coverage. Saved values and uncertainty remain unchanged; native-speaker terminology review is pending.')
        page.locator('#close-feature-detail').click()
        page.locator('#copilot-form').scroll_into_view_if_needed()
        page.locator('#language').select_option('ne')
        until(77)
        ask('अवलोकन क्षेत्र कति छ?', 'ne')
        until(105)
        caption('The evidence panel exposes fact IDs and canonical values. Matching saved facts verifies numerical consistency; it does not verify physical mapping accuracy.')
        page.locator('.evidence summary').last.click()
        page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')
        until(114)
        caption('Inspect Data & method: recorded satellite inputs, elevation and historical OpenStreetMap stay attached to the selected analysis.')
        page.locator('#method-tab').click()
        page.locator('#source-inventory').scroll_into_view_if_needed()
        check_screen()
        until(124)
        caption('Generate a Nepali situation report using validated narrative and selected map facts. HTML, PDF and JSON preserve the snapshot, source dates, partial coverage and attribution.')
        page.locator('#copilot-form').scroll_into_view_if_needed()
        page.locator('#language').select_option('ne')
        with page.expect_response(lambda response: response.url.endswith('/report') and response.request.method == 'POST', timeout=70000) as generated:
            page.locator('#generate-report').click()
        page.wait_for_function('!state.asking && document.querySelector(".report-downloads a") !== null', timeout=70000)
        report = generated.value.json()
        if report.get('run_id') != run['id'] or report.get('language') != 'ne':
            raise RuntimeError('The saved Nepali report could not be generated for this run.')
        if args.require_llm and report['generation']['mode']!='llm':
            raise RuntimeError('A Groq-generated report is required for this take: '+str(report['generation'].get('reason')))
        reports.append({'language': 'ne', 'status': report['status'], 'links': report['links'], 'generation': report['generation']})
        page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')
        check_screen()
        until(132)
        report_url = report['links']['html']
        page.goto(base + report_url, wait_until='networkidle')
        caption('The one-page report explicitly marks cached real inputs, incomplete observations and unavailable debris. Nepali text uses bundled Devanagari fonts; the report needs independent field and language review.')
        check_screen()
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
        credits = ' '.join(ATTRIBUTIONS)
        page.locator('.page-footer').scroll_into_view_if_needed()
        caption('Educational prototype, not operational. Field verification and native Nepali review remain necessary. The copilot uses a hosted model; this application trains no model.')
        until(166)
        caption(credits)
        until(181)
        check_screen()
        if page_errors or acquisitions or failed_responses:
            raise RuntimeError('Discard the take: page error, failed HTTP response or unexpected new acquisition.')
        captured_seconds = time.monotonic() - started
        video = page.video
        page.close()
        context.close()
        source = Path(video.path())
        browser.close()
    duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                             '-of', 'default=noprint_wrappers=1:nokey=1', str(source)], text=True).strip())
    if len(segments) != len(PLAYBACK_CUES) - 1:
        raise RuntimeError('The captured scenes do not match the narration cues.')
    clips = []
    for index, segment in enumerate(segments):
        clip_start = leading_trim + segment['capture_seconds']
        clip_end = (leading_trim + segments[index + 1]['capture_seconds']
                    if index + 1 < len(segments) else duration)
        captured = clip_end - clip_start
        if captured <= .2:
            raise RuntimeError('A scene has no readable capture; discard this take.')
        target = PLAYBACK_CUES[index + 1] - PLAYBACK_CUES[index]
        segment.update({'playback_start_seconds': PLAYBACK_CUES[index],
                        'playback_end_seconds': PLAYBACK_CUES[index + 1],
                        'capture_duration_seconds': round(captured, 3)})
        clip = recordings / f'script-scene-{index:02d}.mp4'
        subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                        '-ss', str(clip_start), '-t', str(captured), '-i', str(source),
                        '-vf', f'setpts={target / captured:.10f}*(PTS-STARTPTS),fps=25,tpad=stop_mode=clone:stop_duration=2,setpts=N/(25*TB)',
                        '-frames:v', str(target * 25), '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                        '-pix_fmt', 'yuv420p', str(clip)], check=True)
        clips.append(clip)
    clip_list = recordings / 'script-scenes.txt'
    clip_list.write_text(''.join(f"file '{clip.name}'\n" for clip in clips), encoding='utf-8')
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
                    '-i', str(clip_list), '-c', 'copy', '-movflags', '+faststart', str(destination)], check=True)
    actual_duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                                    '-of', 'default=noprint_wrappers=1:nokey=1', str(destination)], text=True).strip())
    if abs(actual_duration - 180) > .001:
        raise RuntimeError('The exported recording does not have the required three-minute duration.')
    manifest = {'run_id': run['id'], 'data_mode': run['mode'], 'synthetic_preview': synthetic, 'duration_seconds': 180,
                'acquisition_mode': run.get('acquisition_mode'), 'analysis_status': run.get('analysis_status'),
                'captioned': True, 'narration': False, 'actual_duration_seconds': actual_duration,
                'llm_configuration': provider, 'segments': segments,
                'captured_seconds': round(captured_seconds, 3), 'leading_trim_seconds': round(leading_trim, 3),
                'editing': 'Each scene edited to the supplied narration cues; not a live latency benchmark.',
                'llm_required_for_supported_answers': args.require_llm,
                'coverage_presentation': 'Compact neutral data-status panel; saved values and partial-coverage limitations are unchanged.',
                'page_errors': page_errors, 'new_acquisitions': acquisitions, 'failed_http_responses': failed_responses,
                'replies': replies, 'reports': reports, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                'attributions': [credits], 'limitations': 'Captioned saved-run walkthrough edited to three minutes, not a new acquisition, runtime benchmark, field-accuracy validation or native Nepali sign-off. Actual response-engine labels remain visible.'}
    destination.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'video': str(destination.resolve()), 'data_mode': run['mode'], 'bytes': destination.stat().st_size}, indent=2))


if __name__ == '__main__':
    main()
