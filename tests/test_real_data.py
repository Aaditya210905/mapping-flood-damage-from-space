from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import box, mapping

from app.geo import Grid, detect_changes
from app.models import AnalysisRequest
from app.providers import interval_snapshot
from app.public_data import Calibration, optical_pair


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
    result = detect_changes(radar, radar, grid, AnalysisRequest(water_model="external"), dem=np.zeros((11, 11)))
    assert result["valid"].sum() == 1
    assert result["valid"][5, 5]
