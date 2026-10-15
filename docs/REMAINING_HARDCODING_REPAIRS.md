# Six additional hardcoding repairs

This is the earlier six-item repair record. The later [integrated repairs and current validation](INTEGRATED_REPAIRS.md) supersede its configuration-version and test-count snapshot.

Completed against the six findings in the follow-up audit. Earlier saved runs, reference archives and submission PDFs/video retain their original snapshots.

| Finding | Result |
| --- | --- |
| Fixed road allowlist discarded link roads | `app/mapping.toml` version `rule-based-v4` defines `drivable_highways`, including motorway/trunk/primary/secondary/tertiary links. `app/impact.py` reads the configured profile; mapped vehicle restrictions remain effective. Each new result records the list in mapping configuration |
| Form rejected supported inputs | Coordinate inputs accept finer decimals. Resolution is a numeric field bounded by the API schema, so values such as 20 m survive initialization and submission. The radar-change slider also preserves supported non-half-step values such as 3.2 dB |
| Reference selection/output implicitly used EMSR927 | `fetch_reference.py` requires an explicit activation and derives `data/reference_only/ACTIVATION`. Both local and returned identities are checked before writing; a different activation cannot reuse a populated cache. `evaluate_assets.py` requires `--reference` |
| Public acquisition policy remained scattered | `app/acquisition.toml` version `acquisition-v3` declares public STAC/signing/DEM/geoid endpoints, timeouts, read retries, page bounds, GDAL limits, optical-age warning and historical OSM request limits. Public pagination follows GET/POST continuations, deduplicates IDs and checks origin |
| Asset matching used inline tolerances | `app/evaluation.toml` declares observation fraction, road buffer/overlap and other asset distance. CLI overrides are validated; effective values and prediction/reference hashes are stored in each comparison. Missing positive/observed comparisons remain N/A |
| Synthetic notice named an unrelated Nepal event | The notice now states that generated fixtures do not describe real event observations |

Public cache identity now includes search window, applicable preset cutoff, source endpoints and terrain horizon. Changing source/observation policy cannot silently reuse the previous acquisition cache; changing a timeout alone does not force new downloads. Existing cache directories are preserved. Saved-input replay checks the currently configured approved source endpoints.

## Configuration and commands

The files are validated at startup. Restart after changes. Use the current complete TOML files as the basis for local overrides; older schemas may omit required new policy fields. Overrides are `FLOOD_LENS_MAPPING_CONFIG`, `FLOOD_LENS_ACQUISITION_CONFIG`, `FLOOD_LENS_EVALUATION_CONFIG` and `FLOOD_LENS_PRESETS_FILE`. Keys stay in the private environment.

```bash
# Explicit example activation; substitute the required code
uv run python scripts/fetch_reference.py --activation EMSR927

# Supply the actual matching reference directory
uv run python scripts/evaluate_assets.py data/runs/RUN_ID \
  --reference data/reference_only/EMSR927 \
  --road-match-buffer-m 30 --road-match-min-overlap 0.5 \
  --asset-match-distance-m 50 --observation-threshold 0.8
```

The numerical flags above illustrate the existing default policy, not calibrated universal values. Omitting the four flags uses `app/evaluation.toml`. Evaluation never changes prediction files or becomes inference input. Selecting a reference still requires class, area and source-date review.

## Verification

- **311 tests passed**, with two existing dependency deprecation warnings. Six new routing tests first reproduced all five missing link-road types and an ignored profile override, then passed after repair.
- Configurable-policy tests use synthetic geometry and mocked HTTP. They verify activation isolation/preservation, wrong remote identity rejection, required CLI selection, configured endpoints/timeouts/retries, pagination, cache invalidation and actual changes in asset matches when tolerances change.
- Billable CDSE processing still does not blindly retry an ambiguous transport timeout; read-only requests explicitly opt into bounded transport retries.
- Browser checks passed at 360/768/1280/1440 px with no page errors. A synthetic run submitted 20 m resolution, four-decimal coordinates and a 3.2 dB threshold and retained all values in the completed result. Evidence: `output/qa/remaining-hardcoding-browser/browser-checks.json`.
- Actual cached CDSE Bangladesh inputs were replayed into new run `4e56ebc5-c117-43d3-9075-d7df8b418e38`. The road inventory increased from **522 to 527** because the five link roads were previously omitted. Water/debris observations retain their original partial coverage. No new satellite acquisition was made. The original result and acquired-input hashes are checked against the replay audit.

The native Groq inventory answer returned the authoritative **527** roads using one provider request (172 completion tokens). The five recovered OSM elements are all `trunk_link` roads. The saved-real dashboard also passed its map/evidence/list checks without page errors; its chat exercise uses deterministic replay and spends no Groq quota. These checks are separate from synthetic browser fixtures. Verification records are `output/qa/remaining-hardcoding-real-verification.json` and `output/qa/remaining-hardcoding-real-browser/checks.json`.

Algorithm definitions, schema/status/language contracts, required legal text, resource bounds and authored fallback wording remain declared code. This work does not claim zero numeric constants or universal field accuracy. The selected Copilot route still requires independent mapping validation and native-speaker Nepali review.
