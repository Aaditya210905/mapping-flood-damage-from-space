# Track B Copilot completion checklist

Checked against the local `Track B.pdf`. The selected AI component is the situation-report copilot; segmentation-model training is the alternative route and is not required for this selection.

Latest custom-area/provider/quota repairs and verification: [GENERALIZATION.md](GENERALIZATION.md). Older submission PDFs/video remain dated snapshots; the new acquisition fixes do not rewrite those artifacts.

| PDF requirement | Current status and evidence |
| --- | --- |
| Area/date input and before/after Sentinel-1 mapping | Implemented. Inputs are explicit; auto uses configured CDSE credentials. Fresh small-area CDSE acquisitions completed in Bangladesh, Nepal and Europe; IDs/coverage are in the generalization verification |
| Clear Sentinel-2 support and debris mapping | Optical time-window, adjacent-tile selection and shadow masking repaired. Fresh Bangladesh and Nepal CDSE runs have actual paired optical pixels/metadata; debris eligibility remains partial (0.2823% and 16.6951%). Unavailable observations stay unknown |
| Copernicus DEM | Implemented. Fresh run used the public Copernicus GLO-30 mirror after CDSE DEM returned HTTP 403; the source change is recorded |
| Historical OSM; exclude post-event edits | Implemented and checked. Snapshot selection must predate the event; the documented Nepal preset has a geographic cutoff |
| Buildings, roads and bridges overlaid | Implemented with mapped inventory/exposure, per-class assessment coverage and map exports; physical damage remains unconfirmed |
| Settlement road access to town/hospital | Implemented with destination-specific baseline/disrupted routes, uncertainty and unassessed states. Bounds/connectors/topology limit real-world interpretation |
| English and Nepali answers | Real Groq calls passed on the new Bangladesh data. Repeated identical answers use validated reuse with zero additional requests/tokens; fact/relevance checks and labeled fallback remain |
| Every number comes from computed maps | Typed evidence and claim validation; short provider aliases resolve back to permanent run-bound IDs. Forgery, relevance and cross-run regressions pass |
| System-generated one-page report | Real Groq English/Nepali snapshots, one page each, PDF/HTML/JSON plus source/output manifest in `output/pdf/copilot-real/` |
| Map dashboard | Browser checks passed at 360/768/1280 px; bilingual citations, settlement/hospital evidence, full lists and map links work |
| Repository and run README | README/configuration/CLI instructions updated locally; existing origin is `vishal88736/mapping-flood-damage-from-space`. Repair changes are not committed/pushed by this task |
| Report, maximum six pages, including limitations | Rebuilt `docs/technical-report.pdf` and `output/pdf/copilot-technical-report.pdf`, with editable Markdown source |
| Three-minute demo | New `output/video/flooline-repaired-demo.mp4`, exactly 180 seconds, captioned, actual Groq answers/report, saved real inputs, attribution and checksum manifest. Previous user recording preserved |
| Required attribution and training citations | Sentinel/WorldDEM/OSM credits present. EMS reference credited. No external satellite training dataset is used by this route; archived training artifacts retain their own citations |
| Case study compared with EMSR927 | Extent and official asset comparisons executed independently and packaged in `output/case-study/`. Both are N/A because matching/joint debris observations are unavailable; no water accuracy is claimed |
| Bonus upstream trace | DEM downhill trace and nearby settlements implemented; screening only, not hydraulic simulation |
| Live judge-selected area/date | A fresh selected small-area acquisition passed. Other choices remain subject to same-orbit scenes, historical OSM, source availability, tile bounds and quotas; cached replay cannot substitute for a different requested area/date |

Remaining work that cannot be replaced by code assertions:

1. Obtain native-speaker Nepali review and record corrections/sign-off in `NEPALI_GLOSSARY.md`.
2. Obtain suitable independent class-matched observations/reference evidence and field checks before claiming mapping accuracy, confirmed physical damage or verified closures. The current N/A comparison cannot be turned into a valid score by changing thresholds or examples.
3. Commit/push the reviewed local repairs and deliverables to the existing GitHub repository before submitting its link. This task preserves the user's earlier working-tree changes and does not publish them.
4. For the live judging session, run the judges' supplied area/date with working source access and quota. A successful test on this tile cannot guarantee all possible inputs.

No segmentation-model training, synthetic replacement observations, invented casualty values or altered reference maps are needed to complete the selected Copilot route.
