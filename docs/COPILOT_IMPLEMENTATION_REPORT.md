# FloodLens Copilot implementation report

Completed implementation and software checks on 14 October 2026. The provider is **Groq**, as requested. Live Groq and real satellite-data validation remain pending credentials and a real stored analysis. No segmentation models were trained, training datasets downloaded or geospatial algorithms replaced.

## 1. Implemented functionality

Added a native Groq SDK integration, environment configuration, bounded retrieval/claim generation, six typed read-only evidence tools, semantic claim validation, explicit missing-data handling, compound questions and labeled deterministic fallback. Added same-run conversational history, supporting details in the existing dashboard, report generation/download controls, typed report snapshots and shaped English/Nepali one-page PDFs. Fixed map fitting when a new result is rendered in a hidden tab, with a browser regression check. Existing API request fields, response fields, report routes and original tests remain intact.

## 2. Actual architecture

Question + selected language/run → existing dashboard → FastAPI → one validated saved-run snapshot → typed evidence tools → Groq tool selection → structured fact-ID claims → Python semantic validation → deterministic numerical/unit and bilingual narrative rendering → response/evidence/fallback status → dashboard.

Mapping statistics still come from the existing Python raster, OSM intersection and road-graph services. The LLM cannot execute code, read arbitrary paths, choose another run, calculate measurements or modify results. Report snapshots are derived outputs stored separately from the immutable original analysis.

## 3. Files created and changed

| Area | Files |
| --- | --- |
| New backend modules | `app/llm.py`, `app/copilot_types.py`, `app/evidence.py`, `app/grounding.py`, `app/copilot_service.py` |
| Existing backend integration | `app/copilot.py`, `app/main.py`, `app/models.py`, `app/report.py` |
| Acquisition metadata only | `app/providers.py`, `app/public_data.py`, `app/pipeline.py`: retain processed optical dates without changing selection/processing algorithms |
| Existing frontend | `app/static/app.js`, `app/static/index.html`, `app/static/styles.css` |
| PDF fonts | `app/static/fonts/NotoSansDevanagari-Regular.ttf`, `NotoSansDevanagari-Bold.ttf`, `OFL.txt` |
| New tests | `tests/conftest.py`, `test_llm_config.py`, `test_evidence_grounding.py`, `test_copilot_llm.py`, `test_copilot_reports_api.py` |
| Verification | New `scripts/verify_copilot.py`; expanded `scripts/check_dashboard.py`; `scripts/review_outputs.py` resolves indexed evidence and verifies explicit missing facts |
| Configuration | `.env.example`, `.gitignore`, `pyproject.toml`, `uv.lock`, `run.sh` |
| Documentation | `README.md`, `docs/COPILOT.md`, `docs/COPILOT_IMPLEMENTATION_PLAN.md`, this report |

## 4. Provider configuration

Installed Groq SDK 1.7.0 and WeasyPrint 69.0 through the existing Python 3.12 environment and lockfile. Default hosted model: `openai/gpt-oss-120b` on Groq. Set `LLM_ENABLED=true` and `GROQ_API_KEY` in a private `.env`, then restart `./run.sh`. Optional `LLM_API_KEY` overrides the key; model/base URL/timeout/budget are configurable. The native SDK base URL is `https://api.groq.com`, without the `/openai/v1` suffix. No key was created, copied or committed.

Official [Groq structured-output documentation](https://console.groq.com/docs/structured-outputs) requires separate tool and strict-output requests. The implementation uses two retrieval rounds, at most six tool calls, one structured final draft and one repair, within a shared default 30-second budget. Timeout, rate-limit, credential, server, schema and validation failures return sanitized fallback responses.

## 5. Available tools

`event_summary`, `flood_surface_change`, `infrastructure_impacts`, `settlement_connectivity`, `sources_limitations`, `feature_evidence`. Infrastructure and settlement queries support pagination and saved name/ID search. Facts preserve IDs, units, dates, methods and uncertainty. Unknown features return a safe missing result. Unknown/mismatched runs are rejected. Reference evaluation fields are excluded from the evidence tools.

## 6. Numerical and factual grounding

The model cannot submit numbers or free factual sentences. Each claim references an allowlisted retrieved fact ID and must match its run, subject ID/type, field and interpretation. Python resolves the authoritative value and formats it using fixed unit precision or permitted rounding. Counts/dates/identifiers cannot be rounded; arbitrary arithmetic/conversions are unavailable. Missing/invalid values remain unavailable, never zero.

Unsupported values/dates, wrong locations, foreign runs, invented evidence, casualties/population, extra destruction/closure prose and malformed output are rejected in tests. A single repair may succeed; otherwise the original deterministic classifier/templates or typed safe rendering take over. Validation prevents these output forms but cannot guarantee source accuracy, completeness, relevance or field truth.

## 7. English and Nepali

The explicit language selector controls output. Groq selects claims and authored explanation codes; Python renders bilingual labels, units and uncertainty from the same facts. Names and identifiers are preserved. Tests verify identical underlying values/IDs and unavailable facts across languages. Sixteen held-out bilingual questions passed internal checks. Native-speaker review has **not** been performed. The fallback classifier has narrower language coverage than the configured LLM; unsupported phrasing may need rewording or the existing question chips.

## 8. Reports

Reports contain title/time, run/area/event, radar and available optical acquisition metadata, flood/surface-change measurements, potential asset exposure, modeled settlement access, sources/evidence, limitations and required attribution. New POST generation stores a typed snapshot and returns HTML/PDF/JSON links. Existing GET routes work for both languages. Bundled Devanagari fonts fix Nepali shaping; English and Nepali PDFs were rendered, visually inspected and verified as one page with readable attribution and no clipping.

Synthetic fixtures do not contain actual optical acquisition dates. Their report is explicitly partial, with that field unavailable; no date was inferred from a product name. Critical missing area/event data produces a controlled error. Reports exceeding a readable one page return a controlled HTML alternative rather than clipping text.

## 9. Executed checks

| Command/check | Actual result |
| --- | --- |
| Baseline `.venv/bin/python -m pytest -q` | 55 passed; existing Starlette/httpx deprecation warning |
| Final `.venv/bin/python -m pytest -q` | **123 passed**, 1 existing Starlette/httpx warning, 4.59 seconds |
| Native Groq SDK using `httpx.MockTransport` | Tool-message protocol and separate strict JSON-schema phase passed; no real provider request |
| Mocked configured Copilot API | Nepali Groq-mode response, correct run and numerical fact passed |
| `scripts/check_dashboard.py --base-url http://127.0.0.1:8765 --output output/qa/copilot-browser` | Passed; zero page errors; no overflow at 360, 768, 1280, 1440 px, initial and synthetic states |
| `scripts/review_outputs.py ... --output output/qa/copilot-review.json` | **16/16** held-out bilingual prompts; five synthetic settlement-route reviews; no issues |
| `scripts/verify_copilot.py --base-url http://127.0.0.1:8765 --run-id b8481150-9c59-4640-a0b2-cb41346fd218 --allow-demo` | **6/6** API checks: both languages, compound question, absent casualty statistic and both PDFs |
| `node --check app/static/app.js`, `bash -n run.sh`, `git diff --check` | Passed |
| `uv sync --frozen --python 3.12` | Verified locked project environment |
| PDF QA | Both one-page PDFs opened with pypdf and rendered with Poppler; English/Nepali images visually inspected |
| Local website | http://127.0.0.1:8000 health returned OK, Groq disabled/unconfigured, deterministic fallback available |

The isolated browser run tested map/layer controls, hidden-tab area fitting, bilingual answers, loading, expandable evidence, compound questions, unsupported casualties, bounded history, Nepali PDF downloads, synthetic labels, source/reference separation, reload and invalid-bounds recovery. Local QA artifacts are under ignored `output/qa/`; no large datasets or credentials are committed.

## 10. Evidence-backed examples

These are **synthetic software-check results**, not observations of the Nepal flood. Stored test run: `b8481150-9c59-4640-a0b2-cb41346fd218`.

- “How large is the newly detected water area?” → potential new inundation **42.080 km²**, read from that run's `stats.water_km2` (raw value 42.08), with uncertainty and a synthetic label.
- “सम्भावित नयाँ डुबानको क्षेत्रफल कति छ?” → the same **42.080 वर्ग कि.मि.**, same fact ID/raw value, Nepali uncertainty.
- “Which roads are exposed and which settlements are isolated?” → retrieves both saved infrastructure and connectivity facts, describes potential exposure and modeled access loss, and includes evidence IDs. Closure is unverified.
- “How many people died?” → unsupported; no casualty estimate or evidence facts supplied.
- Missing `water_km2` in a controlled unit/API fixture → “Unavailable” / “उपलब्ध छैन”; no substitute zero.

## 11. Incomplete validation and limitations

Live Groq responses and real satellite-analysis end-to-end behavior have **not** been executed: there is no private `.env`, exported Groq key, exported CDSE OAuth configuration or real saved analysis in the workspace. User-owned Sentinel APIs must be configured locally; keys should never be shared in messages. Actual satellite processing may be used through the existing public or Copernicus path once a real run is available.

Native Nepali review, Nepal case-study evaluation and field verification remain outstanding. Existing historical model cards/case-study/video outputs were preserved and do not validate this implementation. There is no unrestricted generated prose/free translation path; controlled bilingual narrative is deliberate. Missing historical metadata cannot be reconstructed safely. Provider availability, rate limits and language/tool-selection quality require live validation. Very long reports may require HTML.

## 12. Live validation steps

Configure the private key and enable Groq, restart `./run.sh`, create or select a real stored analysis, then run:

```bash
uv run python scripts/verify_copilot.py --require-llm
# Or select a known real run:
uv run python scripts/verify_copilot.py --run-id REAL_RUN_UUID --require-llm
```

The command rejects synthetic runs unless explicitly authorized with `--allow-demo`, and rejects fallback for supported checks when `--require-llm` is used. Run fallback checks after setting `LLM_ENABLED=false` and restarting. Review Nepali terminology with a native speaker and independently validate mapping/access hypotheses before operational use. Detailed setup, API contracts and troubleshooting are in [COPILOT.md](COPILOT.md).
