# Copilot submission materials

The selected component is Track B Option 2: Situation-Report Copilot. Current repairs, real CDSE verification and executed tests are recorded in [repair verification](REPAIR_VERIFICATION.md). Historical preparation statements below do not override that record.

## Prepared artifacts

| Material | Location | Scope |
| --- | --- | --- |
| Current technical report | `docs/technical-report.md`, `docs/technical-report.pdf`; export copy in `output/pdf/copilot-technical-report.pdf` | Six pages, current Copilot architecture, setup, grounding, language, reports and honest limitations |
| English/Nepali one-page exports | `output/pdf/copilot-preview/preview-en.pdf`, `preview-ne.pdf` | Explicitly synthetic; not a real-event deliverable |
| Offline printable reports | Same folder, `.html` files | Embedded Devanagari fonts; no server needed |
| Typed evidence/report export | Same folder, `.json` and `manifest.json` | Source run, source checksum, missing fields and output checksums |
| Presenter walkthrough | `docs/demo-script.md` | Timed narration and judge Q&A; the user will record the video |
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

## Record the walkthrough yourself

The user will record the three-minute video manually. No completed video is supplied in this preparation package. Use the timings and narration in `docs/demo-script.md`, keep the data mode and actual response engine visible, and show the supporting evidence and report downloads. Use real data for the final case-study submission; if demonstrating the interface with generated inputs, identify it as a synthetic preview throughout. Show the required attribution and do not include victim imagery.

The following updated helper is optional and was not successfully executed to produce a video in this task. Playwright video capture also needs its FFmpeg runtime (`uv run playwright install ffmpeg`).

Start `./run.sh` with the configured key and a real saved run, then:

```bash
uv run python scripts/record_demo.py --run-id REAL_RUN_UUID \
  --output output/video/copilot-demo.mp4
```

The recorder uses an isolated Chromium context, never a private browser profile. It is designed to display the selected analysis, English/Nepali questions, supporting evidence, a compound question, an unsupported statistic and report exports. Actual mode requires a real saved current-route analysis. To create a labeled preview, explicitly use `--preview` with a current-route demo run UUID.

The helper keeps frontend response-engine labels and targets a three-minute captioned video with attribution, without voiceover. Its capture/output flow has not been validated in this task; recording was stopped at the user's request.

## Remaining external inputs

The Groq configuration is populated and loaded, but provider calls remain untested. The path to a real saved analysis has not been provided, so actual-event report exports cannot be represented as complete. A native-speaker review requires a human reviewer and is deferred alongside application testing. The preview PDFs are complete software-demonstration materials, not a substitute for real case-study results or independent EMSR927 evaluation. Video recording is assigned to the user.

Prepared current-route demo run: `1dfef292-6f22-4d29-b279-d6f92bf8e8ca`. The preview report manifest identifies this source. The old saved demo came from the removed segmentation pipeline and was not reused for the final preview exports. Use the dashboard's explicit **Preview synthetic demo** control for your own interface recording, or create/select a real analysis for the final submission. Do not describe demo measurements as real event results.

The rest of the project still needs real satellite/OSM inputs for mapping; the Copilot itself reads those computed maps and does not require Kuro Siwo, Sen1Floods11 or flood-model training.
