# Flood Lens — Mapping Flood Damage from Space

Flood Lens maps potential new water and debris, intersects pre-event infrastructure, estimates lost road access and produces English/Nepali situation reports. Satellite acquisition, terrain processing, the map dashboard, the copilot and evaluation remain available.

The chosen Track B AI component is the **situation-report copilot**. Mapping uses radar and optical change-detection rules. There is no flood-segmentation model, model placeholder, weight setup or external training-dataset dependency. Kuro Siwo and Sen1Floods11 are not needed for this route.

## Current demo

**[Watch the repaired three-minute demo](output/video/flooline-repaired-demo.mp4)** — captioned screen recording using cached real CDSE inputs and historical OSM, with English/Nepali Groq answers, map citations, access uncertainty, a Groq-assisted report and evaluation limitations. It has no voiceover and does not show a new acquisition. [Recording manifest](output/video/flooline-repaired-demo.json). The earlier user recording remains preserved separately.

## Run

```bash
uv sync --frozen --python 3.12
./run.sh
```

Open **http://127.0.0.1:8000**. With CDSE credentials configured, the dashboard selects the Copernicus account provider. On a fresh checkout the workspace starts empty; enter bounds/date and run an actual analysis, or explicitly select synthetic mode to try the interface without downloading satellite data.

The dashboard shows observation status and the selected situation-report copilot. **Data & method** separates analysis inputs from independent evaluation maps. **Preview synthetic demo** generates test fixtures; its map and counts remain labeled as synthetic.

| Source | Current application use |
| --- | --- |
| Sentinel-1 VV/VH | Pre/post radar inputs, acquired on demand |
| Sentinel-2 L2A | Optical support and cloud classifications when available |
| Copernicus DEM GLO-30 | Terrain correction, slope screening and downhill trace |
| Historical OpenStreetMap | Pre-event infrastructure and road network |
| EMSR927 | Independent evaluation only; excluded from prediction inputs |

```bash
# Synthetic fixtures; no observations of the actual Nepal event
uv run python scripts/analyze.py --mode demo
# Actual imagery and pre-event OSM; rule-based change detection
uv run python scripts/analyze.py
```

Actual analysis retrieves original Sentinel-1 VV/VH measurements and annotations from the public Microsoft mirror, processes radar locally using Copernicus DEM GLO-30, and uses Sentinel-2 L2A reflectance/cloud classifications when available. Network/provider availability is required. Failed acquisition never substitutes synthetic inputs. The public path requires no satellite account; the optional Copernicus Sentinel Hub provider needs `CDSE_CLIENT_ID` and `CDSE_CLIENT_SECRET`. The historical ohsome v2 fallback needs `OHSOME_API_KEY` when v1 is unavailable. `./run.sh` loads an optional `.env` using uv. Direct CLI commands use exported variables or `uv run --env-file .env`.

With `satellite_provider=copernicus`, radar and available optical imagery come from CDSE using those credentials. If CDSE returns HTTP 403 specifically for the DEM, the application fetches actual Copernicus GLO-30 elevation from its public mirror and records that source change in provenance and warnings. Other acquisition errors still fail the run; no demo data is substituted.

Historical OSM is selected strictly before the event. The Nepal case uses 27 July 2026 or earlier. Published damage maps and post-event OSM edits are excluded from inference inputs. Ready-made mirror radar RTC using PlanetDEM is excluded; the local processor uses Copernicus DEM. EGM2008 is used only for vertical-datum conversion.

## Situation-report copilot

Ask questions about flood extent, infrastructure exposure, road access, acquisition dates or limitations, and request a short briefing in English or Nepali. Every reported area, count and settlement name comes from the saved analysis; responses include evidence paths and the run ID. Unsupported casualty and population questions return no estimate.

The optional generative integration uses **Groq’s official SDK**, defaulting to hosted `openai/gpt-oss-120b`. Recognised questions retrieve saved evidence locally and normally need one generation request. Groq writes concise narrative around validated fact placeholders; Python binds run, subject, field and interpretation, inserts authoritative values and preserves citations/uncertainty. The labeled fallback uses deterministic bilingual routing. Hospital inventory, radar coverage and eligibility have separate evidence; mixed questions retain supported answers while declining unsupported estimates. Matching lists are paginated locally and displayed with totals/export links. Cached feature-name lookup avoids scanning every name with a regular expression. This application trains no model. Named features and follow-ups use saved identifiers and route evidence; ambiguous references request clarification. No model weights or satellite training datasets are downloaded. Native-speaker Nepali review remains necessary. See [audit repairs](docs/AUDIT_REPAIRS.md).

Processing defaults are in `app/mapping.toml`; the Nepal preset is in `app/presets.py`. Selected configuration is recorded per run. The dashboard and CLI share defaults, and custom areas do not inherit Nepal labels or a global date cutoff. See the [hardcoding audit](docs/HARDCODING_AUDIT.md) and [PDF requirement checklist](docs/TRACK_B_CHECKLIST.md).

Copy `.env.example` to a new private `.env`, set `LLM_ENABLED=true` and `GROQ_API_KEY`, then restart `./run.sh`. Without credentials, or on provider/validation failure, the application stays usable. Each response identifies which engine actually produced it. Configuration and limitations: [Copilot documentation](docs/COPILOT.md).

See **[copilot scope and grounding](docs/COPILOT.md)**. `/api/copilot` reports its method and supported languages. The removed segmentation-model API/CLI option is no longer available; requests for external or trained segmentation explicitly fail before acquisition.

## Maps, access and reports

Rule-based detection compares pre/post radar and clear optical water indices. Radar-derived new water requires a non-water baseline, low post-event backscatter, sufficient radar change, terrain eligibility and minimum component size. Optical vegetation/bare-surface changes supply low-confidence debris candidates.

Exposure denotes intersection with historical OSM, not confirmed structural damage. Building observation coverage is area-weighted over each footprint, and hazard classes retain separate eligibility. The directed road graph honors one-way and basic vehicle restrictions. Its closure scenario requires continuous overlap of at least half a grid cell; water beneath a mapped bridge does not prove a failed crossing. Snapping is limited to 100 m and off-road connectors remain unverified. Hospital and town connectivity are computed separately, with distinct before/after destinations and same-target detours. Unobserved routes, turn restrictions, complete node identity, facility operation and routes outside the area remain uncertain.

English and Nepali **one-page PDFs**, printable HTML and typed JSON reports include provenance, uncertainty and attribution. **Generate report** stores a separate snapshot with download links. PDFs use WeasyPrint/Pango and bundled Noto Devanagari fonts. Missing measurements remain unavailable; incomplete reports are visibly labeled. The D8 downhill trace is terrain screening, not hydraulic simulation.

Exports include class rasters, eligibility masks, source metadata, GeoJSON and audit JSON. Processing is bounded to two concurrent jobs, one degree per side and 2,500 raster pixels per side. Runs and job progress persist; after a restart, unfinished jobs are explicitly marked interrupted and must be resubmitted. `FLOOD_LENS_DATA` changes the run directory and `FLOOD_LENS_PORT` changes the local server port.

## Observation availability and cached real inputs

A completed job can have **partial scientific coverage**. `class_availability` records separate water/debris eligibility, missing reasons and coverage. An unavailable class has a null area; an observed zero applies only to eligible pixels. The UI, copilot and reports preserve this distinction, including older saved runs without rewriting their evidence.

The dashboard prefers a saved actual run over a demo, shows coverage and unknown access prominently, and links cited facts to map features. Settlement cards load long route geometries only on demand. Parsed results use a bounded cache invalidated by source changes, and a small source-bound metadata index avoids repeatedly reading large result files.

Reprocess an existing validated CDSE cache without new downloads, into a **new UUID**:

```bash
uv run python scripts/reprocess.py data/runs/SOURCE_RUN_UUID
```

This reads only retained acquired S1/S2, Copernicus DEM and historical OSM inputs. It verifies scene/grid/date/source contracts and hashes, preserves original provenance, and labels the new run `cached_reprocessing`. It never uses old predictions or EMS products as mapping inputs. It does not represent a new live acquisition or support a different AOI/date. The current replay utility intentionally rejects public raw-GRD caches and synthetic caches.

## Independent reference evaluation

Official EMSR927 products remain a separate evaluation-only path under **`data/reference_only/EMSR927/`**. They are never passed to acquisition or the prediction pipeline.

```bash
uv run python scripts/fetch_reference.py
uv run python scripts/evaluate.py data/runs/RUN_ID \
  data/reference_only/EMSR927/reference-extent.geojson \
  --coverage data/reference_only/EMSR927/reviewed-coverage.geojson \
  --output data/runs/RUN_ID/evaluation.json
uv run python scripts/evaluate_assets.py data/runs/RUN_ID
uv run python scripts/summarize_data.py data/runs/RUN_ID
```

The evaluator requires matching water/debris reference classes and each class's eligible mask. Unknown legends require explicit review (`--reference-class water|debris|combined`). Both-empty comparisons and unobservable classes report N/A, never a perfect score. It reports the fraction of reference-positive area that was actually evaluated. A landslide overlap score is not flood-water accuracy. Evaluation does not alter prediction files. Synthetic evaluation is an explicitly labeled sanity check only.

## Checks and historical outputs

```bash
uv run pytest -q
node --check app/static/app.js
# With the local server running; creates a clearly labeled synthetic run
uv run python scripts/check_dashboard.py
# A real saved analysis and configured Groq; fail if it falls back
uv run python scripts/verify_copilot.py --require-llm
# Explicitly synthetic software checks on an existing demo run
uv run python scripts/verify_copilot.py --run-id RUN_ID --allow-demo
```

The current six-page Copilot report is [technical-report.pdf](docs/technical-report.pdf), with editable [source](docs/technical-report.md). Prepared materials are listed in [Copilot submission setup](docs/COPILOT_SUBMISSION.md). Real Groq English/Nepali one-page PDF, offline HTML, JSON and a checksum manifest are under `output/pdf/copilot-real/`. Independent extent/asset comparisons and a fresh-acquisition summary are under `output/case-study/`. The [current three-minute demo](output/video/flooline-repaired-demo.mp4) records the repaired interface using cached real inputs; [capture notes](docs/demo-script.md) describe its scope. Current checks and remaining external work are in the [PDF checklist](docs/TRACK_B_CHECKLIST.md).

Older case studies, model cards, situation reports and videos in `docs/` remain **historical outputs from the removed segmentation model**. Their scores/counts do not validate the current rule-based pipeline. The previous technical report is preserved in `docs/archive/segmentation-technical-report.*`. A real CDSE current-pipeline analysis is available locally; its observed coverage is partial and does not establish field accuracy. Use the current repair verification rather than archived segmentation scores. Recording uses Playwright, isolated Chromium and ffmpeg.

```bash
# Real saved analysis; no tests or new acquisitions are run by the exporter
uv run --env-file .env python scripts/export_copilot_reports.py data/runs/REAL_RUN_UUID --output output/pdf/copilot-real --use-llm
uv run python scripts/record_demo.py --run-id REAL_RUN_UUID --require-llm --output output/video/flooline-repaired-demo.mp4
# Synthetic software walkthrough requires explicit permission
uv run python scripts/record_demo.py --run-id DEMO_RUN_UUID --preview --output output/video/copilot-preview.mp4
uv run python scripts/build_report.py
```

See the [Copilot implementation report](docs/COPILOT_IMPLEMENTATION_REPORT.md) for executed checks and remaining live-validation requirements.

API documentation is at `/docs`. Useful endpoints include `/api/health`, `/api/data-sources`, `/api/copilot`, `/api/analyses/latest-real`, `POST /api/analyses`, and per-run status, copilot, report, trace and file exports.

## Attribution

Contains modified Copernicus Sentinel data 2026.

Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

© OpenStreetMap contributors. OSM retains ODbL obligations. Official evaluation data: European Union, Copernicus Emergency Management Service data.

No external training dataset is used by the current application. Historical artifacts retain their original citations. Source/license details: [attribution](docs/ATTRIBUTION.md).

Authored code is MIT. This is an educational prototype requiring independent accuracy and field validation.
