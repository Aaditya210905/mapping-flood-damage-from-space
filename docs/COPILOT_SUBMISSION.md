# Copilot submission materials

The selected component is Track B Option 2: Situation-Report Copilot. Current repairs, real CDSE verification and executed tests are recorded in [repair verification](REPAIR_VERIFICATION.md). Historical preparation statements below do not override that record.

## Prepared artifacts

| Material | Location | Scope |
| --- | --- | --- |
| Current technical report | `docs/technical-report.md`, `docs/technical-report.pdf`; export copy in `output/pdf/copilot-technical-report.pdf` | Six pages, current Copilot architecture, setup, grounding, language, reports and honest limitations |
| English/Nepali one-page exports | `output/pdf/repair-real/situation-report-en.pdf`, `situation-report-ne.pdf` | Cached real CDSE inputs; partial coverage and unavailable debris are explicit |
| Offline printable reports | Same folder, `.html` files | Embedded Devanagari fonts; no server needed |
| Typed evidence/report export | Same folder, `.json` and `manifest.json` | Source run, source checksum, missing fields and output checksums |
| Current demonstration | `output/video/copilot-demo.mp4`, `copilot-demo.json` | Three-minute captioned recording of the repaired interface, with real cached inputs and response metadata |
| Capture notes | `docs/demo-script.md` | Scope, reproduction and judge Q&A |
| Nepali terminology handout | `docs/NEPALI_GLOSSARY.md` | Authored phrases; independent native-speaker sign-off remains pending |
| Configuration | `.env.example`; private untracked `.env` | Startup, Groq and optional satellite OAuth placeholders |

The old technical report was copied to `docs/archive/segmentation-technical-report.*` before replacement. Other historical case-study results and old demo videos still describe the earlier implementation and should not be submitted as evidence for this Copilot.

## Groq configuration

The user supplied a Groq key in the private `.env`. Sanitized configuration reports Groq enabled/configured with model `openai/gpt-oss-120b`; the website has been restarted to load it. The key remains untracked and the file is owner-readable only (0600). Actual English and Nepali Groq calls have now succeeded on the saved real CDSE run. Rate limits also exercised the labeled deterministic fallback; credentials do not guarantee continuing service availability.

No key is committed to Git. For later changes, edit the private `.env` locally and restart `./run.sh`. `LLM_API_KEY` takes precedence if set; direct CLI commands can load configuration with `uv run --env-file .env`. In your recording, retain the visible response-engine status and do not describe a fallback response as Groq-generated.

## Produce the real report package

Real report exports from run `0fb99632-2943-4d03-9656-55f1db0682cd` are available in `output/pdf/repair-real/`. They explicitly show cached real inputs, partial coverage and unavailable debris. To export another real saved folder:

```bash
uv run --env-file .env python scripts/export_copilot_reports.py \
  data/runs/REAL_RUN_UUID --output output/pdf/copilot-real --use-llm
```

The exporter rejects demo runs unless `--allow-demo` is explicitly supplied. It preserves original raw/results files and creates derived PDF/HTML/JSON reports plus a manifest. Missing evidence is marked unavailable. Do not replace unavailable measurements with example numbers.

## Current recording and reproduction

[Watch the current demo](../output/video/copilot-demo.mp4). The companion JSON records the source run, acquisition mode, captions, actual response engines and output checksum. This is a captioned screen recording without voiceover, showing cached real CDSE run `0fb99632-2943-4d03-9656-55f1db0682cd`, rather than a new acquisition. It includes English/Nepali questions, linked map evidence, uncertain settlement access, casualty refusal, the Nepali report, N/A evaluation and required credits.

The recorder uses an isolated Chromium context and never captures a private browser profile or credentials. To reproduce it, start `./run.sh`, install Playwright's video encoder once if needed, then run:

```bash
uv run playwright install ffmpeg
uv run python scripts/record_demo.py --run-id REAL_RUN_UUID \
  --output output/video/copilot-demo.mp4
```

The actual response engine remains visible. The final MP4 is edited to three minutes; it is not a runtime benchmark. A synthetic preview requires explicit `--preview` and keeps its synthetic label throughout. Archived videos under `docs/` show the removed segmentation implementation.

## Remaining external inputs

Native-speaker Nepali approval and field verification remain pending. The current EMSR927 comparison is N/A because its landslide reference lacks eligible debris observations in this run; it does not validate water mapping. A new judge-selected AOI/date still requires compatible satellite acquisitions, historical OSM, quotas and provider availability. No satellite training dataset is required by the copilot.

Older synthetic report previews remain in `output/pdf/copilot-preview/` for software demonstrations. They are separate from the real-input reports and current video and must not be described as real event observations.
