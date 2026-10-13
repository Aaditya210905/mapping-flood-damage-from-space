# Situation-report copilot route

Track B permits either a trained flood-segmentation model or a situation-report copilot as the AI component. This project chooses the copilot. A segmentation adapter, segmentation weights and Kuro Siwo/Sen1Floods11 training downloads are not required.

The shared mapping requirements still apply: process an area and past flood date from raw Sentinel observations, map potential new water/debris, intersect historical OSM infrastructure, assess road connectivity and compare the Nepal case against independent EMSR927 products. Copernicus DEM supports terrain processing and the optional downhill trace. Published damage maps never enter predictions.

## From observations to a briefing

1. The acquisition code retrieves compatible pre/post Sentinel-1 observations, Sentinel-2 when available, historical OSM and Copernicus DEM.
2. Fixed change-detection rules compute water/debris candidates with observation and terrain masks. Spatial intersections and road-graph calculations produce the impact statistics.
3. The saved result contains those statistics, source dates, settlement layers, uncertainty and attributions.
4. A local bilingual TF-IDF/logistic-regression classifier chooses a question topic from authored examples bundled with the application. It cannot supply numbers or names.
5. English/Nepali templates read the saved facts and return evidence paths tied to the run ID. The same facts generate situation reports.

No external language-model weights or training datasets are downloaded. The authored question examples are code fixtures, not satellite training data. The classifier is the copilot's AI method; this is not an open-ended language model.

## Supported questions and safeguards

The copilot handles mapped flood/debris area, infrastructure exposure, modeled road access, source provenance, limitations and situation-report summaries. Low-confidence or unsupported questions receive no estimate. Deaths, injuries, missing people and population are not inferred. Synthetic runs keep explicit demonstration labels in answers and reports.

Evidence identifies the saved fields used, such as `stats.water_km2`, `stats.buildings_exposed`, `layers.settlements` and `provenance.mapping`. Counts come from map calculations, not question classification. The classifier does not alter maps, thresholds or reference comparisons.

## Running and checking

Start `./run.sh`, open `http://127.0.0.1:8000`, choose an area/date and run an analysis. Use **Preview synthetic demo** to exercise the interface without acquiring satellite data. `/api/copilot` describes the assistant; `/api/data-sources` separates analysis inputs from evaluation-only maps.

Run `uv run pytest -q` and, with the server running, `uv run python scripts/check_dashboard.py`. The browser check generates synthetic inputs and checks bilingual answers, reports, source separation and error recovery. These checks establish software behavior, not actual Himalayan mapping accuracy.

Independent Nepal case-study evaluation, native-speaker Nepali review and field validation remain necessary. Exposure is not confirmed damage; assumed road interruption is not a verified closure. Keep the required Copernicus Sentinel, WorldDEM and OpenStreetMap attributions in generated outputs.
