# Actual-data verification

> Historical artifact from the removed segmentation implementation. Its counts, scores and reproduction instructions do not validate the current copilot route. See [current scope](COPILOT.md) and regenerate submission results.

Verified locally on 12 October 2026. Final actual run: `0f54ad10-431a-4d91-bff9-7d8164fc1039`.

| Check | Result |
| --- | --- |
| Actual training downloads | 72 Kuro Siwo samples and 96 Sen1Floods11 chips, author/original URLs and checksums retained |
| Training / held-out separation | Publisher splits preserved; actual content and Kuro Siwo event leakage checks passed |
| Actual model fitting | Both datasets contribute 327,680 labeled pixels from 80 training chips |
| Held-out evaluation | 88 chips evaluated; scores/counts and substantial recall limits in model card |
| Actual Sentinel-1 processing | Original 16/28 August Sentinel-1D VV GRD locally denoised, calibrated and terrain-corrected using Copernicus DEM |
| Actual Sentinel-2 | Four L2A tile crops, two acquisition mosaics, reflectance offsets and SCL masking |
| Actual historical OSM | 52,721 full-history geometries selected at 27 July 2026 08:59:58 UTC, strictly before the event |
| Actual terrain | Copernicus GLO-30 tiles and EGM2008 vertical conversion; original DEM retained |
| Official evaluation | Six EMSR927 products, 24 initial observed-event polygons, separate read-only diagnostic; Landslide class mismatch disclosed |
| Automated suite | 42 tests passed, including calibration power units, ignored labels, split leakage, historical intervals, terrain context and audit-preserving dashboard projection |
| JavaScript / Python | JavaScript syntax check and Python compilation passed |
| Responsive browser | 360, 768, 1280 and 1440 px; no horizontal overflow or JavaScript errors in checked workflows |
| Actual dashboard | Latest actual case loads; all source roles, downloads and measured model scores displayed |
| Map / copilot | Before/after and road/method views; English/Nepali map-derived answers exercised |
| Reports | English generated PDF and Nepali Chromium-printed PDF each one A4 page; technical report six A4 pages |
| Actual demo | Captioned H.264, 1280×900, exactly 180 seconds |
| Authenticated alternative provider | Request formation tested; not executed against a credentialed Sentinel Hub account |

Deliverables: [actual demo](demo-actual.mp4), [English report](actual-situation-report-en.pdf), [Nepali report](actual-situation-report-ne.pdf), [six-page technical report](technical-report.pdf), [data inventory](data-status.json), [model card](water-model-card.json) and [machine-readable verification](verification.json). Local detailed captures are in ignored `docs/qa/`.

The dashboard requests a compact view containing the exposed building footprints it displays. Complete 49,468-building data remains in run exports and audit JSON; counts and source data are preserved. This avoids transmitting unused full geometry for every dashboard reload.

These checks establish successful actual-source processing and implementation behavior. They do not establish full benchmark performance, independent Himalayan water accuracy, structural damage or actual road closures. Radar/terrain usable coverage is only 13.6%; paired clear optical coverage is 31.1%. The initial model's Kuro Siwo test recall is 0.115. The official overlap diagnostic has IoU 0.0149 against landslide polygons on only 1,093 shared valid pixels and must not be called flood-water accuracy.

The earlier synthetic video and sample reports remain explicitly labeled test artifacts. Public source and deliverables are at https://github.com/vishal88736/mapping-flood-damage-from-space. Raw datasets, trained models, source caches and full local run exports are excluded from Git; the README documents their reproduction. Independent field validation and native-speaker Nepali review have not been performed. The test runner reports one Starlette/httpx transport deprecation warning.
