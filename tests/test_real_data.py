from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr
from rasterio.transform import from_origin
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
    params = AnalysisRequest(case_id="trishuli", bbox=[85, 27, 85.2, 27.2])
    def tile(day, geom, identifier):
        return {"id": identifier, "geometry": mapping(geom), "properties": {
            "datetime": day + "T00:00:00Z", "platform": "Sentinel-2C", "sat:relative_orbit": 119, "eo:cloud_cover": 80}}
    items = [tile(day, box(85, 27, 85.2, 27.1), day+"-south") for day in ["2026-08-25", "2026-08-26", "2026-08-27"]]
    items += [tile(day, box(85, 27.1, 85.2, 27.2), day+"-north") for day in ["2026-08-25", "2026-08-26", "2026-08-27"]]
    pre, post = optical_pair(items, params)
    assert len(pre) == len(post) == 2
    assert all(i["id"].startswith("2026-08-25") for i in pre)
    assert all(i["id"].startswith("2026-08-27") for i in post)


def test_radar_observations_and_water_slope_eligibility_are_separate(monkeypatch):
    from app import geo
    grid = Grid("EPSG:32645", from_origin(400000, 3100000, 30, 30), 11, 11, 30)
    slopes = np.full((11, 11), 40.)
    slopes[4:7, 4:7] = 0
    monkeypatch.setattr(geo, "slope_degrees", lambda dem, resolution: slopes)
    before = np.stack([np.full((11, 11), .1), np.ones((11, 11))])
    after = before.copy()
    after[0] = 10 ** (-23 / 10)
    result = detect_changes(before, after, grid, AnalysisRequest(case_id="trishuli"), dem=np.zeros((11, 11)))
    assert result["radar_valid"].all()
    assert result["valid"].sum() == 9
    assert result["valid"][5, 5]
    assert result["water"].sum() == 9
