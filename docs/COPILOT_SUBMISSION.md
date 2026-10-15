# Copilot submission materials

The selected component is Track B Option 2: Situation-Report Copilot. Current executed checks are in [audit repairs](AUDIT_REPAIRS.md), [hardcoding audit](HARDCODING_AUDIT.md) and the [PDF requirement checklist](TRACK_B_CHECKLIST.md). The repaired artifacts below use current evidence; prior user recordings and historical outputs remain preserved.

## Prepared artifacts

| Material | Location | Scope |
| --- | --- | --- |
| Current technical report | `docs/technical-report.md`, `docs/technical-report.pdf`; export copy in `output/pdf/copilot-technical-report.pdf` | Six pages, current Copilot architecture, setup, grounding, language, reports and honest limitations |
| English/Nepali one-page exports | `output/pdf/copilot-real/situation-report-en.pdf`, `situation-report-ne.pdf` | Genuine Groq-assisted snapshots; cached real CDSE inputs, partial coverage and unavailable debris are explicit |
| Offline printable reports | Same folder, `.html` files | Embedded Devanagari fonts; no server needed |
| Typed evidence/report export | Same folder, `.json` and `manifest.json` | Source run, source checksum, missing fields and output checksums |
| Current demonstration | `output/video/flooline-repaired-demo.mp4`, `flooline-repaired-demo.json` | Exactly 180-second captioned recording; genuine English/Nepali Groq answers and Nepali report, saved real inputs, response metadata and checksum |
| Independent case study | `output/case-study/` | Extent and official asset comparison, source/output hashes, class-specific N/A explanations and a separate fresh-acquisition summary |
| Capture notes | `docs/demo-script.md` | Scope, reproduction and judge Q&A |
| Nepali terminology handout | `docs/NEPALI_GLOSSARY.md` | Authored phrases; independent native-speaker sign-off remains pending |
| Configuration | `.env.example`; private untracked `.env` | Startup, Groq and optional satellite OAuth placeholders |

The old technical report was copied to `docs/archive/segmentation-technical-report.*` before replacement. Other historical case-study results and old demo videos still describe the earlier implementation and should not be submitted as evidence for this Copilot.

## Groq configuration

The user supplied a Groq key in the private `.env`. Sanitized configuration reports Groq enabled/configured with model `openai/gpt-oss-120b`; the website has been restarted to load it. The key remains untracked and the file is owner-readable only (0600). Actual English and Nepali Groq calls have now succeeded on the saved real CDSE run. Rate limits also exercised the labeled deterministic fallback; credentials do not guarantee continuing service availability.

No key is committed to Git. For later changes, edit the private `.env` locally and restart `./run.sh`. `LLM_API_KEY` takes precedence if set; direct CLI commands can load configuration with `uv run --env-file .env`. In your recording, retain the visible response-engine status and do not describe a fallback response as Groq-generated.

## Produce the real report package

Real Groq report exports from run `0717d3b1-ac72-48d2-a8bb-8ecb93e9663c` are in `output/pdf/copilot-real/`. Both generated in one provider request and rendered one page. They explicitly show cached real inputs, partial coverage and unavailable debris. The final six-check API verification record is `output/qa/audit-real-api.json` (local QA, excluded from Git). To export another real saved folder:

```bash
uv run --env-file .env python scripts/export_copilot_reports.py \
  data/runs/REAL_RUN_UUID --output output/pdf/copilot-real --use-llm
```

The exporter rejects demo runs unless `--allow-demo` is explicitly supplied. It preserves original raw/results files and creates derived PDF/HTML/JSON reports plus a manifest. Missing evidence is marked unavailable. Do not replace unavailable measurements with example numbers.

To verify and export actual API snapshots together, avoiding extra generation calls:

```bash
uv run python scripts/verify_copilot.py --run-id REAL_RUN_UUID --require-llm \
  --request-gap-seconds 45 --export-reports output/pdf/copilot-real
```

The exporter also accepts `--verified-api-record output/qa/copilot-e2e.json` to reuse persisted snapshots. It validates ownership and every selected fact against the saved source. A separate fresh small-area acquisition completed as `25236045-4d1e-4fd5-baac-26918b4224e7`; its source summary is packaged with the case study. It does not replace the wider corridor case or guarantee all future judge-selected areas.

## Current recording and reproduction

[Watch the current repaired demo](../output/video/flooline-repaired-demo.mp4). The companion JSON records the source run, acquisition mode, captions, actual response engines and output checksum. It is a captioned recording without voiceover of cached real CDSE run `0717d3b1-ac72-48d2-a8bb-8ecb93e9663c`. It includes genuine English/Nepali Groq questions, linked evidence, uncertain settlement access, source-provenance inspection, a genuine Groq-assisted Nepali report, N/A evaluation and credits. No new acquisition is shown. Previous `flooline-final-demo.*` user artifacts are unchanged.

The recorder uses an isolated Chromium context and never captures a private browser profile or credentials. To reproduce it, start `./run.sh`, install Playwright's video encoder once if needed, then run:

```bash
uv run playwright install ffmpeg
uv run python scripts/record_demo.py --run-id REAL_RUN_UUID --require-llm \
  --output output/video/flooline-repaired-demo.mp4
```

The actual response engine remains visible. Each scene is edited to the fixed cues in [the narration](demo-script.md); the MP4 is exactly three minutes and is not a runtime benchmark. `--require-llm` rejects fallback for supported questions and the generated report. Preserved [narration text](../output/video/flooline-final-voiceover.txt) and [timed cues](../output/video/flooline-final-voiceover.srt) remain available for adding audio. Synthetic previews require explicit `--preview` and keep their label. Archived `docs/` videos show the removed segmentation implementation.

## Remaining external inputs

Native-speaker Nepali approval and independent field/mapping verification remain pending. Extent and asset EMSR927 comparison are N/A because matching/joint debris observations are unavailable; neither validates water accuracy. A new judge-selected AOI/date still needs compatible acquisitions, historical OSM, quotas and provider availability. Local changes need review and commit/push before GitHub submission; this task has not published them. No satellite training dataset is required by the copilot.

Older synthetic report previews remain in `output/pdf/copilot-preview/` for software demonstrations. They are separate from the real-input reports and current video and must not be described as real event observations.
