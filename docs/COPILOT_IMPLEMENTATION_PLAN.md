# Copilot implementation plan

Repository inspected on 14 October 2026. Baseline: `python -m pytest -q` in the project environment, **55 passed**, one existing Starlette/httpx deprecation warning. Working tree started clean.

## Verified starting architecture (before implementation)

- `app/static/app.js`: `ask()` posts a question and selected language for `state.run.id`; it already discards responses when the selected run changes.
- `app/main.py`: `POST /api/analyses/{run_id}/copilot` loads `result.json` via `saved_run()` and calls `app.copilot.answer()`.
- `app/copilot.py`: local TF-IDF/logistic-regression intent classification and authored bilingual fact templates. No generative LLM or tool-calling provider is connected.
- `app/pipeline.py`, `app/geo.py`, `app/impact.py`: working change detection, exposure intersections and directed road-graph calculations. These algorithms will be preserved.
- `data/runs/<UUID>/result.json`: existing persistence, containing stats, layers and provenance. The only stored workspace run is explicitly synthetic; no real-data run or configured LLM key is currently available.
- `app/report.py`: printable bilingual HTML and English ReportLab PDF. Nepali PDF is explicitly rejected by the current route. Templates assume complete result fields, so missing data can raise errors.
- `app/models.py`: analysis, question/language and trace request validation. Existing chat response fields must remain compatible.
- `tests/`: mapping, provider, copilot, report and API checks; `scripts/check_dashboard.py` checks synthetic UI behavior at four widths.

## Incremental implementation

1. Add Groq through its official Python SDK, environment configuration, bounded timeouts and failure handling. Default to deterministic mode without credentials.
2. Build Pydantic evidence facts and six approved tools: event summary, surface change, infrastructure impacts, connectivity, sources/limitations and feature evidence. Bind each tool context to a single validated saved run.
3. Add strict structured claims referencing returned fact IDs, subject IDs, fields, interpretation and allowlisted formatting. Resolve values in Python; reject ungrounded or cross-run claims. Use controlled bilingual narrative blocks rather than unrestricted factual prose.
4. Integrate a bounded tool-calling loop and one validation repair attempt, preserving existing deterministic classification/templates as the fallback. Support compound questions and explicit missing data.
5. Build typed report data from the same evidence. Preserve HTML/PDF routes and add shaped Nepali PDF rendering with one-page verification.
6. Extend existing chat controls with loading, response provenance, supporting facts and report downloads. Keep selected-run checks.
7. Add mocked SDK, security, grounding, language, missing-data, report and API tests; run them at each stage and finish with the full suite and isolated browser checks.
8. Update README, `.env.example`, architecture/troubleshooting documentation and actual implementation/test results. Clearly distinguish mocked LLM tests and synthetic checks from unavailable live-provider/real-satellite checks.

No segmentation training, external training downloads, new database, multi-provider framework, geospatial rewrites or frontend replacement is planned. Provider keys and internal traces must never reach responses or committed fixtures.
