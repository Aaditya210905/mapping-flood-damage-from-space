# Situation-report Copilot

Track B Option 2 is the selected AI component. The existing satellite change detector, spatial intersections and directed road-network engine supply computed results. No flood-segmentation training dataset or model adapter is required. EMSR927 remains independent evaluation data, excluded from the Copilot tools.

## Architecture

`app/static/app.js` submits a question, explicit language, selected run UUID and at most six same-run conversation turns. `app/main.py` validates and loads that run once. `app/copilot_service.py` creates a read-only `EvidenceContext`, invokes Groq when configured, validates claims and returns rendered text, typed facts, citations, generation metadata and attribution. The frontend checks response/evidence ownership before displaying anything; changing analyses clears history.

`app/copilot_query.py` resolves explicit feature names/IDs and references against the selected run. Both engines receive the same resolved scope. A singular reference to several roads, or to an unidentified road, returns a bilingual clarification without guessing. Generic measurement follow-ups and translation requests reuse the previous question's scope; remembered values remain untrusted. The fallback uses deterministic bilingual topic rules without model training, retrieves multiple topics for compound questions, and looks up individual features. Rule-based routing has no statistical confidence score.

The provider is **Groq**, using its native Python SDK and Chat Completions API. The default is `openai/gpt-oss-120b`, hosted by Groq; no OpenAI account or xAI/Grok integration is used. Official references: [Python SDK](https://github.com/groq/groq-python), [local tools](https://console.groq.com/docs/tool-use/local-tool-calling), [structured outputs](https://console.groq.com/docs/structured-outputs).

Recognised scopes retrieve evidence in Python and normally need **one structured generation request**. Unresolved questions retain the bounded model-selected tool loop, followed by structured generation. Groq does not combine strict structured outputs with local tool use. Retrieval rounds, tool-call/page budgets and completion limits are configurable. All provider requests share a deadline. SDK automatic retries remain disabled; the application retries a short advertised `retry-after` once when it fits the configured wait/deadline. Longer delays are reported with the fallback. No arbitrary execution, filesystem or model-write tools are supplied.

Validated fully bound drafts for recognized scopes are reused in a bounded process-local cache. The key includes exact retrieved evidence/run, language, history, scope, question, prompt/schema and provider configuration. Reuse always retrieves and validates current evidence again; full lists remain current local queries. Concurrent duplicates share one generation, other provider calls obey a concurrency limit, and account rate-limit/credential failures apply a bounded cooldown. Cached answers expose `generation.cache_hit=true` and zero new provider requests/tokens. Cache state resets on restart and is not shared between server processes.

Name/ID matching uses a bounded cached alias trie, invalidated by changed identifiers/names. Radar coverage, optical coverage and water/debris eligibility are separate evidence fields. Mapped hospital inventory uses its own query, rather than settlement access. Mixed supported/unsupported questions retain the supported part and decline the unsupported part separately. Relevance validation requires each supported topic, scope and requested field.

Provider payloads omit repeated identities and duplicate facts. Request-local short aliases replace long fact IDs during generation; Python restores permanent run-bound identifiers in claims, narrative placeholders and citations before validation. Generated explanation codes must be empty; Python supplies mandatory topic uncertainty. This avoids repeatedly spending tokens on identifiers and prevents invented explanation enums. Full lists are paginated locally outside the model token/claim budget. `lists` contains all matching rows up to the configurable cap, totals, explicit truncation and a full-layer export. Provider quotas can still cause fallback; HTTP 413 is labeled `request_too_large`.

## Installation and configuration

Run `uv sync --frozen --python 3.12`, then `./run.sh`. Open http://127.0.0.1:8000. For a new private configuration copy `.env.example` to `.env` and edit locally. `./run.sh` loads it with uv; direct CLI commands use exported variables or `uv run --env-file .env`. Never put keys in chat, frontend code, fixtures or Git.

| Variable | Default / purpose |
| --- | --- |
| `LLM_ENABLED` | `false`; explicit enablement |
| `GROQ_API_KEY` | Private Groq key |
| `LLM_API_KEY` | Alternative key name, takes precedence |
| `LLM_MODEL` | `openai/gpt-oss-120b`; use a Groq model supporting strict structured output and local tools |
| `LLM_BASE_URL` | `https://api.groq.com`; SDK root without `/openai/v1` |
| `LLM_TIMEOUT_SECONDS` | `25`, allowed 1–30 seconds per request |
| `LLM_BUDGET_SECONDS` | `60`, allowed 3–60 seconds for the whole request |
| `LLM_MAX_COMPLETION_TOKENS` | `4096`, allowed 512–8192; account quotas still apply |
| `LLM_REASONING_EFFORT` | Unset in code; `.env.example` uses `low` for the default gpt-oss model. Omit/change it when another Groq model has different support |
| `LLM_APPLICATION_RETRIEVAL` | `true`; retrieve recognised scopes locally |
| `LLM_RETRIEVAL_ROUNDS`, `LLM_MAX_TOOL_CALLS` | `2`, `6`; bounded fallback tool selection |
| `LLM_RETRIEVAL_PAGE_SIZE` | `5`, allowed 1–5 |
| `LLM_MAX_RATE_LIMIT_RETRIES`, `LLM_MAX_RETRY_WAIT_SECONDS` | `1`, `3`; respect short provider retry delays within the deadline |
| `COPILOT_MAX_LIST_RECORDS` | `1000`, allowed 1–5000; larger lists carry explicit truncation/export |
| `LLM_RESPONSE_CACHE_TTL_SECONDS`, `LLM_RESPONSE_CACHE_ENTRIES` | `300`, `64`; validated-draft reuse; TTL `0` disables reuse |
| `LLM_MAX_CONCURRENT_REQUESTS`, `LLM_QUEUE_WAIT_SECONDS` | `2`, `2`; process-local provider admission and duplicate wait |
| `LLM_PROVIDER_COOLDOWN_SECONDS` | `30`; fallback cooldown when no retry delay is advertised; explicit provider delays are respected up to one hour |
| `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET` | Optional Copernicus Sentinel Hub OAuth |
| `OHSOME_API_KEY` | Optional historical ohsome v2 fallback |
| `FLOOD_LENS_DATA` | Existing run directory; defaults to `data/runs` |

Set `LLM_ENABLED=true` and `GROQ_API_KEY` locally, then restart. `/api/health` and `/api/copilot` expose configuration status without secrets. Configured status does not prove key validity, model availability or network access. No model weights are downloaded. The public satellite path still needs no Sentinel login; credentials alone do not create an actual analysis.

PDF rendering needs Pango. This workstation already has it. Debian/Ubuntu packages include `libpango-1.0-0` and `libpangoft2-1.0-0`; Arch uses `pango`. Follow [WeasyPrint setup](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) for other systems. Bundled Noto Sans Devanagari regular/bold fonts use the included OFL license. PDF resource access allows only those two font files, never arbitrary or remote resources.

## Approved backend tools

| Tool | Saved results returned |
| --- | --- |
| `event_summary` | Run, area/bounds, requested event, status, metrics, acquisition metadata and limitations |
| `flood_surface_change` | Potential water/surface-change areas, units, method and thresholds |
| `infrastructure_impacts` | Buildings/roads/bridges, actual IDs, exposure/observation status and overlap/length; searchable and paginated |
| `settlement_connectivity` | Modeled status, distinct before/after destinations, same-target detours and route road IDs; optional `destination_type=hospital|town|all` refuses unavailable legacy subtype evidence; searchable and paginated; optional exact `road_id` filters saved route dependencies |
| `sources_limitations` | Recorded products/dates, DEM tile IDs when recorded, historical OSM, CRS, methods, warnings and limitations |
| `feature_evidence` | One exact feature ID/layer in the selected run; unknown IDs return `not_found` |
| `mapped_inventory` | Count/list unique saved mapped elements, including hospital-only or town-only destination inventory; facility operation remains unverified |

Pydantic validates all inputs/results. Models cannot supply another run ID, executable code or a filesystem path argument. Tools never change results or recalculate geometry. Product IDs replace signed asset URLs. Corrupt/cross-run optional evaluation sidecars are excluded with a warning; evaluation measurements do not become Copilot inputs.

Road-filtered connectivity returns `filter_road_id`, `matching_settlements_count` before pagination, and `route_membership_unavailable_count`. A match means the recorded baseline or disrupted route uses that road; physical proximity and all possible routes are not assessed. Missing/invalid route evidence stays unknown. Missing settlement layers produce unavailable counts rather than zero. Whole-analysis connectivity counts cannot substitute for a road-filtered answer. The claim validator also rejects omitted requested measurements and evidence for unrelated named features or settlement filters.

## Grounding and bilingual responses

`EvidenceFact` contains a stable run/path-based ID, run, field, subject ID/name, value, canonical unit, source IDs, method, timestamp, uncertainty and status. Missing/invalid values remain `None` with a reason. Absence never becomes zero. New analyses preserve processed optical acquisition dates; older runs and synthetic optical fixtures may lack them and remain unavailable.

The provider selects exact returned fact IDs and allowlisted formatting operations. Python constructs complete internal `Claim` identities and validates them. Each generated narrative sentence contains `{fact:EXACT_FACT_ID}` placeholders and the same `fact_ids`. Placeholders expand complete labeled values, subjects and citations from the saved evidence; literal numbers, known feature names, markup and unsupported outcome claims are rejected. Every selected claim must appear in its narrative. Counts, dates, names and IDs cannot be rounded. Eligible measured units allow bounded rounding; raw values remain in typed facts. No arithmetic or conversion tools are exposed.

The validator rejects invented evidence, cross-run or wrong-location claims, unsupported interpretations/rounding, malformed schemas and extra prose asserting destruction or verified closures. One repair covers provider-rejected structured JSON, incomplete output or local grounding failures, within the same deadline. A second failure uses the deterministic answer. Casualty/population questions receive no estimate. Provider errors are sanitized; raw keys, messages and traces never reach the frontend. Backend schema diagnostics record value-free error locations only.

English and Nepali use the same facts. The model can write concise connective prose around validated placeholders instead of repeating fixed headings/sentences. Python still renders authoritative labels/values and required uncertainty in the selected language. Previous conversation is untrusted context; current facts must be retrieved again.

Generated wording is constrained to cited facts and connective prose; validation does not prove the meaning of every generated phrase or field accuracy. Input metadata/geospatial results can themselves be wrong. Native-speaker review has not occurred. Potential exposure is not confirmed damage; modeled disconnection is not a verified closure; mapped facilities are not proven operational. The deterministic fallback retains narrower language coverage than the configured LLM.

## Reports and API

`build_report_data()` creates a typed `SituationReport` from the selected snapshot. It revalidates and consumes generated narrative sections, rather than copying only generation metadata. Required measurements, dates, attribution and uncertainty remain present even if the narrative selects fewer claims. Long fact-ID citations remain in the typed report instead of filling the printable page. Missing measurements produce a visibly incomplete report. Missing area/event date produces a controlled error. PDFs must fit one readable A4 page, otherwise printable HTML remains available without clipping.

Existing request/response fields remain compatible. `draft` carries the fully bound validated sections; `lists` carries complete matching rows/totals/truncation/export links; `unsupported_parts` identifies separately declined clauses. Generation metadata includes request/token usage, rate-limit retries and advertised retry delay.

- `POST /api/analyses/{run_id}/copilot`: chat; optional bounded same-run `history`.
- `GET /api/analyses/{run_id}/feature-evidence?layer=settlements&feature_id=...`: typed evidence.
- `POST /api/analyses/{run_id}/report`: `{"language":"ne","use_llm":true}` returns snapshot plus HTML/PDF/JSON links.
- `GET /api/analyses/{run_id}/reports/{report_id}?format=html|pdf|json`: persisted snapshot download.
- Existing `GET /api/analyses/{run_id}/report?language=en|ne&format=html|pdf` remains supported; Nepali PDF now works.

Reports are separate UUID-named JSON snapshots under `data/runs/.reports`, excluded from Git and analysis inputs. They carry generation time and report/run IDs. Required Sentinel, WorldDEM and OSM attribution is preserved. The chat adds loading, actual engine/fallback status, expandable evidence and report downloads.

## Verification and troubleshooting

For prepared non-test submission materials, report exports and the three-minute recording, see [COPILOT_SUBMISSION.md](COPILOT_SUBMISSION.md). The report exporter accepts a saved run and does not train a model, fetch new observations or run tests. Synthetic reports and recordings require explicit flags and retain visible labels.

```bash
uv run pytest -q
node --check app/static/app.js
bash -n run.sh
uv run python scripts/check_dashboard.py --base-url http://127.0.0.1:8000
uv run python scripts/verify_copilot.py --require-llm
uv run python scripts/verify_copilot.py --require-llm --request-gap-seconds 45
uv run python scripts/verify_copilot.py --run-id RUN_ID --require-llm --checks reports --request-gap-seconds 45 --export-reports output/pdf/copilot-real
uv run python scripts/verify_copilot.py --run-id RUN_ID --allow-demo
```

Tests use labeled synthetic fixtures and mocked Groq responses/transports, without paid API calls or external network access. Real-run verification rejects synthetic data by default and requires actual LLM responses when `--require-llm` is supplied. Browser checks deliberately create synthetic inputs. None proves Nepal field accuracy.

Fallback reasons include `disabled`, `missing_credentials`, `invalid_configuration`, `timeout`, `rate_limited`, `request_too_large`, `credentials_rejected`, `provider_unavailable`, `provider_busy`, `request_rejected`, `structured_output_failed`, `invalid_tool_call`, `validation_failed` and `ambiguous_reference`. Clarification responses use `intent=clarification` with no facts. Restart after changing `.env`. Use a supported model/reasoning setting if a custom model rejects strict schemas. Missing evidence remains missing. Provider latency/rate limits, network access, terrain-processing cost, historical OSM availability, field validation and native-language review remain external constraints.

## Repair verification

See [generalization verification](GENERALIZATION.md), [audit repairs](AUDIT_REPAIRS.md) and [previous repair verification](REPAIR_VERIFICATION.md). Every factual statement/list row carries run-bound evidence. Availability uses positive eligible pixel counts rather than rounded display percentages. `app/mapping.toml` and `app/acquisition.toml` hold versioned rule/acquisition settings; `app/presets.json` supplies optional case inputs/cutoffs after explicit selection. These settings never supply event measurements. Scientific coverage remains partial when source observations are partial.
