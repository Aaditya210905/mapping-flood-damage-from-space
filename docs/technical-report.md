# FloodLens AI: Situation-Report Copilot

## 1. Project objective and selected AI component

FloodLens AI is an educational flood-response prototype for Multimodal AI Hackathon 2026, Track B. The selected AI component is Option 2: Situation-Report Copilot. It answers questions about a selected map analysis and produces short English and Nepali briefings. Every measurement and critical factual field comes from stored Python analysis results.

The existing mapping services compare compatible pre-event and post-event Sentinel-1 radar observations, use clear Sentinel-2 observations when available, and process terrain using Copernicus DEM. They intersect historical OpenStreetMap buildings, roads and bridges with mapped change candidates and assess settlement connectivity in baseline and assumed-disrupted road networks.

This Copilot does not train a flood-segmentation model. Kuro Siwo and Sen1Floods11 are not used by the current application. The language model is a hosted Groq service; no model weights or satellite training datasets are downloaded. Historical reports about the removed segmentation approach are archived separately and do not substantiate the current system.

The dashboard is implemented with the existing FastAPI backend and Leaflet interface. Analyses persist as UUID-identified result files containing measured statistics, map layers, source metadata, uncertainty and attribution. The Copilot reads this existing storage without replacing the geospatial engine or introducing a new database.

Completion status: the Copilot code, reporting interface and reproducible export scripts are implemented. A user-supplied Groq key is configured privately and loaded by the local website; provider calls remain untested. The prepared report examples use an explicitly labeled synthetic run. The user will record the three-minute video. A real saved analysis is still required for real-event deliverables. This document does not claim a completed real Nepal case or operational accuracy.

<!-- page -->

## 2. Read-only tools and request architecture

The frontend sends the question, selected language, selected analysis UUID and bounded same-run conversation history to the existing Copilot route. FastAPI loads and validates the chosen result once. An EvidenceContext exposes only approved facts from that snapshot. Responses from another run or language are rejected before display.

Event summary returns the area, requested event date, processing status, measured statistics, observation dates and limitations. Flood and surface change returns recorded candidate areas, units, detection method and processing thresholds.

Infrastructure impacts returns saved buildings, roads or bridges, their actual identifiers, potential exposure status and relevant overlap or length measurements. Pagination and saved name/identifier search support focused questions without exposing arbitrary queries.

Settlement connectivity returns the modeled status, pre-event destination, baseline and disrupted-network distances, and supporting route road identifiers. Feature evidence retrieves an exact identifier and layer in the selected run. Unknown identifiers produce an explicit missing result.

Sources and limitations returns recorded satellite products and acquisition dates, historical OSM metadata, DEM identifiers when recorded, projection, processing methods, warnings and limitations. Signed asset URLs, private provider configuration and evaluation measurements are excluded.

Groq's native Python SDK supplies tool selection and structured claim generation. The default hosted model is openai/gpt-oss-120b. Tool retrieval and strict JSON-schema output use separate provider requests. Two retrieval rounds, a total of six tool calls, one final structured draft and one repair are bounded by a shared request budget.

There are no model tools for filesystem access, SQL, Python, shell execution, browsing, geospatial calculation or modifying results. Reference-only EMS products cannot become Copilot input features. The original deterministic classifier and authored bilingual templates remain available when Groq is disabled, unavailable or produces invalid claims.

<!-- page -->

## 3. Numerical grounding and factual interpretation

Each important field becomes a typed EvidenceFact. It records a fact identifier, analysis identifier, field, subject type and identifier, subject name, value, canonical unit, source identifiers, computation method, timestamp, availability and uncertainty. Fact identifiers are tied to the selected run and saved field path.

The model's claim schema contains references and approved formatting operations, never a numerical value or unrestricted factual sentence. Python verifies that the referenced fact was returned by an approved tool, belongs to the selected analysis and matches the claimed subject, field and interpretation. Topic validation prevents unrelated measurements from being placed under infrastructure, source or connectivity claims.

Python resolves authoritative values and applies fixed display precision or permitted rounding. Counts, dates and identifiers cannot be rounded. Arbitrary arithmetic, percentages derived from absent fields, unit conversions, invented acquisition dates, casualty estimates and population statistics are not supported model operations.

Missing and invalid values remain unavailable, rather than becoming zero. Unknown observations do not establish absence of flooding or damage. Reports with unavailable measurements are visibly marked incomplete; a missing analysis area or event date stops report generation with a controlled error.

Mapped intersections indicate potential infrastructure exposure, not confirmed structural destruction. Surface-change candidates may include non-disaster land-cover changes and are not confirmed debris flows. Settlement disconnection is a network scenario under assumed impassability, not a verified road closure or proof of physical inaccessibility.

Schema or semantic failures trigger one repair attempt, then an evidence-backed deterministic fallback. Raw provider errors, credentials and internal traces never reach the frontend. Compound questions can retrieve more than one tool while preserving each fact's association with its run and subject.

The validator constrains unsupported output forms; it cannot guarantee that stored metadata, map measurements or model-selected relevance are correct. Field verification, independent mapping evaluation and language review remain necessary.

<!-- page -->

## 4. English, Nepali and one-page situation reports

The explicit language selector controls the answer language. English and Nepali use the same evidence store, identifiers and canonical measurements. Groq interprets multilingual requests, selects relevant facts and chooses authored explanatory codes. Python renders bilingual labels, units and uncertainty from those references.

Names, product identifiers and location identifiers are preserved. Unavailable measurements remain unavailable after language changes. Potential exposure cannot become confirmed damage in translation, and modeled loss of access cannot become a verified real-world road closure.

The Copilot intentionally releases controlled bilingual narrative rather than unrestricted generated factual prose. This makes numerical and semantic validation precise, but limits stylistic flexibility and open-domain conversation. Native-speaker review of Nepali terminology is pending; the accompanying glossary provides the phrases used for that review.

A typed SituationReport contains its title, generation time, analysis/run identifier, area, requested event date, actual recorded acquisitions, flood and surface-change findings, potential asset exposure, modeled settlement connectivity, sources, uncertainty and required attribution.

Report generation reads the same authoritative snapshot as the conversation. Python inserts numerical values deterministically. Generated report snapshots are stored separately from original results and return HTML, PDF and JSON download links. A conversational briefing may use Groq, while the printable report retains its fixed evidence-backed sections.

PDF rendering uses WeasyPrint and Pango with bundled Noto Sans Devanagari fonts. Both language layouts target one readable A4 page. A report that cannot fit safely returns a controlled error with printable HTML available. Offline exported HTML embeds the fonts and remains readable without the running server.

The existing dashboard exposes loading indicators, actual response engine or deterministic fallback status, expandable evidence, selected-run ownership and report downloads. Synthetic examples retain prominent notices on every answer and report. Missing dates in synthetic or older saved analyses are never reconstructed from product names.

<!-- page -->

## 5. Setup, reproducibility and submission workflow

Install the locked Python 3.12 environment with uv sync --frozen --python 3.12 and start ./run.sh. The local interface opens at http://127.0.0.1:8000. The startup script loads the private .env file. It is excluded from Git and should be readable only by its owner.

Set LLM_ENABLED=true and add GROQ_API_KEY locally to enable the hosted provider. Optional configuration controls model name, SDK base URL, per-request timeout and overall request budget. Defaults use the native SDK root https://api.groq.com, a ten-second request timeout and a thirty-second Copilot budget. Empty or rejected credentials leave the fallback available.

A real situation report requires a real result produced by the existing mapping pipeline. Choose an area and past event date, acquire the allowed observations and historical map inputs, then select the completed run. The public satellite path needs no satellite account; the optional Copernicus Sentinel Hub path uses local OAuth configuration. Network and historical OSM availability remain external prerequisites.

Use scripts/export_copilot_reports.py with the selected run folder to export English and Nepali PDF, HTML and JSON reports, plus a source-checksum manifest. The exporter rejects synthetic runs unless --allow-demo is explicitly supplied. The optional --use-llm flag adds the configured provider's conversational briefing.

The user will record the three-minute walkthrough using the timed presenter script and judge Q&A notes in docs/demo-script.md. Show the selected analysis, English/Nepali questions, a compound question, evidence, an unsupported statistic, report generation, limits and attribution. An updated optional capture helper supports real recordings and explicitly labeled previews; it has not produced a completed video in this task.

Submission includes the repository and current README, this technical report, one-page situation reports and the three-minute video. Current preview artifacts demonstrate the interface and reporting workflow; they do not replace the required real case-study comparison or prove live provider behavior. No new application test suite was run during this submission-preparation task.

<!-- page -->

## 6. Limitations, responsibility and attribution

Satellite revisits occur over days. This system cannot warn of a sudden glacier collapse minutes ahead and may miss transient inundation. Terrain shadow, layover, vegetation, urban radar scattering, clouds and imperfect terrain correction affect mapped change. Eligibility masks leave unobserved areas unknown.

Historical OSM may omit roads, buildings, bridges, settlements or facilities. Modeled disruption assumes intersecting segments are impassable. Facility operating status, all turn restrictions, full node identity and alternative routes beyond the area remain uncertain. Neither an intersection nor a modeled route loss establishes physical damage or a verified closure.

The Copilot cannot establish deaths, injuries, missing people, affected population or operational rescue priorities from these map inputs. Its validator checks evidence association and allowed interpretations; it cannot guarantee physical truth, complete answer relevance or translation quality. Independent native Nepali review remains pending.

The service has bounded tile size and concurrency. Provider latency, rate limits, credential validity, historical OSM access and satellite availability can prevent an end-to-end run on a judge-selected area/date. Groq configuration is loaded, but no real saved analysis is available locally. Real-event exports need that analysis. Provider calls and application testing have been deferred at the user's request.

EMSR927 and other published damage maps are independent evaluation material only. They never supply input features, training labels, thresholds or Copilot measurements. The current preparation does not claim a Nepal comparison score or verified Himalayan accuracy. Existing historical case-study scores describe a removed implementation.

Contains modified Copernicus Sentinel data 2026.

Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

© OpenStreetMap contributors. OpenStreetMap retains ODbL obligations. Credit official evaluation products, when used, as European Union, Copernicus Emergency Management Service data. No satellite training dataset is used by this Copilot route. Bundled Noto fonts retain their OFL license; Leaflet retains its BSD-2-Clause license. Authored project code is MIT.

This is an educational prototype, not an operational warning or damage-confirmation service. No victim imagery is used in its reports or demonstrations.
