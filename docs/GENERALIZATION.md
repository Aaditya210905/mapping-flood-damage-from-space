# Custom-area acquisition and Groq verification

Verified locally on 15 October 2026. These repairs remove implicit event inputs and use configured credentials for fresh observations. They do not establish mapping accuracy for every geography.

The subsequent [six remaining hardcoding repairs](REMAINING_HARDCODING_REPAIRS.md) add configurable driving classes, fine coordinate/resolution support, explicit reference selection and matching/provider policy. They recovered five omitted roads from the same real Bangladesh inputs without new satellite requests. The latest suite passes 311 tests; earlier fresh-acquisition evidence below remains a dated snapshot.

## What changed

- The API requires explicit bounds/date or an explicitly selected configured preset. CLI commands behave the same way. The fresh dashboard starts with empty custom inputs; an optional area name may be left blank.
- `auto` selects CDSE when both OAuth credentials exist. A partially configured credential pair is reported as an error. Explicit public access remains available. A failed CDSE satellite request never becomes a public or synthetic satellite run.
- OAuth tokens are reused until their actual expiry margin, bound to the endpoint and credential pair, and refreshed once after a 401. Sentinel Hub retries interpret `Retry-After` in milliseconds, respect bounded waits and do not blindly repeat ambiguous transport timeouts.
- CDSE catalog discovery follows bounded GET/POST pagination and removes duplicate product IDs. Compatible adjacent radar/optical footprints can jointly cover the requested area. Different platforms/tracks/passes are not combined by footprint selection.
- Cloud metadata ranks optical candidates instead of rejecting all tiles above a fixed cloud percentage. Actual SCL/dataMask determines usable observations. Topographic shadow and unknown SCL codes are excluded; both providers prefer clear samples over shadow in overlaps.
- Sentinel-2 catalog datatake time can precede local tile sensing time. The selected optical UTC day is requested, and the actual processed tile timestamps/IDs are validated and saved. Radar keeps a narrow selected-pass window. Before/after optical days cannot include the event day.
- Satellite processing requires both TIFF pixels and processed-scene metadata. Missing metadata, unexpected observation dates or missing dataMask pixels fail validation before TIFF acceptance. Signed query strings are removed from saved source paths.
- Optical mosaics retain each processed tile's ID, timestamp and epoch. Saved-input replay accepts validated multi-tile optical epochs and retains source hashes without using old predictions.
- Groq drafts are cached only for identical evidence/run, question/scope, language, history and provider configuration. Cache hits revalidate current evidence and report zero new requests/tokens. Concurrent duplicates share generation; process-local concurrency, admission waits and account cooldowns are bounded.
- Attribution years come from actual observations. Unrelated custom runs do not inherit Nepal's OSM cutoff or EMSR927 evaluation labels. The evaluation CLI requires an explicit activation to label an EMS comparison and accepts custom reference attribution. Real measurements still come from saved rasters and historical OSM, never presets or authored example answers.

## Configuration

| File or environment variable | Purpose |
| --- | --- |
| `app/mapping.toml` | Versioned water/debris/terrain/access rules and SCL policy |
| `app/acquisition.toml` | Provider endpoints, default provider, catalog bounds, scene ranking, retries and terrain horizon |
| `app/presets.json` | Optional named input areas/dates/cutoffs; never output statistics |
| `FLOOD_LENS_MAPPING_CONFIG` | Replacement local mapping TOML |
| `FLOOD_LENS_ACQUISITION_CONFIG` | Replacement local acquisition TOML |
| `FLOOD_LENS_PRESETS_FILE` | Replacement validated preset JSON; `{}` disables presets |
| `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET` | Private Sentinel Hub OAuth credentials |
| `LLM_ENABLED=true`, `GROQ_API_KEY` | Private Groq enablement/key |
| `OHSOME_API_KEY` | Optional historical OSM v2 fallback credential |
| `LLM_RESPONSE_CACHE_TTL_SECONDS`, `LLM_RESPONSE_CACHE_ENTRIES` | Default 300 seconds / 64 validated drafts; TTL zero disables reuse |
| `LLM_MAX_CONCURRENT_REQUESTS`, `LLM_QUEUE_WAIT_SECONDS` | Default two provider generations / two-second admission wait |
| `LLM_PROVIDER_COOLDOWN_SECONDS` | Default 30-second fallback cooldown when provider failure supplies no delay |

Restart after configuration changes. New analyses record the effective provider, requested provider and mapping/acquisition settings. Existing analyses keep their original observations and parameters. Model/reasoning/deadline settings remain in `.env.example` and [Copilot configuration](COPILOT.md).

Scientific thresholds, physical units, API contracts, resource limits, legal wording and bilingual labels are necessary declared rules. Moving an arbitrary threshold into a file does not make it accurate everywhere; regional calibration still requires independent evidence. Only the explicit synthetic path uses generated fixtures.

## Run with actual observations

Keep the existing private `.env` with both CDSE credentials and the enabled Groq configuration. Start `./run.sh`, open http://127.0.0.1:8000, select **Custom area**, enter bounds and a past date, keep **Actual satellite observations** and **Automatic from configured credentials**, then run analysis. The name is optional. Review per-class coverage and source dates before asking questions or generating reports.

```bash
uv run --frozen --no-sync --env-file .env python scripts/analyze.py \
  --bbox 91.35,23.0,91.40,23.05 --date 2024-08-21 --provider auto

# Explicit opt-in to the documented case, or substitute another configured ID
uv run --frozen --no-sync --env-file .env python scripts/analyze.py --preset trishuli
```

The coordinates above are an explicit reproducible example, not an application default. Supply the requested area/date. Use `--provider copernicus` to require CDSE even on a machine without configured auto credentials. A bare analyze/catalog command rejects missing inputs.

## Executed checks

- `uv run --frozen --no-sync pytest -q`: **285 passed**, two existing dependency deprecation warnings. Fixtures/transports are synthetic or mocked and do not spend provider quota.
- `node --check app/static/app.js`: passed.
- Fresh CDSE acquisitions below used the configured account, actual satellite pixels, strictly pre-event historical OSM and actual Copernicus elevation. They are new UUIDs, not replayed predictions.

| Region / event | Fresh run UUID | Observations and limits |
| --- | --- | --- |
| Bangladesh / 2024-08-21 | `7cefaaa5-7b25-4c3a-8f38-15a39875c419` | Real S1 and paired S2. Water-eligible coverage 99.9873%; paired clear optical/debris eligibility 0.2823%. Potential new water 1.201 km² within eligible coverage. A zero debris estimate applies only to those observed optical pixels |
| Nepal / 2026-08-26 | `9449f7e6-a371-4684-acb0-894cee2be1ba` | Real S1 and four actual processed S2 tiles across the two epochs. Water eligibility 12.9420%, debris eligibility 16.6951%. No destination inside this small AOI; its 11 settlements remain unassessed |
| Europe / 2021-07-14 | `bb36d222-efa1-4c83-a6a3-1e1562d1e743` | Real S1/OSM/DEM; water eligibility 99.1045%. This diagnostic preceded the optical time-window repair; optical remains unavailable in this saved run and was not re-fetched for this check |

CDSE denied DEM processing with HTTP 403 in these account checks. The application retrieved actual Copernicus GLO-30 elevation from the public Copernicus mirror and recorded the reason/tile URLs. Satellite imagery remained CDSE data. No quota or permission change was attempted.

On the Bangladesh run, live Groq English and Nepali water answers each required **one request** (179 and 191 completion tokens). Identical repeats each required **zero requests/tokens**, returned identical facts/wording, and exposed `cache_hit=true`. Evidence is in `output/qa/generalization-live-groq.json`. Unit tests separately cover evidence/language/credential invalidation, concurrent duplicate suppression and rate-limit cooldown.

Live English/Nepali report JSON and HTML also passed on this Bangladesh run: all saved-stat facts matched, both reports retained partial coverage, and attribution used 2024 observations. English required one Groq request; Nepali required two including its bounded validation repair. Evidence is in `output/qa/generalization-live-reports.json`. These checks did not export new submission PDFs; PDF pagination/font behavior is covered separately by regression tests.

Browser checks cover an initially empty workspace, explicit custom synthetic submission, saved real Bangladesh maps, bilingual citations, inventory/threshold questions, complete settlement lists and responsive widths. Both suites completed without page errors. Evidence is in `output/qa/generalization-empty-browser/checks.json` and `output/qa/generalization-real-browser/checks.json`; the latter uses deterministic chat replay and makes no Groq calls. Live provider checks are separate from browser fixtures.

## Remaining practical limits

- Supported inputs still require historical source coverage, compatible radar scenes and pre/post observations. Clouds, terrain, missing mapped destinations and unavailable historical OSM can produce partial or unavailable results.
- Areas are bounded to one degree and 2,500 raster pixels per side, within 75° latitude. Split larger or antimeridian areas. Supporting arbitrary global jobs would require a tiled processing/storage/job architecture and broader validation.
- Rules are configurable but not proven universally calibrated. Independent class-matched evaluation and field checks remain required before accuracy/damage/closure claims. Native-speaker Nepali review is still outstanding.
- Groq/CDSE quotas and network availability remain external. Cache/concurrency state is process-local; a multi-worker deployment would need shared admission/cache state to enforce an account-wide limit.
- Older submission PDFs/video preserve their dated analysis snapshots; they were not regenerated by these acquisition repairs. See [the PDF checklist](TRACK_B_CHECKLIST.md) for submission status.

Official contracts consulted: [CDSE token reuse](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Overview/Authentication.html), [Sentinel Hub millisecond retry delay](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Overview/RateLimiting.html), [processing/mosaics](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Process.html), [Sentinel-2 L2A](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html) and [processed-scene metadata](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Evalscript/resources/metadata_sh_docs.html).
