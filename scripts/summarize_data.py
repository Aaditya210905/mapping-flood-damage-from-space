"""Write a compact, shareable inventory of actual local data and measured results."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.datasets import ROOT, DATASETS, SOURCES, save_json, sha256
from app.segmentation import MODEL_FOLDER

runs = [json.loads(p.read_text()) for p in (ROOT / "data/runs").glob("*/result.json")]
run = max((r for r in runs if r["mode"] == "live"), key=lambda r: r["created_at"])
folder = ROOT / "data/runs" / run["id"]
datasets = []
for source in SOURCES:
    if source["id"] in ("kuro_siwo", "sen1floods11"):
        path = DATASETS / "training" / source["id"] / "manifest.json"
        manifest = json.loads(path.read_text())
        records = manifest["records"]
        datasets.append({**source, "downloaded_samples": len(records),
                         "splits": {s: sum(r["split"] == s for r in records) for s in ("train", "valid", "test")},
                         "selection": manifest["selection"], "actual_host": manifest["download_location"],
                         "manifest": str(path.relative_to(ROOT)), "manifest_sha256": sha256(path)})
    elif source["id"] == "emsr927":
        path = DATASETS / "evaluation/EMSR927/manifest.json"
        manifest = json.loads(path.read_text())
        datasets.append({**source, "downloaded_products": len(manifest["products"]),
                         "manifest": str(path.relative_to(ROOT)), "manifest_sha256": sha256(path),
                         "reference_class": "Landslide", "prediction_input": False})
    else:
        datasets.append(source)
card = json.loads((MODEL_FOLDER / "model-card.json").read_text())
save_json(ROOT / "docs/water-model-card.json", card)
comparison = json.loads((folder / "evaluation.json").read_text())
save_json(ROOT / "docs/data-status.json", {"datasets": datasets, "run_id": run["id"], "mode": run["mode"],
    "created_at": run["created_at"], "bbox": run["bbox"], "event_date": run["flood_date"], "stats": run["stats"],
    "provenance": run["provenance"], "warnings": run["warnings"], "evaluation": comparison,
    "raster_checksums": {p.name: sha256(p) for p in folder.glob("*.tif")},
    "artifact_directory": str(folder.relative_to(ROOT)), "training_scope": "Initial real-data subsets of both listed datasets"})
print(f"Saved data inventory and model card for actual run {run['id']}")
