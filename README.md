# Flood Lens — Mapping Flood Damage from Space

Flood Lens maps potential new water and debris, intersects pre-event infrastructure, estimates lost road access and produces English/Nepali situation reports. Satellite acquisition, terrain processing, the map dashboard, the copilot and evaluation remain available.

The water-segmentation model is now a **user-supplied adapter**. This project contains no Kuro Siwo/Sen1Floods11 downloaders, training-data parsers or water-model training scripts. The threshold baseline is the default, so the app starts without model weights or training datasets.

## Run

```bash
uv sync --frozen --python 3.12
./run.sh
```

Open **http://127.0.0.1:8000**. On a fresh checkout the workspace starts empty; enter bounds/date and run an actual analysis, or explicitly select synthetic mode to try the interface without downloading satellite data.

```bash
# Synthetic fixtures; no observations of the actual Nepal event
uv run python scripts/analyze.py --mode demo
# Actual imagery and pre-event OSM; threshold detection, no trained model required
uv run python scripts/analyze.py --water-model threshold
```

Actual analysis retrieves original Sentinel-1 VV/VH measurements and annotations from the public Microsoft mirror, processes radar locally using Copernicus DEM GLO-30, and uses Sentinel-2 L2A reflectance/cloud classifications when available. Network/provider availability is required. Failed acquisition never substitutes synthetic inputs. The public path requires no satellite account; the optional Copernicus Sentinel Hub provider needs `CDSE_CLIENT_ID` and `CDSE_CLIENT_SECRET`. The historical ohsome v2 fallback needs `OHSOME_API_KEY` when v1 is unavailable. Export variables in your shell; `.env` is not loaded automatically.

Historical OSM is selected strictly before the event. The Nepal case uses 27 July 2026 or earlier. Published damage maps and post-event OSM edits are excluded from inference inputs. Ready-made mirror radar RTC using PlanetDEM is excluded; the local processor uses Copernicus DEM. EGM2008 is used only for vertical-datum conversion.

## Add your model later

Put your model's weights in **`models/weights/`** and implement **`models/water_model.py`**. Different model formats need their own loading/preprocessing code; copying an arbitrary weight file alone is not sufficient.

The adapter implements `model_info()` and `predict_water(vv_db, vh_db, valid_mask)`. It receives aligned terrain-corrected radar in dB and returns a 2D water-probability raster. Set `configured=True` after implementing/loading the model. You may instead set `FLOOD_LENS_MODEL_ADAPTER` to another importable Python module with the same interface. Restart the server after changing the adapter.

See **[model integration instructions](docs/MODEL_INTEGRATION.md)** for shapes, channel requirements, metadata, weights, tiling and validation. No model architecture or training dataset is hardcoded. Install your chosen inference framework into this project's environment only when you know the model's requirements.

```bash
# Once your adapter is ready
uv run python scripts/analyze.py --water-model external
```

`/api/water-model` reports whether the adapter is ready. The dashboard enables external-model selection only when it is configured. Explicit requests for an absent/broken model fail before acquiring satellite data. Model outputs are checked for alignment, finite probability ranges and observation validity; failures never silently switch detection methods. Training sources and citations come from the adapter's metadata.

## Maps, access and reports

Threshold detection compares pre/post radar and clear optical water indices; external mode compares the model's pre/post water probabilities. Both require radar change for radar-derived new water, terrain eligibility and minimum component size. Optical vegetation/bare-surface changes supply low-confidence debris candidates.

Exposure denotes intersection with historical OSM, not confirmed structural damage. A directed road graph honors one-way and basic vehicle restrictions, removes candidate-intersecting segments under an assumed closure scenario, and tests alternative destinations. Unobserved routes are uncertain. Turn restrictions, complete node identity, facility operation and routes outside the area remain unverified.

The copilot uses an authored bilingual intent classifier and fact templates. All counts and names come from saved analysis statistics/layers. Unsupported casualty and population questions return no estimate. Native-speaker Nepali review remains necessary. English PDF and bilingual printable HTML reports include provenance, uncertainty and attribution. The D8 downhill trace is terrain screening, not hydraulic simulation.

Exports include probability/class rasters, eligibility masks, source metadata, GeoJSON and audit JSON. Processing is bounded to two concurrent jobs, one degree per side and 2,500 raster pixels per side. Runs persist; in-flight job state does not. `FLOOD_LENS_DATA` changes the run directory and `FLOOD_LENS_PORT` changes the local server port.

## Independent reference evaluation

Official EMSR927 products remain a separate evaluation-only path under **`data/reference_only/EMSR927/`**. They are never passed to the model adapter or prediction pipeline.

```bash
uv run python scripts/fetch_reference.py
uv run python scripts/evaluate.py data/runs/RUN_ID \
  data/reference_only/EMSR927/reference-extent.geojson \
  --coverage data/reference_only/EMSR927/reviewed-coverage.geojson \
  --output data/runs/RUN_ID/evaluation.json
uv run python scripts/evaluate_assets.py data/runs/RUN_ID
uv run python scripts/summarize_data.py data/runs/RUN_ID
```

The supplied EMS observed-event polygons describe landslides. The evaluator compares debris candidates against that class and reports water validation as unavailable. A landslide overlap score is not flood-water accuracy. Evaluation does not alter prediction files. Synthetic evaluation is an explicitly labeled sanity check only.

## Checks and historical outputs

```bash
uv run pytest -q
node --check app/static/app.js
```

Existing case studies, model cards, reports and demo videos in `docs/` are **historical outputs from the removed baseline model**. Their scores/counts do not validate the current placeholder or a future model. Regenerate results and submission artifacts after adding and independently validating your model. The optional browser/demo scripts need Playwright, Chromium and, for video, ffmpeg.

API documentation is at `/docs`. Useful endpoints include `/api/health`, `/api/data-sources`, `/api/water-model`, `/api/analyses/latest-real`, `POST /api/analyses`, and per-run status, copilot, report, trace and file exports.

## Attribution

Contains modified Copernicus Sentinel data 2026.

Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

© OpenStreetMap contributors. OSM retains ODbL obligations. Official evaluation data: European Union, Copernicus Emergency Management Service data.

Approved external-training references: Bountos et al. (2024), *Kuro Siwo*, NeurIPS 37, 38105–38121; Bonafilia et al. (2020), *Sen1Floods11*, CVPR Workshops, 210–211. Cite whichever datasets your supplied model actually uses in its metadata. Source/license details: [attribution](docs/ATTRIBUTION.md).

Authored code is MIT. This is an educational prototype requiring independent accuracy and field validation.
