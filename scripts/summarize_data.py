"""Summarize a completed run without reading training datasets or model weights."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.storage import ROOT, save_json, sha256
from app.sources import SOURCES

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("run_folder", nargs="?", type=Path)
parser.add_argument("--output", type=Path, default=ROOT / "docs/current-run-summary.json")
args = parser.parse_args()
if args.run_folder is None:
    runs = Path(os.getenv("FLOOD_LENS_DATA", ROOT / "data/runs"))
    candidates = []
    for path in runs.glob("*/result.json"):
        try:
            run = json.loads(path.read_text())
            if run.get("mode") == "live" and run.get("created_at"):
                candidates.append((run["created_at"], path.parent))
        except (OSError, ValueError, TypeError):
            continue
    if not candidates:
        parser.exit(2, "No completed actual-data run exists. Run scripts/analyze.py first, or supply a completed run folder.\n")
    folder = max(candidates)[1]
else:
    folder = args.run_folder
try:
    run = json.loads((folder / "result.json").read_text())
except (OSError, ValueError) as error:
    parser.exit(2, f"Cannot read the completed run: {error}\n")
evaluation_path = folder / "evaluation.json"
evaluation = json.loads(evaluation_path.read_text()) if evaluation_path.exists() else run["evaluation"]
if evaluation.get("run_id") not in (None, run["id"]):
    parser.exit(2, "The evaluation belongs to a different run.\n")
save_json(args.output, {
    "sources": SOURCES, "run_id": run["id"], "mode": run["mode"],
    "created_at": run["created_at"], "bbox": run["bbox"], "event_date": run["flood_date"],
    "stats": run["stats"], "provenance": run["provenance"], "warnings": run["warnings"],
    "evaluation": evaluation, "attributions": run["attributions"],
    "raster_checksums": {p.name: sha256(p) for p in folder.glob("*.tif")},
    "artifact_directory": str(folder.resolve()), "model_training": "Managed outside this application",
})
print(f"Saved run summary: {args.output}")
