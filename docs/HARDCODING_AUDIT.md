# Hardcoding audit and repairs

The repaired application derives event measurements from the selected run. Mapping/acquisition rules, independent evaluation policy and optional presets are explicit configuration. Custom bounds/date are required unless a preset is explicitly selected. Legal credit strings and bilingual labels remain authored content. See [the six additional repairs and verification](REMAINING_HARDCODING_REPAIRS.md) and [earlier live verification](GENERALIZATION.md).

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
| Event bounds/date repeated in UI and CLI | Optional validated `app/presets.json`; fresh custom inputs are empty/required; analyze/catalog CLI require bounds/date or an explicit preset |
| Custom requests inherited Nepal area name/cutoff | Generic custom name; cutoff scoped to case/location/date rather than globally by event date |
| Radar/optical and access defaults scattered through code | Validated versioned `app/mapping.toml`; settings and per-run overrides retained in provenance |
| Slider maximum disagreed with API | Dashboard reads threshold bounds from `AnalysisRequest` JSON schema |
| Fixed screening distance remained 1.92 km at other resolutions | Public-source warning derives distance from grid resolution and actual ray padding |
| Credentials configured but requests still defaulted to public satellite access | Shared `auto` provider resolves to CDSE with both credentials; a CDSE error never silently switches to public imagery or synthetic inputs |
| Optical queries reused a narrow radar time window | Selected optical UTC day is queried; actual processed tile IDs/timestamps are checked and retained separately from STAC datatake candidates |
| One-tile/cloud-percentage cutoffs hid usable pixels | Compatible adjacent-tile footprints are unioned; metadata cloud percentage ranks candidates while actual SCL/dataMask determines usable pixels |
| Topographic shadow accepted/preferred over a clear overlapping tile | Both satellite paths use the configured official SCL allowlist; shadow and unknown codes remain unobserved |
| OAuth requested repeatedly and Sentinel Hub retry delay misinterpreted | Expiring credential-bound token reuse, one 401 refresh, bounded retries with Sentinel Hub's millisecond delay |
| Repeated Groq calls spent tokens on identical evidence | Bounded validated draft cache, concurrent duplicate suppression, provider concurrency/cooldown; cache hits revalidate against current evidence and report zero requests/tokens |
| 2026/EMSR927 labels appeared for unrelated events | Attribution years follow actual observations; evaluation activation appears only when recorded on that run |
| Pre-existing optical water counted as new radar water | Clear optical baseline water is excluded before final class assignment |
| Tiny positive coverage rounded to unavailable/zero | Eligible counts determine availability; new saved percentages preserve precision |
| VV-only product accepted although raw processor needs VH | Pair selection requires both polarizations; public path also checks both asset keys |
| Valid historical OSM v2 cache rejected | Replay accepts both supported source identifiers while retaining source/date/hash checks |
| Provider failures flattened/slow report generation timed out | Specific sanitized reasons/status, one bounded repair, advertised short rate-limit retries, 25-second request and 60-second total deadline; optional reasoning effort is configured |
| Asset agreement used union observation coverage | Evaluation now requires joint water/debris eligibility and the exact raster grid; polygon coverage is area-weighted and line samples length-weighted |
| Real export manifest always described synthetic reports | Manifest reflects actual source mode; verified snapshots can be exported without new provider calls and reject changed facts |
| Fixed road classes omitted valid ramps | `app/mapping.toml` defines `drivable_highways`, including five link-road types. Vehicle restrictions still apply; the selected list is saved in each new analysis |
| Form rejected fine coordinates and omitted valid resolutions | Coordinates use arbitrary decimal steps; resolution is numeric with bounds/step from the API schema. Configured change thresholds also retain arbitrary supported decimals |
| Reference fetch selected EMSR927 and its output implicitly | Activation is required, the default directory derives from it, and existing metadata is checked before any writes. Asset comparison requires an explicit reference directory |
| Public provider limits and endpoints remained inline | Public STAC/signing/DEM/geoid URLs, timeouts, retries, page bounds and GDAL limits are in `app/acquisition.toml`; historical OSM read limits are configured too |
| Matching distances/overlap remained inline | Validated `app/evaluation.toml` plus CLI overrides; effective policy, prediction hashes and reference hashes are stored in the comparison |
| Synthetic notice named Nepal for unrelated inputs | Dashboard notice is event-neutral and retains the synthetic label |

Fixed content retained intentionally:

- `app/presets.json` contains the PDF's optional named case and documented historical cutoff. It supplies inputs only after explicit selection. `FLOOD_LENS_PRESETS_FILE` can replace the catalog, including with an empty JSON object.
- `app/mapping.toml` contains versioned rule defaults. Per-request parameters can override exposed settings; configuration changes require restart and new analysis/reprocessing. Existing runs keep their recorded parameters.
- `app/acquisition.toml` contains service endpoints and bounded acquisition/ranking/retry policy. `FLOOD_LENS_MAPPING_CONFIG` and `FLOOD_LENS_ACQUISITION_CONFIG` select local replacement files. Keys stay in the private environment.
- `app/evaluation.toml` contains independent comparison tolerances; `FLOOD_LENS_EVALUATION_CONFIG` selects a replacement. These values never enter hazard inference.
- Schemas, supported language/status enums, safety/resource bounds, physical units and algorithm definitions are code contracts. Evaluation matching tolerances are declared in the output method, not hidden case results.
- Bilingual labels, uncertainty/refusal phrases and legal WorldDEM/OSM wording are authored content. Sentinel observation years are computed; the 2026 credit remains correct for the dated Nepal case.
- `app/demo.py` and test fixtures intentionally use deterministic synthetic inputs. Every such analysis stays labeled synthetic and never replaces failed real acquisition.
- Historical reports/videos retain their original dated numbers. Current deliverables and the checklist identify the repaired version; archived values do not drive API responses.

Native generated language is constrained, rather than unconstrained factual prose. This preserves evidence binding but does not guarantee translation quality, stored-map accuracy or the meaning of every connective phrase. See [the latest checks](AUDIT_REPAIRS.md) and [Track B checklist](TRACK_B_CHECKLIST.md).
