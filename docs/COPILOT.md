# Situation-report Copilot

Track B Option 2 is the selected AI component. The existing satellite change detector, spatial intersections and directed road-network engine supply computed results. No flood-segmentation training dataset or model adapter is required. EMSR927 remains independent evaluation data, excluded from the Copilot tools.

## Architecture

`app/static/app.js` submits a question, explicit language, selected run UUID and at most six same-run conversation turns. `app/main.py` validates and loads that run once. `app/copilot_service.py` creates a read-only `EvidenceContext`, invokes Groq when configured, validates claims and returns rendered text, typed facts, citations, generation metadata and attribution. The frontend checks response/evidence ownership before displaying anything; changing analyses clears history.

The provider is **Groq**, using its native Python SDK and Chat Completions API. The default is `openai/gpt-oss-120b`, hosted by Groq; no OpenAI account or xAI/Grok integration is used. Official references: [Python SDK](https://github.com/groq/groq-python), [local tools](https://console.groq.com/docs/tool-use/local-tool-calling), [structured outputs](https://console.groq.com/docs/structured-outputs).

Groq does not combine strict structured outputs with local tool use, so retrieval and the final JSON-schema response are separate requests. The loop permits two retrieval rounds, six approved calls in total, one final response and one validation repair. All requests share a wall-clock budget; SDK automatic retries are disabled. Tool-selection free text is discarded. No built-in browser, code-execution, arbitrary filesystem, SQL, shell or model-write tools are supplied.

## Installation and configuration

Run `uv sync --frozen --python 3.12`, then `./run.sh`. Open http://127.0.0.1:8000. For a new private configuration copy `.env.example` to `.env` and edit locally. `./run.sh` loads it with uv; direct CLI commands use exported variables or `uv run --env-file .env`. Never put keys in chat, frontend code, fixtures or Git.

| Variable | Default / purpose |
| --- | --- |
| `LLM_ENABLED` | `false`; explicit enablement |
| `GROQ_API_KEY` | Private Groq key |
| `LLM_API_KEY` | Alternative key name, takes precedence |
| `LLM_MODEL` | `openai/gpt-oss-120b`; use a Groq model supporting strict structured output and local tools |
| `LLM_BASE_URL` | `https://api.groq.com`; SDK root without `/openai/v1` |
| `LLM_TIMEOUT_SECONDS` | `10`, allowed 1–30 seconds per request |
| `LLM_BUDGET_SECONDS` | `30`, allowed 3–60 seconds for the whole request |
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
| `settlement_connectivity` | Modeled status, pre-event destination, baseline/disrupted distances and route road IDs; searchable and paginated |
| `sources_limitations` | Recorded products/dates, DEM tile IDs when recorded, historical OSM, CRS, methods, warnings and limitations |
| `feature_evidence` | One exact feature ID/layer in the selected run; unknown IDs return `not_found` |

Pydantic validates all inputs/results. Models cannot supply another run ID, executable code or a filesystem path argument. Tools never change results or recalculate geometry. Product IDs replace signed asset URLs. Corrupt/cross-run optional evaluation sidecars are excluded with a warning; evaluation measurements do not become Copilot inputs.

## Grounding and bilingual responses

`EvidenceFact` contains a stable run/path-based ID, run, field, subject ID/name, value, canonical unit, source IDs, method, timestamp, uncertainty and status. Missing/invalid values remain `None` with a reason. Absence never becomes zero. New analyses preserve processed optical acquisition dates; older runs and synthetic optical fixtures may lack them and remain unavailable.

The LLM can emit only a claim's fact ID, run, subject type/ID, field, interpretation and allowlisted formatting operation. It cannot supply numerical values, dates, names, units, percentages or free factual prose. Python checks that the fact was returned by an approved tool, belongs to the selected run, matches its subject/field/interpretation and fits the section topic. Counts, dates, names and IDs cannot be rounded. Eligible measured units allow bounded rounding; `exact` uses canonical unit-specific display precision while raw values remain in typed facts. No arithmetic or conversion tools are exposed.

The validator rejects invented evidence, cross-run or wrong-location claims, unsupported interpretations/rounding, malformed schemas and extra prose asserting destruction or verified closures. One repair is attempted, then a safe deterministic answer replaces invalid output. Casualty/population questions receive no estimate. Provider errors are sanitized; raw keys, messages and traces never reach the frontend.

English and Nepali use the same facts. The model interprets multilingual questions, chooses tools/claims and selects authored explanation codes. Python renders labels, units and those codes in the explicitly selected language. Names, identifiers, measurements and uncertainty stay bound to evidence. Previous conversation is untrusted context; current facts must be retrieved again.

**Unrestricted generated prose and free translation are not released.** Narrative is controlled bilingual text. Fact selection/relevance can still be wrong or incomplete; input metadata/geospatial results can themselves be wrong. This validator does not establish field truth or make hallucinations impossible. Native-speaker review has not occurred. Potential exposure is not confirmed damage; modeled disconnection is not a verified closure; mapped facilities are not proven operational. The deterministic classifier has narrower language coverage than the configured LLM; the existing question chips provide supported fallback queries.

## Reports and API

`build_report_data()` creates a typed `SituationReport` from the selected snapshot. Python inserts dates, measurements, asset/access counts and product IDs. Sections and uncertainty use authored bilingual text. An LLM-assisted report request also creates a validated conversational briefing; printable report fields keep their fixed schema. Missing measurements produce a visibly incomplete report. Missing area/event date produces a controlled error. PDFs must fit one readable A4 page, otherwise printable HTML remains available without clipping.

Existing question/language requests and response fields (`answer`, `intent`, `intent_confidence`, `evidence`, `method`) remain compatible. Added fields include `run_id`, `language`, `facts`, `generation`, `missing_fact_ids`, `attributions` and `report_links`.

- `POST /api/analyses/{run_id}/copilot`: chat; optional bounded same-run `history`.
- `GET /api/analyses/{run_id}/feature-evidence?layer=settlements&feature_id=...`: typed evidence.
- `POST /api/analyses/{run_id}/report`: `{"language":"ne","use_llm":true}` returns snapshot plus HTML/PDF/JSON links.
- `GET /api/analyses/{run_id}/reports/{report_id}?format=html|pdf|json`: persisted snapshot download.
- Existing `GET /api/analyses/{run_id}/report?language=en|ne&format=html|pdf` remains supported; Nepali PDF now works.

Reports are separate UUID-named JSON snapshots under `data/runs/.reports`, excluded from Git and analysis inputs. They carry generation time and report/run IDs. Required Sentinel, WorldDEM and OSM attribution is preserved. The chat adds loading, actual engine/fallback status, expandable evidence and report downloads.

## Verification and troubleshooting

```bash
uv run pytest -q
node --check app/static/app.js
bash -n run.sh
uv run python scripts/check_dashboard.py --base-url http://127.0.0.1:8000
uv run python scripts/verify_copilot.py --require-llm
uv run python scripts/verify_copilot.py --run-id RUN_ID --allow-demo
```

Tests use labeled synthetic fixtures and mocked Groq responses/transports, without paid API calls or external network access. Real-run verification rejects synthetic data by default and requires actual LLM responses when `--require-llm` is supplied. Browser checks deliberately create synthetic inputs. None proves Nepal field accuracy.

Fallback reasons include `disabled`, `missing_credentials`, `invalid_configuration`, `timeout`, `rate_limited`, `credentials_rejected`, `provider_unavailable`, `invalid_tool_call` and `validation_failed`. Restart after changing `.env`. Use the default model if a custom model rejects strict schemas. For missing evidence regenerate a real analysis; never fill fields with example statistics. For PDF dependency/page-fit errors use HTML. Provider latency/rate limits, network access, terrain-processing cost, historical OSM availability, field validation and native-language review remain external constraints.
