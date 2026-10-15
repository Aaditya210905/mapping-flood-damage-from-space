# FloodLens AI: Situation-Report Copilot

## 1. Track B objective and implemented system

FloodLens is an educational prototype that maps candidate flood impacts, overlays pre-event infrastructure, tests road access and produces English and Nepali situation reports. The selected AI component is Track B Option 2: the situation-report copilot. The PDF offers this as an alternative to segmentation-model training. The current application uses no Kuro Siwo or Sen1Floods11 training data and downloads no model weights.

FastAPI serves a Leaflet dashboard and a Python geospatial pipeline. Compatible before/after Sentinel-1 VV/VH observations support water-change detection. Clear paired Sentinel-2 reflectance and scene classifications support water and low-confidence debris or land-cover-change candidates. Copernicus DEM supports terrain processing, slope screening and the optional downhill trace. Historical OpenStreetMap supplies buildings, roads, bridges, settlements and mapped hospital/town destinations.

New-water rules require a non-water baseline, low post-event backscatter, sufficient change, eligible terrain and a minimum connected component. Clear pre-event optical water is excluded from new radar-water candidates. Water and debris retain separate observation masks. Positive observed pixels remain assessable even when their displayed coverage rounds to zero. Missing observations never become zero impact.

Infrastructure exposure means intersection with mapped candidates, not confirmed destruction. A directed road graph compares baseline and assumed-disrupted access to mapped destinations. Before/after targets, route identifiers, uncertain connectors and unavailable destination-specific assessments remain explicit. The bonus DEM trace lists nearby settlements along a descending terrain path; it is not a hydraulic simulation.

Completed analyses retain original imagery dates, parameters, provenance, map layers, warnings and attribution in UUID-identified folders. The interface distinguishes fresh acquisitions, cached real replay and synthetic demonstrations. Published damage maps remain evaluation-only and never supply prediction features, thresholds or copilot measurements.

<!-- page -->

## 2. Copilot architecture and response repairs

The browser submits the selected analysis UUID, explicit language, question and at most six same-run conversation turns. A read-only EvidenceContext exposes seven approved tools: event_summary, flood_surface_change, infrastructure_impacts, settlement_connectivity, feature_evidence, sources_limitations and mapped_inventory. No filesystem, shell, arbitrary code, browsing or model-write tool is exposed.

Recognised questions resolve scope and retrieve evidence in Python, normally requiring one Groq structured-generation request. Unresolved questions retain bounded model-selected retrieval. All provider requests share a deadline. SDK automatic retries are disabled; short advertised rate-limit delays may be retried within configured limits. One fresh repair is allowed for rejected JSON or grounding failures, followed by a labeled deterministic bilingual fallback if needed.

The default provider is Groq's native Python SDK with hosted openai/gpt-oss-120b. The supplied private configuration uses low reasoning effort. Request-local short references reduce repeated citation identifiers in model input/output; Python restores permanent run-bound identifiers before validation, rendering or export. This reduces report size. Mandatory uncertainty codes are supplied by Python, removing the observed model-invented explanation-code failure.

Generated English/Nepali connective wording is consumed by chat and printable reports. Previously the application called the provider but rendered fixed prose. The repaired report builder revalidates generated sections, preserves required measurements and compacts repeated labels and warnings so a one-page export remains readable.

Routing distinguishes radar coverage from class eligibility, hospital inventory from hospital access, and threshold questions from general extent. Relevance validation requires every supported topic, requested field and feature scope. Mixed casualty/map questions retain supported map answers and separately decline unsupported estimates.

Matching lists are paginated locally outside the model token budget. The UI shows totals, explicit record-cap truncation, full-layer exports and map links. A bounded source-invalidated alias trie replaces per-feature regular-expression scans. All 109 current-case settlements can be listed instead of silently returning only a few.

<!-- page -->

## 3. Numerical grounding and shared configuration

Each EvidenceFact records a stable identifier, run, field/path, subject identifier, canonical value/unit, source IDs, computation method, timestamp, availability and uncertainty. The provider selects returned references and approved formatting operations. Python binds complete identities. An unknown reference or an unrelated feature cannot become evidence.

Generated sentences contain validated fact placeholders. Python inserts full labeled values and citations; the model cannot supply literal measurements, dates, counts or place names as factual prose. Declared references must match placeholders and selected claims. Counts, dates and identifiers cannot be rounded. Permitted measured units support bounded rounding; raw values remain in JSON. Unsupported casualty, population and rescue-priority estimates receive no invented value.

Validation covers schema, run/subject/field association, topic relevance, interpretation and returned-evidence membership. It rejects markup and prohibited literal outcomes. It cannot prove every connective phrase, input correctness, physical mapping accuracy or translation quality. Authored uncertainty remains mandatory regardless of generated wording.

Versioned app/mapping.toml supplies processing defaults, including radar/optical thresholds and road snap/overlap settings. Selected configuration and per-run parameters are recorded in provenance. app/presets.py holds the Nepal bounds/date and OSM cutoff. Unrelated areas sharing that date do not inherit the cutoff. Dashboard and CLI consume shared defaults; slider limits come from the request schema. Custom requests no longer inherit the Nepal area name.

Road disruption is a sensitivity scenario. Candidate overlap removes eligible unbridged segments; water below bridges does not prove deck failure. Hospital/town access is computed separately. Snaps, exterior routes and facility operation remain unverified. Incomplete OSM topology can leave settlements uncertain or unassessed; these states remain distinct from modeled new loss of access.

Fixed legal credits, bilingual labels, supported languages, schemas, algorithm definitions and explicitly synthetic fixtures are intentional authored content. They do not supply event measurements. The hardcoding audit identifies the repaired paths and explains this distinction.

<!-- page -->

## 4. Bilingual reports and executed verification

English and Nepali share facts, canonical values and permanent citations. Names and product identifiers retain source spelling. The explicit selector controls language. Missing fields stay unavailable across translation. Potential exposure cannot become confirmed destruction, and modeled access loss cannot become verified physical closure.

A typed SituationReport carries report/run identifiers, generation time, area/event, actual observation metadata, flood findings, exposure, access, sources, missing fields, generation metadata and attribution. Separate report snapshots provide HTML, PDF and JSON without changing the analysis. Offline HTML embeds bundled Noto Devanagari fonts. WeasyPrint/Pango renders readable A4 reports; oversized content returns a controlled error with HTML available.

Real Groq generation succeeded for English, Nepali and compound questions. After the output-contract repair, both language reports completed in one provider request and rendered one page. The recorded report-generation checks took approximately 2.7 and 3.6 seconds. These are account/workstation observations, not guaranteed latency. Quotas or provider failures can still trigger the labeled fallback.

The full regression suite passes 258 tests. It covers optical baseline-water exclusion, tiny positive coverage, VV/VH selection, scoped OSM policy, relevance, hospital inventory, complete lists, alias binding, forged narrative rejection, bounded provider repair, rate limits, report snapshot validation, class-aware evaluation and bilingual PDF output. Unit tests use clearly synthetic fixtures and mocked HTTP/SDK responses. Live records and current checks are in docs/AUDIT_REPAIRS.md.

Browser checks on the real saved run passed at 360, 768 and 1280 pixels without overflow or page errors. They exercised settlement/hospital details, Nepali citations, repaired questions, complete lists and map inspection. Browser chat used local deterministic replay to avoid duplicate provider calls; actual Groq generation was verified separately. A new captioned three-minute video records genuine Groq chat/report responses and the repaired interface.

Native-speaker Nepali terminology approval remains pending. The glossary identifies review priorities. Software consistency, fonts and visual checks do not establish external language approval or field accuracy.

<!-- page -->

## 5. Trishuli case study and EMSR927 comparison

Current cached replay 0717d3b1-ac72-48d2-a8bb-8ecb93e9663c derives from acquired CDSE run 74c35c59-a417-4d63-aa1c-b30356311cc9. It retains the 26 August 2026 event and bounds 85.19-85.41 east, 27.90-28.30 north. Sentinel-1D observations are from 16 and 28 August with compatible geometry. Historical OSM is valid at 27 July 2026, 08:59:58 UTC. Source/result hashes are retained; replay uses acquired inputs rather than old predictions.

Radar coverage is 97.6%, while water-eligible coverage is 13.639%. Mapped new water is 0.000 square kilometres within eligible observations; pre-existing water is 0.701 square kilometres. Paired optical support leaves debris unavailable. These are limited-observation findings, not evidence that flooding or damage was absent throughout the corridor.

Inventory contains 49,468 building elements, 817 roads, 118 bridge elements, 109 settlements and nine mapped hospital destinations. These are OSM elements, not a complete physical inventory or verified operating facilities. Access results record 56 unassessed settlements, 47 uncertain and six without a baseline mapped connection. They do not establish physical new cutoffs or current rescue access.

The independent EMSR927 comparator uses initial products, reviewed coverage and matching class eligibility. The supplied reference class is Landslide. Inside the grid are 7,316 reviewed pixels and 2,752 reference-positive reviewed pixels, but no eligible debris pixels to compare. IoU, precision, recall and F1 are N/A. No matching water-reference class is present; this cannot validate water mapping.

Official asset grading was checked separately. Inside the AOI are 864 reference building points, 44 road geometries and six bridge points; nine additional road references lack geometry. None has sufficient joint water/debris observation coverage for binary exposure comparison. Asset agreement scores are N/A. The repaired evaluator uses footprint/length coverage and preserves prediction hashes; unavailable debris cannot support a negative-exposure score.

A separate fresh small-area run, 25236045-4d1e-4fd5-baac-26918b4224e7, completed from CDSE imagery, public Copernicus DEM and pre-event OSM after the repairs. Bounds were 85.28,28.00,85.32,28.04 at 30 metres. It generated maps, access results and reports without replaying predictions. Coverage remains partial: 12.942% water eligibility, unavailable debris, and all 11 settlements unassessed with no destination inside this small tile. This verifies this selected raw-input path, not every possible judge area/date.

<!-- page -->

## 6. Reproduction, limitations and attribution

Install the locked Python 3.12 environment with uv sync --frozen --python 3.12, then start ./run.sh and open http://127.0.0.1:8000. A fresh checkout has no acquired runs. Choose an area and past event date for actual acquisition, or explicitly select synthetic mode. Acquisition errors never substitute synthetic data.

Private .env configuration supplies optional Groq/CDSE credentials; .env.example contains placeholders. Provider defaults are 25 seconds per request and 60 seconds overall. The supplied Groq configuration uses low reasoning effort. Public satellite access needs no Sentinel account; historical OSM, network availability and provider quotas remain prerequisites. README and docs/COPILOT.md give commands.

scripts/export_copilot_reports.py exports bilingual PDF/HTML/JSON with source/output checksums. Its verified-API option reuses checked snapshots without more provider calls and rejects changed source facts. scripts/build_report.py builds this six-page report. scripts/record_demo.py uses an isolated browser context and preserves actual data/engine labels. The exactly three-minute captioned MP4 has a checksum manifest. The previous user video is preserved separately.

Satellite revisit gaps prevent minute-ahead warning of glacier collapse and can miss transient inundation. Shadow, layover, vegetation, clouds and imperfect terrain processing limit observations. OSM omissions, turn restrictions, original node identity, facility operation and exterior routes remain uncertain. Intersections do not prove structural damage. Tile size/concurrency and compatible-scene availability bound the prototype. New judge-selected runs still depend on external sources. Independent mapping accuracy, field checks and native Nepali review remain outstanding.

The required credits follow. No training dataset is used by this copilot route; archived segmentation experiments retain their own citations and do not validate this implementation. EMS products are evaluation-only. No victim imagery is used.

Contains modified Copernicus Sentinel data 2026.

Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

© OpenStreetMap contributors. OpenStreetMap retains ODbL obligations. Evaluation reference credit: European Union, Copernicus Emergency Management Service data. Noto fonts use OFL; Leaflet uses BSD-2-Clause; authored project code uses MIT. This is an educational prototype requiring independent validation.
