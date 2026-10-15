# Project audit repairs - 15 October 2026

The audit's chat, configuration, mapping and evaluation defects are repaired. Existing user edits and original acquired runs were preserved. The wider case uses retained real inputs; a separate small-area satellite/historical OSM acquisition also completed after the repairs. See the [hardcoding audit](HARDCODING_AUDIT.md) and [PDF checklist](TRACK_B_CHECKLIST.md).

## Copilot and reports

- Recognised questions retrieve scoped evidence in Python and normally require one Groq generation request. Provider output contains compact fact references; Python binds full run/subject/field/interpretation identities.
- Generated English/Nepali sentences use validated fact placeholders. Literal values, known feature names, markup, unsupported physical outcomes and unclaimed references are rejected. Required uncertainty remains present. Generated connective wording is not a field-truth guarantee.
- The report builder revalidates and consumes generated narrative, while retaining mandatory source dates, measurements, unavailable fields, attribution and limitations.
- Radar coverage, optical coverage and water/debris eligibility are distinct. Hospital inventory counts unique mapped elements separately from hospital access. Water/change thresholds are retrievable. Relevance validation requires every supported question topic and scope.
- Mixed questions retain supported answers and decline unsupported casualty/population estimates separately.
- Complete matching lists are paginated locally, with run-bound row evidence and map links. A configured record cap is explicitly labeled with a full-layer export.
- Cached alias tries replace per-feature regular-expression scans and invalidate when names/IDs change.
- Short provider-advertised rate-limit delays are retried within configured budgets. Longer delays and specific request/structured-output failures remain visible, with sanitized HTTP status metadata.
- Report checks exposed the previous 10-second request limit. The local/default request timeout is now 25 seconds, with a 60-second total provider deadline; API checks are paced separately for account quotas.
- Provider transport uses short request-local aliases; outgoing facts, claims and citations restore permanent run-bound IDs. Generated explanation codes must be empty because Python supplies mandatory uncertainty. The private default-model configuration uses low reasoning effort.
- Compound eligibility/coverage clauses require each requested measurement, including coordinated radar/optical and water/debris questions. Ordinary questions cannot silently become report requests. The recorder rejects an answer missing water-area or water-eligibility evidence.

## Mapping and configuration

- Clear pre-event optical water is removed from new-water candidates before final class assignment.
- Positive eligible pixel counts remain partial/available even when an older percentage rounded to zero. New saved class coverage and statistics agree; percentages are rounded for display.
- Scene selection requires VV+VH; the public acquisition path also verifies both measurement assets before selection.
- `app/mapping.toml` supplies versioned processing defaults; the selected settings are saved in provenance.
- `app/presets.py` supplies shared case bounds/date/OSM cutoff to the backend and dashboard. The Nepal cutoff is geographically scoped; unrelated events sharing its date do not inherit it.
- Cache replay accepts both supported historical OSM API source identifiers and continues to validate input contracts/hashes.
- `/health` supports the local hosting probe alongside `/api/health`.
- CLI defaults also use shared configuration. Custom requests receive a generic area label. Slider limits come from the request schema, source warnings derive their distance from resolution, and method coverage is rounded for display without changing stored precision.
- Official asset grading requires joint water/debris eligibility on the exact raster grid. Polygon coverage is area-weighted and lines are length-weighted; unavailable debris cannot create an artificial negative-exposure score.

## Executed verification

| Check | Result |
| --- | --- |
| Complete regression suite | 258 passed; two existing dependency/test deprecation warnings |
| JavaScript and Git whitespace | Passed |
| Native Groq chat | English, Nepali and compound evidence requests returned validated `mode=llm`, each using one provider request |
| Local real-run questions | Radar coverage 97.6%; 9 mapped hospital elements; water threshold -17 dB; all 109 settlements returned |
| Query performance | First local coverage response approximately 0.27 s; subsequent checked questions approximately 0.05-0.11 s on this workstation, excluding provider latency/source-file loading |
| Browser | No page errors or overflow at 360/768/1280 px; settlement details, bilingual citations, repaired questions, complete lists and list-to-map inspection passed |
| Bilingual PDF regression | Both languages rendered one readable A4 page, including validated generated narrative |
| Native Groq reports | English and Nepali generated in one provider request each; six-check actual API verification passed |
| Cached real replay | Completed under a new UUID with versioned configuration and precise class coverage |
| Source preservation | Original result and acquired-input hashes still match the replay provenance |
| EMSR927 comparisons | Extent and official asset grading executed as unavailable/N/A; Landslide has no eligible debris observations, and joint asset observations are unavailable |
| Fresh acquisition | New small-area CDSE/OSM run completed, with public Copernicus GLO-30 after CDSE DEM HTTP 403; maps/access/reports saved |
| Submission artifacts | Rebuilt six-page PDF, genuine Groq bilingual one-page exports, offline HTML/JSON/checksum manifest, case-study package and new 180-second captioned video |

Browser chat/list QA used local deterministic replay to avoid extra provider calls; native Groq checks were performed separately. Final recorded English/Nepali/compound chat calls took approximately 1.1-1.3 seconds; bilingual report calls approximately 2.6-2.7 seconds, one provider request each. These are account/workstation observations, not promised performance. Local records are in ignored `output/qa/audit-real-api.json` and `output/qa/audit-real-browser/`. Verified persisted report snapshots were independently rebound against source facts by the exporter.

## Current real run

The current cached replay is `0717d3b1-ac72-48d2-a8bb-8ecb93e9663c`, derived from original acquisition `74c35c59-a417-4d63-aa1c-b30356311cc9`. It retains the original AOI, 26 August 2026 event, 16/28 August radar scenes and pre-event OSM. `new_acquisition=false`; input/source-result hashes are retained.

Radar observation coverage is 97.6%, while water eligibility is 13.639% (13.6% displayed). Detected new water is 0.000 km² within eligible observations; debris is unavailable. Settlement access remains 56 unassessed, 47 uncertain and 6 without a baseline mapped connection. This is partial scientific coverage, not proof of no flood or damage. Independent class-matched accuracy, field validation and native-speaker Nepali sign-off remain outstanding.

Older videos, reports and dated QA describe earlier implementations. They do not replace the verification above.

## Fresh-run scope

Run `25236045-4d1e-4fd5-baac-26918b4224e7` used bounds `[85.28,28.0,85.32,28.04]`, the requested event date and 30 m resolution. Acquisition progressed through historical OSM, matching Sentinel-1 tracks, actual pre/post processing, DEM retrieval, optical availability, mapping, exposure/access and exports. `acquisition_mode=fresh_acquisition`; it did not use replay. Water eligibility is 12.942%, radar coverage 99.9%, and debris unavailable. The mapped 11 settlements are all unassessed because the small tile has no mapped destination. This is a successful acquisition/software path with partial scientific coverage, not verified absence of damage or a guarantee for arbitrary future inputs.

Reports are in `output/pdf/copilot-real/`, the six-page report in `docs/technical-report.pdf`, the new video in `output/video/flooline-repaired-demo.mp4` and comparisons in `output/case-study/`. Original user video files were preserved. Changes remain local and uncommitted; native Nepali sign-off, field/class-matched accuracy evidence and publication remain external work.
