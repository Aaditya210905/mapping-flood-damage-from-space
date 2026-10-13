# Connect your water model

The application contains a placeholder adapter, not a trained water model. Dataset acquisition, parsing and water-model training have been removed. Train externally or obtain a model whose inputs and training data satisfy Track B, then connect its inference code here.

## Files to edit

1. Copy your weights to `models/weights/` (ignored by Git).
2. Implement `predict_water()` and weight loading in `models/water_model.py`.
3. Update `model_info()` and set `configured=True` once loading and inference work.
4. Install your model's inference dependencies in the project environment, restart the app and choose **External model**.

There is no universal loader for unrelated PyTorch, ONNX, TensorFlow or sklearn weights. The adapter is where you implement the exact architecture, channels, normalization, tiling and framework calls that your model requires. Avoid loading weights repeatedly: cache your initialized predictor in the adapter. The app never fits this model.

Alternatively, export `FLOOD_LENS_MODEL_ADAPTER=your_package.your_adapter`; that importable module must expose the two functions below. Keep framework imports/loading inside your implementation so a missing optional framework does not prevent threshold mode from starting. Metadata or import failures are surfaced by `/api/water-model`.

## Input and output contract

```python
def model_info() -> dict:
    return {
        "configured": True,
        "algorithm": "Your model name and version",
        "threshold": 0.5,
        "requires_vh": True,
        "training_datasets": ["names of datasets actually used"],
        "citations": ["their actual citations"],
        "limitations": ["measured accuracy and known input/terrain limitations"],
    }

def predict_water(vv_db, vh_db, valid_mask):
    # Load your model, preprocess, run inference and align to the input grid.
    # Return your actual float32 water-probability raster.
    raise NotImplementedError("Implement inference for your chosen model")
```

| Argument | Shape / meaning |
| --- | --- |
| `vv_db` | Float32 `(H, W)`: observed VV backscatter, locally terrain-corrected gamma0 converted to dB. |
| `vh_db` | Float32 `(H, W)` for available VH; `None` when absent. Set `requires_vh=False` only for a model that supports VV alone. |
| `valid_mask` | Boolean `(H, W)`: jointly observed pre/post radar after terrain screening; high-slope neighbors remain available as context. |
| Return value | Float32 `(H, W)` water probabilities in `[0, 1]`. Use NaN for pixels your model cannot assess. Invalid input pixels are excluded regardless of the returned values. |

The adapter is called once for the pre-event observation and once for the post-event observation. Predict **observed water**, including permanent water. The pipeline identifies new water by comparing those predictions and requiring radar change, slope eligibility and component size. This interface is not a multi-temporal change model or a debris classifier. Adapting either of those requires a different explicit output contract.

Do not treat the dB inputs as linear intensities, or assume gamma0 matches a model trained on sigma0. Resolve those preprocessing differences in your adapter and verify performance on representative Himalayan scenes. For fixed-size models, tile with appropriate overlap/padding and reconstruct a pixel-aligned full raster. Do not stretch output masks to a different geographic grid.

Wrong output shape, infinities, out-of-range probabilities, missing required VH and all-NaN predictions for observed inputs produce explicit errors. The wrapper excludes NaN predictions and never supplies fabricated predictions or an automatic threshold fallback.

## Metadata and evaluation

`configured` and `requires_vh` are Boolean; `algorithm` is a nonempty string; `threshold` is finite and strictly between 0 and 1. Citations, training datasets and limitations are lists of strings. All metadata must be JSON-compatible. You may include measured scores and weight checksums as additional metadata; these must describe your actual model. The app does not infer accuracy from file presence.

Only Sentinel-1/2, Copernicus DEM, pre-event OSM and approved training datasets are allowed. **Never read EMS, UNOSAT or published damage maps in the adapter**, and never use them for training or threshold selection. EMSR927 is loaded only by the separate evaluation tools after prediction. No reference geometry is supplied to `predict_water()`.

Until you configure the adapter, use threshold mode or explicitly labeled synthetic mode. These modes allow interface and geospatial testing; neither establishes your future model's accuracy. The existing model cards, PDFs and videos are historical baseline outputs and need regeneration for any new model.

Required attribution: Contains modified Copernicus Sentinel data 2026. Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. © OpenStreetMap contributors. Training references: Bountos et al., 2024 (Kuro Siwo), and Bonafilia et al., 2020 (Sen1Floods11); record the datasets actually used in your model metadata.
