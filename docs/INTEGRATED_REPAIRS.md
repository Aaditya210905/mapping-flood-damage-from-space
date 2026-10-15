# Integrated repair and verification

Completed 15 October 2026. This pass addresses the shared causes behind the remaining audit findings, including query interpretation, evidence retrieval, lists, source selection and configuration. Earlier real acquisitions and submission artifacts retain their original snapshots.

| Reproduced problem or fixed assumption | Current behavior and location |
| --- | --- |
| “Not exposed” returned exposed roads | Explicit exposure selectors and shared predicates in `copilot_query.py`, `query_filters.py` and `evidence.py`. A negative match requires saved observations supporting absence within the available hazard scope. Incomplete/unknown observations remain a separate count |
| “Not connected” selected connected villages | Negative access selects known modeled disconnection, including baseline disconnection; uncertain and unassessed access are excluded and counted separately |
| “Already disconnected before the flood” selected new loss of access | Baseline and disrupted-network predicates are distinct. A temporal flood modifier does not request an unrelated inundation-area answer |
| “Either a hospital or a town” selected hospitals only | Uses the saved combined network, regardless of destination order. Requiring both targets or different conditions per target receives an explicit scope clarification instead of silently returning an either-target result |
| Chat, lists and native drafts could use different scopes | One query plan builds arguments for local retrieval, fallback and complete pagination. Native drafts must cite the exact requested filter/count facts; true but unrelated aggregate/feature facts are rejected |
| Missing destination-specific results appeared as zero in lists | Missing analysis layers or absent legacy per-type graph results produce unavailable counts in both answer and list. Uncertain routes stay visible in assessment counters |
| Compound questions lost clauses or later topics | Separate asset/access predicates, multiple inventory layers and accompanying water measurements are preserved. Fallback reserves claim space for every topic, prioritizes scope/count facts over optional feature detail and undergoes semantic relevance validation |
| Median size/support and near-road tolerance were inline | `mapping.toml` version `rule-based-v5` supplies the odd median window, valid minimum support and on-network tolerance. Validation rejects incoherent combinations. Per-run filter provenance records actual selected values |
| Source extraction, routing and Copilot assumed only hospital/town categories | Configured settlement places and labeled destination profiles drive OSM selectors, per-type routes, inventory, keyword recognition, evidence labels and dashboard detail. A tested clinic profile stays a clinic, rather than being relabeled a hospital |
| Wider profiles could reuse narrower acquisitions | Historical extracts record their selection policy. Public cache identity includes the actual OSM filter. CDSE cached-input replay rejects a broader requested asset selection and preserves original files |
| OAuth/download and worker/cache policy remained inline | `acquisition.toml` version `acquisition-v4` supplies OAuth and reference-download timeouts. `runtime.toml` supplies bounded analysis workers and token/feature/result/provider-account cache sizes |

## Configuration

Use the current complete TOML files as the basis for local overrides, then restart the application. New required fields are validated at startup. `FLOOD_LENS_MAPPING_CONFIG`, `FLOOD_LENS_ACQUISITION_CONFIG`, `FLOOD_LENS_EVALUATION_CONFIG` and `FLOOD_LENS_RUNTIME_CONFIG` select replacement files; `FLOOD_LENS_PRESETS_FILE` selects optional input presets. Credentials remain in the private environment.

Destination-profile tag entries are OR selectors. The default profiles retain `amenity=hospital` and `place=town/city`. A custom clinic profile can declare its own `amenity=clinic` selector, English/Nepali labels and keywords. Broader profiles require matching fresh historical OSM acquisition. Changing labels, processing or operational timeouts does not create new observations.

Existing v1-v4 extracts have an explicit historical compatibility definition for their original settlement/hospital/town selection. That definition does not change when current configuration changes. Existing runs keep saved profiles where available; new profiles are never retroactively treated as observed in legacy results.

Algorithm definitions, units, sensor encodings, protocol/status contracts, authored translations, uncertainty text and validated resource bounds remain explicit code/configuration. They are not authored event measurements. Regional calibration requires independent observations; moving a threshold to a file does not establish universal accuracy.

## Verification

- The complete regression suite passes, including new English/Nepali negation, baseline, either-target, mixed-predicate, inventory, missingness, wrong-scope native-draft, claim-budget and processing-profile cases. See the current test result below.
- Actual cached CDSE inputs were replayed from `4e56ebc5-c117-43d3-9075-d7df8b418e38` into **`c2f74c94-af7f-426a-bc02-e66b5c0f42aa`**. Input/source-result hashes remained unchanged. The new run retains **527 mapped roads**. This replay performed no new satellite or OSM acquisition.
- A separate fresh small-area historical OSM read verified the generated filter against the actual configured ohsome v1 service. It returned **25 real features**, including the requested hospital, at pre-event snapshot `2024-08-20T23:59:57Z`, with the selected profile recorded in metadata. The upstream request was slow but completed successfully. This check did not make any satellite processing request or replace the replay inputs.
- On those saved real observations, the repaired negative-exposure query found **3** road elements with supporting complete observations, **522** with incomplete/unavailable assessment, and **2** potentially exposed. These categories account for the 527 saved roads; partial observation was not converted to safety.
- The hospital negative-access query found **0 known modeled disconnections** and **1 uncertain/unassessed access result**. It does not establish that the settlement can reach an operating hospital.
- Live Groq API answers passed in English and Nepali. The final fresh English paraphrase used **2 requests**, including the allowed validation repair; Nepali used **1 request**. Immediate identical repeats used **0 provider requests and 0 tokens**, with identical facts and answer text.
- English/Nepali situation-report JSON and HTML were fetched through the application API. Both matched saved water/debris/exposure/uncertainty metrics. These final report checks used deterministic report generation; native chat checks are recorded separately.
- The saved-real dashboard passed at **360/768/1280 px**, with **no page errors**, facility/route detail, bilingual citations, complete lists and list-to-map links. Browser chat used local deterministic replay and made no provider or acquisition calls.
- `node --check app/static/app.js` and `git diff --check` pass. QA evidence is in `output/qa/integrated-repair-real-verification.json` and `output/qa/integrated-repair-browser/checks.json`.

Current full-suite result: **347 tests passed**, with the two existing dependency deprecation warnings. This adds regression coverage to the prior 311-test baseline.

## Remaining external validation

Native-speaker Nepali review and suitable independent class-matched reference/field validation are still required. Neither software tests nor a successful live Groq response establishes mapping accuracy, actual damage, road closures or facility operation. Different requested areas/dates still require available paired scenes, historical OSM and provider quota. The [PDF requirement checklist](TRACK_B_CHECKLIST.md) records these submission/validation obligations. This pass leaves repair changes local and does not publish a repository or rewrite the older submission PDFs/video.
