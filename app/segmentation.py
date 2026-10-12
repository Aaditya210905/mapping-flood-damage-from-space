"""A reproducible CPU water-segmentation baseline trained on both listed datasets."""
from functools import lru_cache
import json
import os
from pathlib import Path

import joblib
import numpy as np
from scipy import ndimage

from app.datasets import ROOT, sha256

MODEL_FOLDER = ROOT / "data" / "models" / "water-baseline"
LEGACY_FEATURES = ["VV dB", "3x3 VV median dB", "VV minus local median dB", "5x5 VV standard deviation dB"]
FEATURES = LEGACY_FEATURES + ["VH dB", "3x3 VH median dB", "VH minus local median dB", "5x5 VH standard deviation dB", "VV minus VH dB"]


def pixel_features(vv_db, valid, vh_db=None):
    values = np.clip(np.nan_to_num(vv_db, nan=-10, posinf=5, neginf=-40), -40, 5).astype("float32")
    median = ndimage.median_filter(values, size=3)
    mean = ndimage.uniform_filter(values, size=5)
    variance = np.maximum(ndimage.uniform_filter(values ** 2, size=5) - mean ** 2, 0)
    features = np.stack([values, median, values - median, np.sqrt(variance)], axis=-1)
    if vh_db is not None:
        cross, cross_usable = pixel_features(vh_db, valid)
        features = np.concatenate([features, cross, (values-cross[..., 0])[..., None]], axis=-1)
    # A pixel's context must also be observed, rather than inserting missing-data
    # values into learned features at the edge of a cloud/nodata hole.
    usable = ndimage.minimum_filter(valid.astype("uint8"), size=5, mode="constant", cval=0).astype(bool)
    return features, usable


@lru_cache(maxsize=4)
def _load_model(path, modified):
    # Only locally generated artifacts are loaded. Dataset files are NPY/GeoTIFF,
    # never downloaded pickle or pretrained executables.
    card = json.loads(Path(path).with_name("model-card.json").read_text(encoding="utf-8"))
    if sha256(path) != card.get("artifact_sha256"):
        raise ValueError("The water-model artifact checksum does not match its provenance.")
    model = joblib.load(path)
    if card.get("training_datasets") != ["kuro_siwo", "sen1floods11"] or card.get("features") not in (FEATURES, LEGACY_FEATURES):
        raise ValueError("The local water model does not match the two-dataset feature contract.")
    return model, card


def water_probabilities(vv_db, valid, model_folder=None, vh_db=None):
    folder = Path(model_folder or os.getenv("FLOOD_LENS_MODEL", MODEL_FOLDER))
    path = folder / "model.joblib"
    if not path.is_file():
        raise ValueError("Train the real-data water model first: python scripts/train_water_model.py")
    model, card = _load_model(str(path.resolve()), path.stat().st_mtime_ns)
    if card["features"] == FEATURES and vh_db is None:
        raise ValueError("The dual-polarization model requires observed VH as well as VV.")
    features, usable = pixel_features(vv_db, valid, vh_db if card["features"] == FEATURES else None)
    output = np.full(valid.shape, np.nan, dtype="float32")
    positions = np.flatnonzero(usable)
    flat = features.reshape(-1, len(card["features"]))
    water_index = list(model.classes_).index(1)
    for offset in range(0, len(positions), 65536):
        selected = positions[offset:offset + 65536]
        output.ravel()[selected] = model.predict_proba(flat[selected])[:, water_index]
    return output, usable, card


def model_status():
    path = Path(os.getenv("FLOOD_LENS_MODEL", MODEL_FOLDER)) / "model-card.json"
    if not path.exists():
        return {"status": "not trained", "training_datasets": ["kuro_siwo", "sen1floods11"]}
    return json.loads(path.read_text(encoding="utf-8"))
