# Behavioral hardcoding follow-up

The previous integrated repair pass was incomplete. Passing 347 tests did not prove that remaining fixed behavior had been removed. This follow-up reproduced three defects before repairing them.

| Actual leftover | Repair |
| --- | --- |
| Local question patterns still determined ordinary retrieval scopes. An unfamiliar question, “Which hamlets retain a path to medical care?”, could accept true but unrelated water facts because no semantic scope was enforced | `app/semantic_query.py` now interprets questions through Groq into a strict, validated query plan before retrieval. `copilot_service.py` binds final claims, complete lists and fallback to that plan. Explicit local mode refuses an unresolved scope instead of accepting arbitrary model-selected evidence |
| `app/sources.py` always advertised the specific EMSR927 activation, and the browser constructed a fixed reference host | The catalog now has a generic EMS reference entry. The backend builds a link only from the run's explicit activation and configured `ems_public_base_url`. Historical OSM links also use the configured endpoint |
| `app/models.py` always described the road closure scenario as half a raster cell, even with a different configured overlap fraction | The warning refers to the recorded per-run overlap threshold. Mapped facilities are described generically, so a configured clinic profile is not mislabeled a hospital |
| Groq model/URL/budget defaults were copied again in `from_env()` | Environment loading now overrides one validated set of model defaults. Query-planning/tool-selection completion limits are configurable through `LLM_QUERY_MAX_COMPLETION_TOKENS` |

## Query planning and quota

`LLM_QUERY_PLANNING=semantic` is the default when Groq is enabled/configured. The question interpreter receives available layer names, destination profiles/aliases, resolved feature references and bounded same-run history. It returns scope only, never areas, counts, dates or conclusions. Python rejects unknown destinations, invented measurements, unresolved feature IDs, incoherent topic/filter combinations and invalid report scopes. The answer phase then receives approved saved evidence and returns validated fact references/placeholders.

`LLM_APPLICATION_RETRIEVAL=true` runs the planned evidence calls locally. A fresh successful answer normally spends one bounded query interpretation plus one grounded composition request. One composition repair is allowed if validation fails. Both phases share the total deadline, account cooldown, concurrency limit and duplicate-request admission. `LLM_QUERY_MAX_COMPLETION_TOKENS` defaults to 1024 for interpretation; answer completion has its separate configured budget.

The bounded cache reuses both scope and answer. Its identity includes run-bound evidence/property fingerprints, destination profiles, question, history, language, prompts/schemas and provider configuration. Reuse retrieves current facts and revalidates the saved draft. Changed properties, missing layers and source metadata invalidate reuse. Simultaneous identical questions share both phases. Cached replies report zero new requests and tokens.

When composition fails after a valid semantic interpretation, the deterministic answer retains that interpretation rather than reverting to an unrelated keyword match. If interpretation itself fails, the labeled local fallback remains narrower. `LLM_QUERY_PLANNING=local` explicitly selects the compatibility path. Its phrase rules remain in `app/copilot.py` and `copilot_query.py`; they no longer select the normal configured Groq scope. They cannot release arbitrary evidence for an unresolved question.

## Validation

The full suite passes **375 tests**, with two existing dependency deprecation warnings. The established local/tool-selection tests explicitly select that compatibility path. The new primary-path matrix exercises the actual Groq SDK over mocked HTTP, covering unfamiliar bilingual phrasing, filtered counts/lists, exact named subjects, complete situation reports, missing versus empty layers, invalid scopes, changed-source invalidation, shared concurrent generation, rate-limit cooldown, all exposure categories and the single bounded composition repair.

Live Groq checks used the saved real CDSE analysis `c2f74c94-af7f-426a-bc02-e66b5c0f42aa`:

- English: “Which hamlets retain a path to medical care?” selected `status=connected`, `destination_type=hospital`.
- Nepali: “अस्पतालतर्फ बाटो नपुग्ने बस्तीको सूची दिनुहोस्।” selected `status=not_connected`, `destination_type=hospital`.
- Both used **two requests**, returned validated evidence, omitted unrelated water facts and reused identical answers with **zero new requests/tokens**. The saved source result remained unchanged.

The live record is `output/qa/semantic-query-live-verification.json`. These checks used previously acquired real inputs; this follow-up did not make a fresh satellite processing request. Native-speaker review and independent mapping/field validation remain separate work.

## What is still fixed, explicitly

- `app/demo.py` intentionally generates reproducible synthetic fixtures. Only explicit synthetic mode uses them; they are not a fallback for failed real acquisition.
- `app/copilot.py`, `copilot_query.py` and `grounding.py` retain authored fallback routing/wording, bilingual labels and uncertainty statements. Generation metadata identifies fallback versus Groq; `query_planning` identifies semantic versus local interpretation.
- `app/models.py` and `geo.py` retain declared supported-domain/resource guards, including the latitude/tile/grid constraints. These are enforced limits, not inferred observations or event answers.
- Sensor band/class encodings, units, CRS conventions, numerical tolerances, API/status/schema contracts and rendering styles remain code. Scientific thresholds and profiles remain explicit configured rules; they do not become universally accurate by being configurable.
- Dated saved runs and submission artifacts retain their original measurements, parameters and text snapshots. They are not rewritten to hide earlier behavior or replace observations.

This record does not claim that the repository has no literals or that every possible natural-language predicate is supported. It identifies the remaining fixed rules rather than treating a passing test count as proof that all hardcoding is gone.
