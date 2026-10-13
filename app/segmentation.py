"""Validated inference boundary for a user-supplied model; no dataset parsing."""
import importlib
import json
import math
import os
import numpy as np

SETUP_MESSAGE = "Configure models/water_model.py with your trained model, or select the threshold baseline. See docs/MODEL_INTEGRATION.md."


def _adapter():
    name = os.getenv("FLOOD_LENS_MODEL_ADAPTER", "models.water_model")
    try:
        module = importlib.import_module(name)
        raw = module.model_info()
    except Exception as error:
        raise ValueError(f"Could not load water-model adapter {name}: {type(error).__name__}. {SETUP_MESSAGE}") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("configured"), bool):
        raise ValueError("model_info() must return a dictionary with a Boolean configured field.")
    info = dict(raw)
    if not isinstance(info.get("algorithm"), str) or not info["algorithm"].strip():
        raise ValueError("model_info() must include the model's algorithm/name.")
    threshold = info.get("threshold", .5)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError("The model probability threshold must be a finite number between 0 and 1.")
    info["threshold"] = float(threshold)
    if not isinstance(info.get("requires_vh", True), bool):
        raise ValueError("requires_vh must be Boolean.")
    info.setdefault("requires_vh", True)
    for field in ("training_datasets", "citations", "limitations"):
        value = info.setdefault(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Model metadata {field} must be a list of strings.")
    if not callable(getattr(module, "predict_water", None)):
        raise ValueError("The model adapter must implement predict_water(vv_db, vh_db, valid_mask).")
    try:
        json.dumps(info, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("model_info() must contain JSON-compatible metadata with finite numbers.") from error
    return module, info


def ensure_model_ready():
    _, info = _adapter()
    if not info["configured"]:
        raise ValueError(SETUP_MESSAGE)
    return info


def model_status():
    try:
        _, info = _adapter()
        return {**info, "status": "ready" if info["configured"] else "not configured",
                "setup": "docs/MODEL_INTEGRATION.md"}
    except ValueError as error:
        return {"status": "error", "configured": False, "message": str(error), "setup": "docs/MODEL_INTEGRATION.md"}


def water_probabilities(vv_db, valid, vh_db=None):
    module, info = _adapter()
    if not info["configured"]:
        raise ValueError(SETUP_MESSAGE)
    vv = np.asarray(vv_db, dtype="float32")
    valid = np.asarray(valid, dtype=bool)
    if vv.ndim != 2 or valid.shape != vv.shape:
        raise ValueError("Model VV input and validity mask must have identical two-dimensional shapes.")
    cross = None if vh_db is None else np.asarray(vh_db, dtype="float32")
    if cross is not None and cross.shape != vv.shape:
        raise ValueError("Model VV and VH input shapes must match.")
    if info["requires_vh"] and cross is None:
        raise ValueError("The configured model requires VH as well as VV observations.")
    observed = valid & np.isfinite(vv)
    if info["requires_vh"]:
        observed &= np.isfinite(cross)
    if not observed.any():
        return np.full(vv.shape, np.nan, dtype="float32"), observed, info
    try:
        output = np.asarray(module.predict_water(vv.copy(), None if cross is None else cross.copy(), observed.copy()), dtype="float32")
    except Exception as error:
        raise ValueError(f"External water-model prediction failed ({type(error).__name__}). Check your adapter's input channels and preprocessing.") from error
    if output.shape != vv.shape:
        raise ValueError(f"Model returned shape {output.shape}; expected {vv.shape}. Return a pixel-aligned 2D probability raster.")
    usable = observed & np.isfinite(output)
    if np.isinf(output[observed]).any() or np.any((output[usable] < 0) | (output[usable] > 1)):
        raise ValueError("Model output must contain water probabilities in [0, 1], not logits or class IDs.")
    if not usable.any():
        raise ValueError("Model returned no valid predictions for the observed pixels.")
    output = output.copy()
    output[~usable] = np.nan
    return output, usable, info
