"""Plug your trained water model in here. See docs/MODEL_INTEGRATION.md.

Keep framework-specific imports and weight loading inside your implementation.
The app works in threshold mode while this adapter is unconfigured.
"""
from pathlib import Path

WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"


def model_info():
    """Set configured=True only after implementing and loading your model."""
    return {
        "configured": False,
        "algorithm": "User-supplied water segmentation model",
        "threshold": 0.5,
        "requires_vh": True,
        "training_datasets": [],
        "citations": [],
        "limitations": ["No external model has been configured or validated yet."],
    }


def predict_water(vv_db, vh_db, valid_mask):
    """Return a float probability array with the same (height, width) as vv_db.

Inputs: terrain-corrected VV/VH in dB, optional VH=None, Boolean validity mask.
Outputs: water probabilities in [0, 1]; NaN can mark unassessed pixels.
Implement your model's normalization, tiling, inference and output alignment here.
Never train, download training data, or read reference maps in this function.
"""
    raise NotImplementedError("Load your weights and implement predict_water in models/water_model.py.")
