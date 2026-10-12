# Actual-data EMSR927 case study

Completed actual run: `0f54ad10-431a-4d91-bff9-7d8164fc1039`. Event date: 26 August 2026. Requested bounds: 85.19°E, 27.90°N to 85.41°E, 28.30°N. Analysis resolution: 30 m, EPSG:32645. This selected corridor does not cover the entire disaster area.

## Observed inputs

- Sentinel-1D VV GRD, ascending relative orbit 85: 16 August 2026 at 12:21:53 UTC and 28 August at 12:21:54 UTC. Original measurement/annotation downloads; annotation thermal-noise removal, beta0 calibration and local gamma0 terrain correction with Copernicus DEM GLO-30.
- Sentinel-2C on 12 August and Sentinel-2B on 27 August, orbit 119, tiles 45RUM and 45RUL at each date. B03/B04/B08/B11 reflectance and scene classifications are downloaded, mosaicked and cloud-masked. The pre-event optical gap is 14 days and can include intervening changes.
- Copernicus DEM GLO-30 N27/E85 and N28/E85. EGM2008 heights are converted to ellipsoidal heights for sensor geometry; original heights support slope and D8 screening.
- Historical OSM geometry valid at 27 July 2026, 08:59:58 UTC, from the PDF's ohsome v1 full-history endpoint. The extractor selects the instant inside the validity interval; no post-event edits or current OSM substitutes are used.
- ExtraTrees water baseline trained on both listed datasets: 48 Kuro Siwo and 32 Sen1Floods11 training chips, 327,680 pixels. The initial subset has weak held-out recall; live gamma0 and training sigma0 processing differ.

Exact scene IDs, original URLs, processing, checksums and model metadata are in [data-status.json](data-status.json). Downloaded observations and prediction exports are under `data/runs/0f54ad10-431a-4d91-bff9-7d8164fc1039/`.

## Model outputs and coverage

| Output | Measured value |
| --- | --- |
| Potential new water | 0.195 km² |
| Debris candidates retained after size/AOI screening | 0.000 km² |
| Pre-existing water | 2.129 km² |
| Usable radar area | 130.261 km² / 13.6% of AOI |
| Paired clear optical coverage | 31.1% of AOI |
| Potentially exposed buildings | 0 of 49,468 mapped footprints |
| Potentially exposed roads / bridges | 1 road and 1 bridge feature |
| Intersecting road length | 0.032 km |
| Settlements newly disconnected in the modeled network | 0 of 109 |
| Unassessed / already baseline-disconnected settlements | 17 / 12 |

These are detection and network outputs, not confirmed damage counts. No detected building exposure or cutoff does not establish that buildings or access were unaffected. Mountain terrain screening excludes most of the AOI; the low-recall initial model can miss water, and a complete local shadow/layover mask is unavailable. Infrastructure and alternative routes outside observed coverage remain unknown.

## Official checking-only comparison

Six final official vector products were downloaded from the EMSR927 activation: initial products for AOI01 Syapru Besi, AOI02 Timure, AOI03 Bidur and AOI05 Phosretar, plus monitoring products for AOI03 and AOI05. The two cancelled products have no vector download. Initial observed-event polygons are used for comparison; monitoring dates are retained separately rather than pooled into a larger reference.

The 24 initial observed-event polygons have `obj_desc=Landslide`, `event_type=6-Mass Movement`. They are **not flood-water ground truth**. Reviewed coverage is the product extent minus officially not-analysed polygons. Rasterized shared valid coverage is only 1,093 pixels (0.9837 km²), 14.94% of the reviewed coverage inside the grid.

Combined water/debris overlap with these landslide polygons has IoU **0.0149**, F1 **0.0294**, recall **0.0149**, with 15 overlapping predicted pixels and 990 missed reference pixels. This is a poor, class-mismatched diagnostic and must not be presented as water accuracy. The separate debris-candidate overlap is zero. Different acquisition dates, spatial resolution and class definitions remain material limitations. It does not verify structural damage or actual closures.

The reference downloader and evaluator are separate from prediction and training. Comparison was written to a separate `evaluation.json`; prediction GeoTIFF, GeoJSON and source audit JSON were not modified. The API attaches the comparison for display and includes the credit: European Union, Copernicus Emergency Management Service data.

Independent Himalayan water labels, broader representative training, improved occlusion handling and local road/translation review remain necessary before claiming reliable generalization or real-world damage assessment.
