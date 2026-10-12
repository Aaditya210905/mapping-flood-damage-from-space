from datetime import datetime, timezone
import json

import numpy as np
import pytest
import xarray as xr
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import box, mapping

from app.datasets import load_sample, validate_training_splits
from app.geo import Grid, detect_changes
from app.models import AnalysisRequest
from app.providers import interval_snapshot
from app.public_data import Calibration, optical_pair


def test_kuro_linear_units_and_ignore_label_are_preserved(tmp_path):
    intensity = np.array([[.01, .1], [1, .001]], dtype="float32")
    labels = np.array([[0, 1], [2, 3]], dtype="uint8")
    fields = {"flood_vv": intensity, "mask": labels, "valid_mask": np.ones((2, 2))}
    for name, values in fields.items():
        np.save(tmp_path / f"{name}.npy", values)
    vv, water, valid = load_sample({"dataset": "kuro_siwo", "files": {n: f"{n}.npy" for n in fields}}, tmp_path)
    np.testing.assert_allclose(vv, [[-20, -10], [0, -30]], atol=1e-5)
    np.testing.assert_array_equal(water, [[False, True], [True, False]])
    assert not valid[1, 1]  # class 3 is ignored even when SAR is observed


def test_chip_content_leakage_is_detected_even_when_ids_differ():
    train = {"dataset": "kuro_siwo", "split": "train", "event": "one", "id": "train-a", "sha256": {"flood_vv": "same", "mask": "same"}}
    test = {**train, "split": "test", "event": "two", "id": "test-b"}
    with pytest.raises(ValueError, match="Identical labeled chip"):
        validate_training_splits([train, test])
    test["sha256"] = {"flood_vv": "different", "mask": "different"}
    test["event"] = "one"
    with pytest.raises(ValueError, match="share a flood event"):
        validate_training_splits([train, test])


def test_history_interval_boundary_and_duplicate_versions():
    instant = datetime(2026, 7, 27, 9, tzinfo=timezone.utc)
    def feature(start, end):
        return {"properties": {"@osmId": "way/123", "@validFrom": start, "@validTo": end}}
    old = feature("2026-07-01T00:00:00Z", "2026-07-27T09:00:00Z")
    current = feature("2026-07-27T09:00:00Z", "2026-07-28T00:00:00Z")
    selected = interval_snapshot({"type": "FeatureCollection", "features": [old, current]}, instant)
    assert selected["features"] == [current]
    with pytest.raises(ValueError, match="Multiple"):
        interval_snapshot({"type": "FeatureCollection", "features": [current, current]}, instant)


def test_raw_dn_noise_and_calibration_are_in_power_units():
    calibration = Calibration.__new__(Calibration)
    calibration.beta = [(0, np.array([0, 1]), np.array([2, 2])), (1, np.array([0, 1]), np.array([2, 2]))]
    calibration.noise = [(0, np.array([0, 1]), np.array([4, 4])), (1, np.array([0, 1]), np.array([4, 4]))]
    calibration.azimuth = [(0, 1, 0, 1, np.array([0, 1]), np.array([.5, .5]))]
    calibration.already_denoised = False
    dn = xr.DataArray([[4, 0], [4, 4]], dims=("line", "pixel"), coords={"line": [0, 1], "pixel": [0, 1]})
    values = calibration.correct(dn).values
    assert values[0, 0] == pytest.approx((4**2 - 4*.5) / 2**2)
    assert np.isnan(values[0, 1])
    calibration.already_denoised = True
    assert calibration.correct(dn).values[1, 1] == 4


def test_sentinel2_mosaic_uses_same_acquisition_and_strict_event_dates():
    params = AnalysisRequest(bbox=[85, 27, 85.2, 27.2])
    def tile(day, geom, identifier):
        return {"id": identifier, "geometry": mapping(geom), "properties": {
            "datetime": day + "T00:00:00Z", "platform": "Sentinel-2C", "sat:relative_orbit": 119, "eo:cloud_cover": 80}}
    items = [tile(day, box(85, 27, 85.2, 27.1), day+"-south") for day in ["2026-08-25", "2026-08-26", "2026-08-27"]]
    items += [tile(day, box(85, 27.1, 85.2, 27.2), day+"-north") for day in ["2026-08-25", "2026-08-26", "2026-08-27"]]
    pre, post = optical_pair(items, params)
    assert len(pre) == len(post) == 2
    assert all(i["id"].startswith("2026-08-25") for i in pre)
    assert all(i["id"].startswith("2026-08-27") for i in post)


def test_steep_observed_neighbors_are_not_missing_model_context(monkeypatch):
    from app import geo, segmentation
    grid = Grid("EPSG:32645", from_origin(400000, 3100000, 30, 30), 11, 11, 30)
    slopes = np.full((11, 11), 40.)
    slopes[5, 5] = 0
    monkeypatch.setattr(geo, "slope_degrees", lambda dem, resolution: slopes)
    def predict(db, valid):
        usable = ndimage.minimum_filter(valid.astype("uint8"), size=5, mode="constant", cval=0).astype(bool)
        card = {k: [] for k in ["training_datasets", "citations", "limitations"]}
        card.update(algorithm="test model", training_pixels=1, training_chip_count=1, artifact_sha256="test", threshold=.5)
        return np.zeros(valid.shape), usable, card
    monkeypatch.setattr(segmentation, "water_probabilities", predict)
    radar = np.stack([np.full((11, 11), .1), np.ones((11, 11))])
    result = detect_changes(radar, radar, grid, AnalysisRequest(), dem=np.zeros((11, 11)))
    assert result["valid"].sum() == 1
    assert result["valid"][5, 5]
