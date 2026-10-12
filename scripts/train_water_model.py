"""Train on real Kuro Siwo + Sen1Floods11, preserving publisher splits."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from threadpoolctl import threadpool_limits

from app.datasets import DATASETS, TRAINING_CITATIONS, load_sample, save_json, sha256, validate_training_splits
from app.evaluation import binary_metrics
from app.segmentation import FEATURES, MODEL_FOLDER, pixel_features

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data", type=Path, default=DATASETS)
parser.add_argument("--output", type=Path, default=MODEL_FOLDER)
parser.add_argument("--pixels-per-chip", type=int, default=4096)
parser.add_argument("--trees", type=int, default=64)
args = parser.parse_args()
if args.pixels_per_chip < 100 or args.trees < 8:
    parser.error("Use at least 100 pixels per chip and 8 trees.")
records, manifest_hashes = [], {}
for dataset in ("kuro_siwo", "sen1floods11"):
    folder = args.data / "training" / dataset
    manifest = folder / "manifest.json"
    content = json.loads(manifest.read_text())
    manifest_hashes[dataset] = sha256(manifest)
    for record in content["records"]:
        records.append((record, folder))
for dataset in ("kuro_siwo", "sen1floods11"):
    split_ids = {split: {r["id"] for r, _ in records if r["dataset"] == dataset and r["split"] == split} for split in ("train", "valid", "test")}
    if any(split_ids[a] & split_ids[b] for a, b in (("train", "valid"), ("train", "test"), ("valid", "test"))):
        raise ValueError("Training and evaluation contain overlapping chip IDs.")
validate_training_splits([record for record, _ in records])
rng = np.random.default_rng(927)
x, y, provenance = [], [], []
for record, folder in records:
    if record["split"] != "train":
        continue
    for key, path in record["files"].items():
        if sha256(folder / path) != record["sha256"][key]:
            raise ValueError(f"Training input checksum changed: {record['id']}")
    vv, labels, valid = load_sample(record, folder)
    features, usable = pixel_features(vv, valid)
    positions = np.flatnonzero(usable)
    selected = rng.choice(positions, min(len(positions), args.pixels_per_chip), replace=False)
    x.append(features.reshape(-1, len(FEATURES))[selected])
    y.append(labels.ravel()[selected].astype("uint8"))
    provenance.append({"dataset": record["dataset"], "id": record["id"], "event": record["event"], "pixels": len(selected)})
if not x or {p["dataset"] for p in provenance if p["pixels"]} != {"kuro_siwo", "sen1floods11"}:
    raise ValueError("Both listed datasets must contribute actual training pixels.")
x, y = np.concatenate(x), np.concatenate(y)
if len(np.unique(y)) != 2:
    raise ValueError("Training requires observed water and non-water labels.")
print(f"Training on {len(x):,} real labeled pixels from {len(provenance)} chips across both datasets", flush=True)
model = ExtraTreesClassifier(n_estimators=args.trees, max_depth=14, min_samples_leaf=12, n_jobs=2, random_state=927)
with threadpool_limits(limits=2):
    model.fit(x, y)
args.output.mkdir(parents=True, exist_ok=True)
joblib.dump(model, args.output / "model.joblib", compress=3)
counts, evaluated = {}, []
for record, folder in records:
    if record["split"] == "train":
        continue
    vv, labels, valid = load_sample(record, folder)
    features, usable = pixel_features(vv, valid)
    positions = np.flatnonzero(usable)
    prediction = np.zeros(labels.shape, dtype=bool)
    for offset in range(0, len(positions), 65536):
        selected = positions[offset:offset+65536]
        prediction.ravel()[selected] = model.predict_proba(features.reshape(-1, len(FEATURES))[selected])[:, 1] >= .5
    metrics = binary_metrics(prediction, labels, usable)
    key = f"{record['dataset']}/{record['split']}"
    count = counts.setdefault(key, Counter())
    count.update({k: v for k, v in metrics.items() if k.endswith("_pixels")})
    evaluated.append({"dataset": record["dataset"], "split": record["split"], "id": record["id"], "event": record["event"], "metrics": metrics})
    print(f"Evaluated {key}: {record['id']}", flush=True)
scores = {}
for key, count in counts.items():
    tp, fp, fn = [count[k] for k in ("true_positive_pixels", "false_positive_pixels", "false_negative_pixels")]
    scores[key] = {**dict(count), "iou": tp/(tp+fp+fn) if tp+fp+fn else 1,
                   "f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1,
                   "precision": tp/(tp+fp) if tp+fp else None, "recall": tp/(tp+fn) if tp+fn else None}
card = {"status": "trained", "created_at": datetime.now(timezone.utc).isoformat(), "algorithm": "ExtraTrees pixel water segmentation baseline",
        "training_datasets": ["kuro_siwo", "sen1floods11"], "features": FEATURES, "threshold": .5,
        "label_mapping": {"kuro_siwo": "0 non-water; 1 permanent and 2 flood mapped to water; 3 ignored", "sen1floods11": "0 non-water, 1 water; -1 excluded"},
        "training_pixels": len(x), "training_chip_count": len(provenance), "training_inputs": provenance,
        "publisher_manifest_sha256": manifest_hashes, "artifact_sha256": sha256(args.output / "model.joblib"),
        "scores": scores, "evaluation_chip_count": len(evaluated), "citations": TRAINING_CITATIONS,
        "limitations": ["Initial subsets of the listed datasets, not complete-dataset benchmark results.",
            "Binary observed water model, not a learned debris detector; new flooding is determined by comparing pre/post predictions.",
            "Publisher splits are preserved. Sen1Floods11 splits can share events; this is chip-held-out, not an unseen-event claim.",
            "Kuro Siwo sigma0 and live gamma0 processing, terrain, spatial resolution and sampling differ; Himalayan generalization requires separate verification.",
            "EMSR927 and all published damage maps are excluded from training and prediction."]}
save_json(args.output / "model-card.json", card)
save_json(args.output / "held-out-results.json", evaluated)
print(json.dumps(scores, indent=2), flush=True)
