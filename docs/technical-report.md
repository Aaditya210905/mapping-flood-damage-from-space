# Flood Lens: Mapping Flood Damage from Space

## 1. Objective and actual implementation

Flood Lens implements the supplied Track B brief as a local FastAPI service, Leaflet dashboard, raster/vector pipeline and bilingual situation-report copilot. An area and event date produce a potential water/debris map, infrastructure intersections and modeled road-access changes. English PDF and English/Nepali printable reports use saved map facts.

All listed data families are now used: Sentinel-1 and Sentinel-2, strictly historical OpenStreetMap, Copernicus DEM, both Kuro Siwo and Sen1Floods11 training datasets, and official EMSR927 products for checking only. Real acquisition, model fitting and a Nepal case ran here without Sentinel Hub credentials using public original-observation mirrors. The authenticated Copernicus provider remains available.

Training uses initial real subsets, not complete benchmark downloads: 72 Kuro Siwo samples and 96 Sen1Floods11 chips. A completed actual-data case loads by default in the development workspace. Synthetic mode remains an explicitly labeled test option. The public GitHub repository contains source and submission deliverables; original data and trained artifacts are recreated using the documented download and training commands.

The resulting baseline is not reliable operational damage assessment. It has poor held-out recall on Kuro Siwo, limited usable Himalayan coverage and weak overlap with official landslide mapping. These measured limits are visible in the dashboard and recorded in the model card and case study.

<!-- page -->

## 2. Actual sources and preprocessing

The PDF links identify the original sources. Kuro Siwo's author repository links the downloaded Hugging Face GRD archives; Sen1Floods11's repository links the original Google Cloud hand-labeled images, labels and split CSVs. Downloads retain checksums and publisher identities. Verified byte ranges retrieve complete samples from large Kuro Siwo archives; ignored labels and incomplete trailing records never enter training.

Original ESA Sentinel-1 GRD measurements and annotation are fetched from Microsoft's public mirror using range reads. Ready-made mirror RTC is excluded because its processing uses PlanetDEM. Local processing squares DN, subtracts annotation range/azimuth thermal noise when required, applies beta0 calibration and sarsen gamma-bilinear radiometric terrain correction with Copernicus GLO-30. DEM orthometric EGM2008 heights are converted to ellipsoid heights for sensor geometry. A complete shadow/layover mask is unavailable locally.

Radar pairing requires the same platform, orbit, direction, IW mode and VV polarization before and after the event. Event-day images are excluded because the brief gives no precise time. Sentinel-2 tiles from one acquisition are mosaicked; processing-baseline reflectance offsets and SCL masks are applied. Cloudy tiles are retained for pixel-level masking rather than discarding a whole acquisition.

Historical OSM comes from ohsome geometry or full-history geometry at a verified instant. Metadata limits the chosen timestamp to strictly before the event. Interval selection rejects simultaneous versions; post-event last edits are rejected. No current-OSM or generated-data fallback is used for a failed actual run.

<!-- page -->

## 3. Two-dataset AI and measured performance

A reproducible CPU ExtraTrees baseline is trained locally on 327,680 labeled pixels from 80 training chips: 48 Kuro Siwo and 32 Sen1Floods11. Features are VV dB, a 3x3 local median, the residual and 5x5 standard deviation. Feature context requires observed neighboring pixels; target slope eligibility is applied separately so steep observed neighbors are not treated as missing radar data.

Kuro Siwo linear backscatter is converted to dB. Labels 1 permanent water and 2 flood water map to water; label 0 maps to land and 3 is ignored, including when the separate validity mask is positive. Sen1Floods11 uses its supplied dB values and binary water labels; -1 is ignored. Checksums, aligned grids, label schema, duplicate labeled chip content and publisher split separation are checked. Kuro Siwo train/test events are also disjoint. Sen1Floods11 held-out chips can share events with training.

The fixed 0.5 probability threshold gives water IoU/recall of 0.111/0.115 on 24 Kuro Siwo test chips, 0.499/0.555 on 32 Sen1Floods11 validation chips, and 0.468/0.494 on 32 Sen1Floods11 test chips. These are initial-subset results, not full benchmark or unseen-Himalayan accuracy claims. Model/data checksums and per-chip evaluation are retained locally.

The model predicts observed water separately before and after the event. New water additionally needs a non-water baseline and more than 3 dB radar decrease. Sentinel-2 MNDWI can add clear observed new water. Vegetation loss with radar change supplies a low-confidence debris candidate. Small components, invalid observations and steep target slopes are excluded.

<!-- page -->

## 4. Exposure, access and grounded reports

Change polygons are intersected with historical OSM in a metric UTM grid. Building and bridge counts denote potentially exposed mapped features; road exposure is the intersecting geometry length, not the entire OSM way length. No intersection establishes structural failure.

Drivable ways form an undirected multigraph with shared geometry vertices. Crossing lines without a shared vertex do not create a junction. Settlements and destinations snap to segment interiors within one kilometre. The nearest baseline destination is selected by network distance. Exposed segments are removed under an explicit assumed-closure scenario. A settlement is newly cut off only if it had baseline connectivity and cannot reach any mapped destination afterward; alternate routes are tested. Unassessed and already disconnected settlements are reported separately.

The copilot uses a locally fitted bilingual character-TF-IDF/logistic-regression intent classifier. It selects a topic; deterministic templates fill numbers and names from statistics, layers and provenance. Evidence paths accompany responses. Unsupported casualty, missing-person and population questions return no estimate. It is not an open-ended language model. Nepali text requires native-speaker review.

Reports retain source dates, coverage, uncertainty, training citations and required attribution. Outputs include before/after radar previews, input crops, a classified GeoTIFF, two-band water probabilities, OSM/layer GeoJSON and audit JSON. The D8 downhill tool traces steepest descent and nearby settlements; it does not model flood depth, speed or arrival time.

<!-- page -->

## 5. Actual August 2026 Nepal case and checking

The selected corridor is 85.19-85.41 degrees east, 27.90-28.30 north, at 30 m resolution. Sentinel-1D orbit 85 acquisitions are 16 and 28 August, twelve days apart. Sentinel-2 mosaics use tiles 45RUM/45RUL on 12 and 27 August. The OSM snapshot is 27 July 2026 at 08:59:58 UTC. Exact IDs, URLs, checksums and parameters are in docs/data-status.json.

The final actual run detects 0.195 km2 potential new water and no retained debris candidates. One road/bridge feature intersects changes over 0.032 km; no building intersections or new modeled settlement cutoffs are detected. Seventeen settlements are unassessed and twelve were already disconnected in the baseline network. Only 13.6% of the requested AOI is usable after radar/terrain screening, while 31.1% has paired clear optical pixels. Empty outputs outside or even within this limited coverage cannot establish absence of damage.

Six official EMSR927 final vector products were downloaded into an evaluation-only folder. Initial observed-event polygons contain the Landslide class, not flood water. Reviewed coverage excludes official not-analysed polygons. Comparison uses only 1,093 shared valid pixels, or 0.9837 km2. Combined water/debris overlap has IoU 0.0149, F1 0.0294 and recall 0.0149; debris overlap is zero. This poor class-mismatched diagnostic is explicitly not water accuracy.

Reference loading is separate and read-only after prediction; published maps never enter acquisition or training. Geospatial, historical snapshot, calibration-unit, leakage, routing, copilot and API tests accompany browser/report checks recorded in docs/verification.md. These checks establish implementation behavior, not field accuracy.

<!-- page -->

## 6. Limitations, attribution and remaining validation

Sentinel revisit gaps cannot provide minute-ahead glacier-collapse warnings and can miss transient inundation. Mountain shadow, layover, incidence geometry, vegetation and urban scattering affect radar. A 20-degree slope screen excludes most of this corridor; no complete local occlusion mask is available. Optical clouds and the fourteen-day pre-event optical gap limit debris inference. Different training/live radiometry and sparse sample selection limit model generalization.

OSM can omit roads, buildings and facilities. Routing omits complete turn/access restrictions and full node identity, assumes reciprocal travel, and cannot assess alternatives outside the AOI. Facility presence does not establish operation; exposed roads are not confirmed closed. The service is bounded by tile size, provider availability and limited local concurrency. It is an educational prototype requiring independent Himalayan water labels, broader training, field verification and local language/transport review.

Contains modified Copernicus Sentinel data 2026.

Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

© OpenStreetMap contributors. OSM: ODbL. Official checking data: European Union, Copernicus Emergency Management Service data.

Training citations: Bountos et al. (2024), Kuro Siwo: 33 billion m2 under the water, NeurIPS 37, 38105-38121. Bonafilia, Tellman, Anderson and Issenberg (2020), Sen1Floods11, CVPR Workshops, 210-211. Kuro Siwo data: CC BY 4.0 per author distribution, repository code MIT. Sen1Floods11 retains original attribution; the original repository has no license file. Leaflet: BSD-2-Clause. Authored code: MIT. Direct original/provider links and license distinctions are in README.md and docs/ATTRIBUTION.md.
