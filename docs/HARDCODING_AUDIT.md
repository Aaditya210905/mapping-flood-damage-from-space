# Hardcoding audit and repairs

The repaired application derives event measurements from the selected run. Processing defaults and the documented Nepal preset remain explicit configuration. Legal credit strings and bilingual labels remain authored content.

| Fault | Repaired location and behavior |
| --- | --- |
| Provider called but generated wording discarded | `app/copilot_types.py`, `app/copilot_service.py`, `app/grounding.py`, `app/report.py`: validated generated narrative reaches chat and reports; Python supplies values/citations and mandatory uncertainty |
| Long repeated fact IDs and invented explanation codes broke report JSON | Provider transport uses request-local short references and requires empty generated explanation codes; Python restores permanent IDs and warnings |
| Report warnings/area labels repeated until PDF overflow | Compact report rendering preserves measurements and typed evidence without repeating the same context for each fact |
| Radar coverage answered with class eligibility | `app/copilot_query.py`, `app/evidence.py`: separate radar, optical and per-class fields |
| Hospital plural incorrectly refused; inventory/access conflated | `mapped_inventory` counts saved mapped elements separately from connectivity; mapped operation remains unverified |
| Threshold requests ignored | Recorded water/change/slope/resolution parameters are retrievable and required by relevance validation |
| Correct but unrelated facts accepted | Every supported topic, requested field and named/road/destination scope is validated |
| Mixed casualty/map question entirely refused | Supported clauses are answered; unsupported statistics are declined separately |
| Four settlements shown as a complete list | `app/copilot_lists.py`: complete local pagination, totals, explicit caps and export links; all 109 current-case settlements verified |
| Every name compiled/scanned with a regex | Cached bounded alias trie invalidates on name/ID changes |
| Event bounds/date repeated in UI and CLI | Shared `app/presets.py`; data-source API initializes the dashboard; analyze/catalog CLI use the same defaults |
| Custom requests inherited Nepal area name/cutoff | Generic custom name; cutoff scoped to case/location/date rather than globally by event date |
| Radar/optical and access defaults scattered through code | Validated versioned `app/mapping.toml`; settings and per-run overrides retained in provenance |
| Slider maximum disagreed with API | Dashboard reads threshold bounds from `AnalysisRequest` JSON schema |
| Fixed screening distance remained 1.92 km at other resolutions | Public-source warning derives distance from grid resolution and actual ray padding |
| Pre-existing optical water counted as new radar water | Clear optical baseline water is excluded before final class assignment |
| Tiny positive coverage rounded to unavailable/zero | Eligible counts determine availability; new saved percentages preserve precision |
| VV-only product accepted although raw processor needs VH | Pair selection requires both polarizations; public path also checks both asset keys |
| Valid historical OSM v2 cache rejected | Replay accepts both supported source identifiers while retaining source/date/hash checks |
| Provider failures flattened/slow report generation timed out | Specific sanitized reasons/status, one bounded repair, advertised short rate-limit retries, 25-second request and 60-second total deadline; optional reasoning effort is configured |
| Asset agreement used union observation coverage | Evaluation now requires joint water/debris eligibility and the exact raster grid; polygon coverage is area-weighted and line samples length-weighted |
| Real export manifest always described synthetic reports | Manifest reflects actual source mode; verified snapshots can be exported without new provider calls and reject changed facts |

Fixed content retained intentionally:

- `app/presets.py` contains the PDF's named case and documented historical cutoff. This supplies request defaults, not answers or output statistics.
- `app/mapping.toml` contains versioned rule defaults. Per-request parameters can override exposed settings; configuration changes require restart and new analysis/reprocessing. Existing runs keep their recorded parameters.
- Schemas, supported language/status enums, safety/resource bounds, physical units and algorithm definitions are code contracts. Evaluation matching tolerances are declared in the output method, not hidden case results.
- Bilingual labels, uncertainty/refusal phrases and the required 2026 Sentinel/WorldDEM/OSM attribution are authored wording. The PDF requires those exact legal credits.
- `app/demo.py` and test fixtures intentionally use deterministic synthetic inputs. Every such analysis stays labeled synthetic and never replaces failed real acquisition.
- Historical reports/videos retain their original dated numbers. Current deliverables and the checklist identify the repaired version; archived values do not drive API responses.

Native generated language is constrained, rather than unconstrained factual prose. This preserves evidence binding but does not guarantee translation quality, stored-map accuracy or the meaning of every connective phrase. See [the latest checks](AUDIT_REPAIRS.md) and [Track B checklist](TRACK_B_CHECKLIST.md).
