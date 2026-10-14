# Repair verification - 14 October 2026

This records executed software checks and a **cached real CDSE analysis**. It does not establish flood accuracy, actual damage/closures, native Nepali approval or successful live operation on an arbitrary new AOI/date.

## Implemented repairs

- Removed the trained TF-IDF/logistic-regression classifier and Scikit-learn dependency. Groq performs approved tool selection and structured fact-reference generation; deterministic bilingual routing/rendering is the labeled fallback. No model is trained by the application.
- Preserved run/subject/field binding, contextual clarification and unsupported-statistic refusal. Every factual chat line has a fact-ID citation. The dashboard opens the corresponding feature or area-wide evidence. Hospital/town questions use separate graph evidence rather than interchangeable destinations.
- Added independent water/debris availability, eligible coverage and missing reasons. An unavailable class has a null area. A finished job can have partial scientific coverage. Older results are normalized at read time without modifying original evidence.
- Replaced invalid-neighbor fill during radar median filtering with a mask-aware median requiring observed support. Building coverage is footprint-area weighted; hazard classes keep separate assessment masks.
- Road closure scenarios use continuous overlap of at least half a grid cell before graph subdivision. Water below bridges and surface changes over tunnels do not establish a failed crossing. Snapping is limited to 100 m; connectors and exterior routes remain unverified. Store distinct before/after destinations and same-target detours.
- Count unique historical OSM elements, with explicit inventory/physical-bridge limitations. Historical acquisition now includes `man_made=bridge` outlines as well as bridge-tagged ways.
- Persisted job progress and restart-interruption messages. Added a source-invalidated bounded result cache, compact metadata index, gzip responses, compact future result serialization and on-demand route detail. Added run-bound settlement CSV export with spreadsheet-formula escaping.
- Added validated real CDSE cache replay into a new UUID, using only acquired radar/optical, Copernicus DEM and historical OSM. Old predictions and EMS evaluation layers are excluded. Input hashes, original scene dates, source run and cached status are retained.
- Evaluation requires class-matched references and class eligibility, records input hashes and excluded reference-positive coverage, and reports both-empty or unobservable scores as N/A.

## Real input and output record

| Item | Recorded value |
| --- | --- |
| Original real acquisition run | `74c35c59-a417-4d63-aa1c-b30356311cc9` |
| Repaired cached run | `0fb99632-2943-4d03-9656-55f1db0682cd` |
| Acquisition mode | `cached_reprocessing`; no new download represented as live acquisition |
| AOI | WGS84 `[85.19, 27.90, 85.41, 28.30]`, Trishuli corridor |
| Requested event | 26 August 2026 |
| Radar acquisitions | 16 August 2026 12:21:41 UTC / 28 August 2026 12:21:41 UTC; Sentinel-1D |
| Historical OSM | 27 July 2026 08:59:58 UTC |
| Elevation | Actual Copernicus GLO-30 from its public mirror after the CDSE DEM request returned 403; source change retained |
| Grid | 30 m |
| Scientific status | Partial; water eligible 13.6%, debris eligible 0% |
| Water/debris result | 0.000 km² detected new water **within eligible observations** / debris unavailable |
| Mapped inventory | 49,468 buildings; 817 motorable road elements; 118 bridge elements; incomplete OSM inventory |
| Settlement access | 109 mapped settlements: 56 unassessed, 47 uncertain, 6 without a baseline mapped connection; 0 modeled cutoffs within the assumptions |

Zero mapped exposure does not establish no damage throughout the AOI. The original result and acquired source files were preserved. The new run records input SHA-256 values and source-result SHA-256 in `provenance.reprocessing`.

## Executed checks

| Check | Result |
| --- | --- |
| Complete regression suite | **217 passed**; existing Starlette transport and test Affine deprecation warnings |
| Current locked environment | `uv sync --frozen --offline --python 3.12` passed; removed unused classifier packages |
| Real cached input replay | Completed mapping, exposure, access and exports under a new run UUID |
| Internal bilingual/route review | 16/16 authored evidence cases; 109 settlement records reviewed; no reported consistency issues |
| Real API verification | 6/6 checks: English/Nepali authoritative values, compound question, casualty refusal, both one-page reports |
| Actual Groq responses | English and Nepali map questions both returned `mode=llm`; recorded 5.184 s / 6.244 s on the checked requests |
| Provider degradation | Actual rate limiting returned labeled deterministic fallback for compound/report requests; no invented replacement values |
| Real dashboard browser | No page errors; no overflow at 360, 768 and 1280 px; settlement details, hospital evidence and clickable Nepali fact citations passed |
| Complete browser workflow | No page errors; checked 360/768/1280/1440 px, before/after, layers, bilingual chat, compound/refusal cases, reports, reload preference and invalid-bounds recovery |
| PDF checks | English and Nepali each one A4 page; six-page technical report; Poppler visual inspection and page counts passed |
| Source checks | JavaScript/shell syntax and Git whitespace checks passed |

Real API records include per-request token usage when the provider supplied it. Actual observed quota failures are account-specific; no universal rate or price is claimed. The typed bilingual report remains deterministic even when conversational fact selection uses Groq.

The checked real dashboard payload was approximately 2.15 MB JSON / 371 kB gzip. A direct source-load check measured about 0.95 s cold and below 1 ms cached on this workstation. These are local observations, not a promised judge-run or slow-network budget.

Local detailed QA is under ignored `output/qa/`: `repair-real-api.json`, `repair-real-internal-review.json`, `repair-evaluation-integrity.json`, `repair-browser/checks.json` and `repair-full-browser/browser-checks.json`.

## Independent EMSR927 comparison

Six official vector products were downloaded to ignored `data/reference_only/EMSR927/`, separate from all inference inputs. Their observed-event polygons are labeled **Landslide**. The actual run has no eligible debris observations in reviewed coverage. IoU, precision, recall and F1 are therefore **N/A**, with zero evaluated debris pixels; water validation lacks a matching reference class.

Recomputing the comparison left `result.json`, `change-mask.tif` and `eligibility.tif` byte-for-byte unchanged. The QA record contains before/after SHA-256 values; evaluation metadata includes prediction, result, eligibility, reference and reviewed-coverage hashes. A class-mismatched score or an empty-mask score must not be presented as flood accuracy.

## Current deliverables and remaining limits

- [English one-page real-input report](../output/pdf/repair-real/situation-report-en.pdf), [Nepali report](../output/pdf/repair-real/situation-report-ne.pdf), alongside offline HTML, typed JSON and checksum manifest.
- [Six-page technical report](technical-report.pdf) and [editable source](technical-report.md).
- [Copilot configuration and contracts](COPILOT.md), [Nepali review handout](NEPALI_GLOSSARY.md), and [timed video script](demo-script.md).

Native-speaker Nepali sign-off, field validation and usable independent class-matched mapping accuracy remain outstanding. Current source coverage cannot be repaired into observations the satellites did not provide. Unknown-area acquisition still depends on compatible scenes, historical OSM, quotas and provider availability; the prototype retains its one-degree/2,500-pixel-per-side bounds. Complete OSM node identity/turn restrictions, exterior/cross-border routes, actual bridge failure and facility operation remain unverified. The user-recorded current three-minute video remains a separate submission task; archived segmentation videos are not current validation.
