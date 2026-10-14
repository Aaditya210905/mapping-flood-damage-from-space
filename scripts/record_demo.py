"""Record the current Copilot interface into a captioned three-minute video.

Defaults to real data. --preview explicitly permits a labeled synthetic walkthrough.
No model keys or private browser profiles are recorded.
"""
import argparse
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
        selected = client.get('/api/analyses/' + args.run_id if args.run_id else '/api/analyses/latest-real')
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
    label = 'SYNTHETIC SOFTWARE PREVIEW - no real disaster observations' if synthetic else 'SAVED SATELLITE ANALYSIS - estimates require field verification'
    segments = []
    with sync_playwright() as playwright:
        options = {'headless': True, 'chromium_sandbox': True}
        if shutil.which('chromium'):
            options['executable_path'] = shutil.which('chromium')
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={'width': 1280, 'height': 900},
                                      record_video_dir=str(recordings), record_video_size={'width': 1280, 'height': 900})
        context.add_init_script('localStorage.setItem("flood-lens-run",' + json.dumps(run['id']) + ')')
        page = context.new_page()
        started = time.monotonic()

        def caption(text):
            segments.append({'capture_seconds': round(time.monotonic() - started, 2), 'caption': text})
            page.evaluate("""([label,text]) => {
                let banner = document.getElementById('demo-banner');
                if (!banner) { banner=document.createElement('div'); banner.id='demo-banner'; document.body.append(banner); }
                banner.style.cssText='position:fixed;top:0;left:0;right:0;z-index:999999;padding:10px 24px;background:#173d35;color:white;font:700 13px/1.4 Arial;pointer-events:none';
                banner.textContent=label;
                let box=document.getElementById('demo-caption');
                if (!box) { box=document.createElement('div'); box.id='demo-caption'; document.body.append(box); }
                box.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:999999;padding:14px 28px;background:#173d35f5;color:#f6f8f1;font:14px/1.55 Arial;pointer-events:none';
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
            page.locator('#ask-button').click()
            page.wait_for_function('!state.asking', timeout=45000)
            page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')

        page.goto(base, wait_until='networkidle')
        page.wait_for_function('state.run !== null', timeout=30000)
        provider = health.get('llm', {})
        caption('FloodLens AI: Situation-Report Copilot. Select an analysis, ask about mapped findings and produce a bilingual briefing. ' +
                ('This walkthrough uses generated inputs and the displayed response engine.' if synthetic else 'All numerical statements come from this selected saved analysis.'))
        until(16)
        page.locator('#copilot-form').scroll_into_view_if_needed()
        caption('Every question stays attached to the selected run. Groq is available when configured; the deterministic fallback remains usable. Current configuration: ' +
                ('Groq enabled with a local key.' if provider.get('configured') else 'Groq is not configured; this recording demonstrates fallback.'))
        ask('How large is the newly detected water area?', 'en')
        until(38)
        page.locator('.evidence summary').last.click()
        caption('Supporting evidence exposes fact identifiers, source paths, canonical values and computation methods. Potential change is not confirmed damage.')
        page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')
        until(55)
        caption('The explicit language selector controls the answer. Nepali preserves saved measurements, names, identifiers and uncertainty.')
        ask('कति भवन प्रभावित छन्?', 'ne')
        until(78)
        caption('Compound questions retrieve infrastructure and settlement facts separately. Modeled disconnection assumes intersecting roads are impassable; actual closures remain unverified.')
        ask('Which roads are exposed and which settlements are isolated?', 'en')
        until(104)
        caption('Unsupported questions receive no invented statistics. These map inputs cannot establish deaths, injuries, missing people or population.')
        ask('How many people died?', 'en')
        until(119)
        page.locator('#language').select_option('ne')
        page.locator('#generate-report').click()
        page.wait_for_function('!state.asking && document.querySelector(".report-downloads a") !== null', timeout=45000)
        page.locator('#conversation').evaluate('element => element.scrollTop=element.scrollHeight')
        caption('Generate an English or Nepali one-page report. HTML, PDF and JSON links retain the selected-run snapshot, source information, unavailable fields and attribution.')
        until(134)
        report_url = page.locator('.report-downloads a').filter(has_text='HTML').last.get_attribute('href')
        page.goto(base + report_url, wait_until='networkidle')
        caption('Nepali rendering uses bundled Devanagari fonts. This report clearly marks synthetic observations and unavailable dates. Independent native-speaker review remains pending.' if synthetic else
                'The report uses recorded acquisitions and computed map facts. Missing measurements remain unavailable. Independent language and field review remain necessary.')
        until(150)
        page.goto(base + '/api/analyses/' + run['id'] + '/report?language=en', wait_until='networkidle')
        caption('English and Nepali reports use the same underlying measurements. The validator binds each claim to its run, subject, field and interpretation; it does not prove physical truth.')
        until(160)
        credits = 'Contains modified Copernicus Sentinel data 2026. Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. © OpenStreetMap contributors.'
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        caption(credits + ' Educational prototype. Reference damage maps are evaluation-only. No satellite training dataset is used.')
        until(181)
        video = page.video
        page.close()
        context.close()
        source = Path(video.path())
        browser.close()
    duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                             '-of', 'default=noprint_wrappers=1:nokey=1', str(source)], text=True).strip())
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', str(source),
                    '-vf', f'setpts={180 / duration:.10f}*PTS,fps=25,tpad=stop_mode=clone:stop_duration=1',
                    '-t', '180', '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(destination)], check=True)
    manifest = {'run_id': run['id'], 'data_mode': run['mode'], 'synthetic_preview': synthetic, 'duration_seconds': 180,
                'captioned': True, 'narration': False, 'llm_configuration': provider, 'segments': segments,
                'attributions': [credits], 'limitations': 'Interface walkthrough, not a test or evidence of field accuracy. Actual response-engine labels remain visible.'}
    destination.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'video': str(destination.resolve()), 'data_mode': run['mode'], 'bytes': destination.stat().st_size}, indent=2))


if __name__ == '__main__':
    main()
