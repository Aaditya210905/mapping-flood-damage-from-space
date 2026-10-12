# Flood Lens — Mapping Flood Damage from Space

An educational project built from **Track B.pdf**, now using actual observations and both listed training datasets. The application maps potential new water and possible debris, intersects pre-event infrastructure, and checks road connectivity. A bilingual copilot answers from saved map facts.

## Run

Python 3.12 and [uv](https://docs.astral.sh/uv/) are recommended:

```bash
./run.sh
```

Open **http://127.0.0.1:8000**. The dashboard loads the most recent completed **actual-data** run in this workspace. It also shows training downloads and measured model scores. Synthetic mode is available explicitly for testing.

On a fresh checkout, acquire data and train before running an actual analysis:

```bash
uv sync --frozen --python 3.12
uv run python scripts/download_datasets.py
uv run python scripts/train_water_model.py
uv run python scripts/fetch_reference.py
uv run python scripts/analyze.py
./run.sh
```

The public acquisition path ran here without account credentials. It downloads original ESA Sentinel measurements from a public mirror and processes radar locally using Copernicus DEM. Network access and provider availability are required. It does not substitute generated data when retrieval fails. Local data and model artifacts are ignored by Git; the commands reproduce them. Initial downloads occupy roughly 0.5 GB, plus source caches and run exports.

## All PDF data sources and their roles

| Data | PDF / original link | Actual use |
| --- | --- | --- |
| Sentinel-1 | [Copernicus Data Space](https://dataspace.copernicus.eu/) | Original VV GRD measurements and annotation; before/after calibration and terrain correction |
| Sentinel-2 | [Copernicus Data Space](https://dataspace.copernicus.eu/) | Before/after L2A reflectance and SCL; same-acquisition tile mosaics and cloud masking |
| Pre-event OpenStreetMap | [ohsome v1](https://api.ohsome.org/v1) | Historical building, road, bridge, settlement and destination geometries |
| Copernicus DEM GLO-30 | [Copernicus Data Space](https://dataspace.copernicus.eu/) | Radar terrain correction, slope mask and downhill tracing |
| Kuro Siwo | [Authors' repository](https://github.com/Orion-AI-Lab/KuroSiwo) | Author-linked Hugging Face GRD samples, model training and held-out testing |
| Sen1Floods11 | [Authors' repository](https://github.com/cloudtostreet/Sen1Floods11) | Original Google Cloud hand-labeled radar and labels, training/validation/testing |
| Copernicus EMSR927 | [Official activation](https://mapping.emergency.copernicus.eu/activations/EMSR927/) | Official vector products for separate evaluation only |

The current training download contains **72 Kuro Siwo samples** (48 train, 24 test) and **96 Sen1Floods11 chips** (32 each train/validation/test). These are real initial subsets, not the full benchmarks. The model uses 327,680 labeled pixels from 80 training chips. Publisher splits are preserved; checks reject duplicate labeled chip content across splits and shared Kuro Siwo train/test events. Sen1Floods11 held-out chips may share events with training.

To expand downloads:

```bash
# All 431 chips in the publisher's hand-labeled train/valid/test CSVs
uv run python scripts/download_datasets.py --dataset sen1floods11 --sen-per-split 0
# More complete samples per author-published Kuro Siwo GRD shard
uv run python scripts/download_datasets.py --dataset kuro_siwo --kuro-per-shard 64
uv run python scripts/train_water_model.py
```

Kuro Siwo is distributed in multi-gigabyte archives. The downloader uses verified HTTP byte ranges to retrieve complete initial samples without downloading every archive. It records original URLs, split/event identities and SHA-256 checksums. Downloads and labels are under `data/datasets/training/`; EMS files live separately under `data/datasets/evaluation/EMSR927/`.

The public Sentinel source is the [Microsoft Planetary Computer catalog](https://planetarycomputer.microsoft.com/api/stac/v1) serving original Copernicus observations. The original PDF portal and actual storage URLs are both retained. Ready-made mirror RTC products are excluded because their terrain processing uses PlanetDEM. DEM tiles come from the [Copernicus public distribution](https://registry.opendata.aws/copernicus-dem/). EGM2008 geoid data converts DEM heights to the ellipsoid for sensor geometry; it is a datum conversion, not another prediction layer.

For direct Copernicus processing, create a [Sentinel Hub OAuth client](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Overview/Authentication.html), export `CDSE_CLIENT_ID` and `CDSE_CLIENT_SECRET`, restart the server and select the Copernicus provider. `.env.example` documents variables; the application does not execute or automatically load `.env`. No credentials are needed for the verified public mirror path. Historical ohsome extraction tries snapshot geometry, then full-history geometry at a strictly pre-event instant, then authenticated v2 with `OHSOME_API_KEY` if necessary.

## Model and measured limits

A locally trained ExtraTrees model classifies observed water from VV dB, local median, residual and local variation. Kuro Siwo linear intensities are converted to dB; permanent and flooded water labels become water, and ignored label 3 is excluded. Sen1Floods11 uses the publisher's dB values, water label 1 and ignored label −1. No pretrained executable/model is downloaded.

| Held-out initial subset | Water IoU | Recall |
| --- | --- | --- |
| Kuro Siwo test | 0.111 | 0.115 |
| Sen1Floods11 validation | 0.499 | 0.555 |
| Sen1Floods11 test | 0.468 | 0.494 |

These are modest baseline results, especially on Kuro Siwo. They do not establish Himalayan accuracy. Training sigma0 and live terrain-corrected gamma0, resolution, terrain and sample selection differ. The model card exposes these limitations and all measured counts; `/api/water-model` returns it. A threshold alternative remains available for comparison.

New water requires post-event water probability at least 0.5, a non-water baseline, a radar drop over 3 dB, observed feature context and target slope below 20°. Small components are removed. Clear Sentinel-2 pixels can support new MNDWI water and vegetation-loss/radar-change debris candidates. Cloudy/missing pixels are excluded; debris remains a low-confidence rule-based candidate.

Potential exposure is the intersection of mapped changes and pre-event OSM. Roads form an undirected multigraph; settlements and destinations snap within 1 km. Only settlements with baseline connectivity that lose every mapped destination after exposed segments are removed count as newly cut off. Alternate paths are tested. Turn restrictions, complete access rules, routes outside the area and actual road closures remain unverified.

The English/Nepali copilot uses a small authored intent classifier and fact templates. Every number comes from saved statistics, layers or provenance. It declines unsupported casualty and population questions. Nepali output requires native-speaker review.

## Actual Nepal case and official comparison

See [case-study results](docs/case-study.md), [data inventory](docs/data-status.json), [model card](docs/water-model-card.json) and [verification](docs/verification.md). An empty change map is not evidence that the event caused no damage: conservative terrain masking, low model recall, clouds and acquisition timing can omit changes.

The six downloaded final EMSR927 products include initial grading and monitoring. Comparison uses initial observed-event polygons and reviewed product coverage minus officially not-analysed areas. Their observed-event class is **landslide**, so the resulting overlap is explicitly a **landslide-class diagnostic, not flood-water accuracy**. EMS data never enters acquisition, training or prediction.

```bash
uv run python scripts/evaluate.py data/runs/RUN_ID   data/datasets/evaluation/EMSR927/reference-extent.geojson   --coverage data/datasets/evaluation/EMSR927/reviewed-coverage.geojson   --output data/runs/RUN_ID/evaluation.json
```

Evaluation writes a separate file and does not change prediction exports. The dashboard attaches a matching evaluation when displaying the run. IoU, precision, recall and F1 use shared valid pixels inside reviewed coverage; undefined precision/recall are null. Synthetic runs are rejected unless explicitly allowed for labeled sanity checks.

## Dashboard and deliverables

Set bounds and event date, run an analysis, inspect before/after radar, toggle layers, view road-access status and data provenance, and ask the copilot in either language. English PDF and printable bilingual HTML reports retain attribution and uncertainty. Exports include class masks, probability rasters, GeoJSON and audit JSON. The downhill tool performs D8 terrain screening, not hydraulic modeling.

The technical report is [six pages](docs/technical-report.pdf), with [editable source](docs/technical-report.md). Actual deliverables: [three-minute video](docs/demo-actual.mp4), [English report](docs/actual-situation-report-en.pdf) and [Nepali report](docs/actual-situation-report-ne.pdf). Details are in the verification record. The earlier [synthetic tour](docs/demo-synthetic.mp4) remains a labeled test artifact. The [public GitHub repository](https://github.com/vishal88736/mapping-flood-damage-from-space) contains source, reports and demos. Raw datasets, model artifacts, source caches and full local run exports are recreated with the commands above.

```bash
uv run pytest -q
uv run python scripts/analyze.py --mode demo
uv run python scripts/analyze.py --provider public --water-model trained
uv run python scripts/analyze.py --provider copernicus --water-model threshold
uv run python scripts/build_report.py
```

API documentation is at `/docs`; useful endpoints include `/api/data-sources`, `/api/water-model`, `/api/analyses/latest-real`, `POST /api/analyses`, per-run `/status`, `/copilot`, `/report`, `/trace` and `/files/<allowed-name>`. Runs persist; in-flight job state does not. Processing is limited to two concurrent runs, one degree per side and 2500 raster pixels per side. Antimeridian bounds and polar areas are unsupported. `FLOOD_LENS_PORT` and `FLOOD_LENS_DATA` override defaults. Bind to loopback for local use.

Code is MIT. Data retains its own licenses and required credits: [attribution](docs/ATTRIBUTION.md). This is an educational prototype requiring independent field verification.
