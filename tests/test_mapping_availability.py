"""Regression cases for missing observations and class-matched evaluation."""
from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from app.evaluation import binary_metrics, evaluate_run
from app.geo import Grid, detect_changes, masked_median
from app.models import AnalysisRequest, Scene
from app.pipeline import class_availability, run_analysis


def test_masked_median_does_not_invent_returns_next_to_nodata():
    grid = Grid("EPSG:32645", from_origin(400000, 3100000, 30, 30), 7, 7, 30)
    before = np.zeros((3, 7, 7))
    after = np.zeros_like(before)
    before[0, 3, 2:5] = .1
    after[0, 3, 2:5] = 10 ** (-23 / 10)
    before[1, 3, 2:5] = after[1, 3, 2:5] = 1
    out = detect_changes(before, after, grid, AnalysisRequest(case_id="trishuli", min_component_pixels=1))
    assert out["after_db"][3, 3] == pytest.approx(-23)
    assert out["before_db"][3, 3] == pytest.approx(-10)
    assert out["water"][3, 3]
    assert out["water_eligible"].sum() == 1  # End returns have only two supported samples.
    assert out["radar_valid"].sum() == 3  # Physical data availability is preserved separately.
    assert np.isnan(out["after_db"][2, 3])


def test_isolated_return_and_all_nodata_are_not_filter_supported():
    values = np.full((5, 5), np.nan)
    values[2, 2] = -25
    result, support = masked_median(values, np.isfinite(values))
    assert np.isnan(result).all()
    assert support[2, 2] == 1
    assert support[0, 0] == 0


def test_zero_reflectance_is_not_usable_clear_optical_coverage():
    grid = Grid("EPSG:32645", from_origin(400000, 3100000, 30, 30), 7, 7, 30)
    radar = np.stack([np.full((7, 7), .1), np.ones((7, 7)), np.zeros((7, 7))])
    optical = np.zeros((6, 7, 7))
    optical[4], optical[5] = 4, 1
    out = detect_changes(radar, radar, grid, AnalysisRequest(case_id="trishuli"), optical_before=optical, optical_after=optical)
    assert not out["optical_valid"].any()
    assert not out["debris_eligible"].any()


def test_availability_uses_each_class_domain_not_union():
    grid = Grid("EPSG:32645", from_origin(400000, 3100000, 30, 30), 5, 5, 30)
    water, debris = np.ones((5, 5), dtype=bool), np.zeros((5, 5), dtype=bool)
    result = class_availability({"water_eligible": water, "debris_eligible": debris}, water, grid, False)
    assert result["water"]["status"] == "available"
    assert result["debris"]["status"] == "unavailable"
    assert result["debris"]["coverage_pct"] == 0
    assert "optical" in result["debris"]["missing_reason"]


@pytest.mark.parametrize("radar_observed", [True, False])
def test_pipeline_missing_observations_are_null_not_zero(monkeypatch, tmp_path, radar_observed):
    params = AnalysisRequest(case_id="trishuli", bbox=[85.2, 28, 85.205, 28.005], satellite_provider="copernicus")
    def acquire(params, grid, folder, progress):
        radar = np.stack([np.full((grid.height, grid.width), .1), np.ones((grid.height, grid.width)), np.zeros((grid.height, grid.width))])
        if not radar_observed:
            radar[2] = 1  # Acquired radar lies entirely in excluded shadow.
        day = datetime.combine(params.flood_date, datetime.min.time(), tzinfo=timezone.utc)
        return {"before": radar, "after": radar.copy(), "dem": np.zeros((grid.height, grid.width)),
                "optical_before": None, "optical_after": None,
                "scenes": [Scene(id=f"test-{offset}", acquired=day + timedelta(days=offset), relative_orbit=19, direction="ascending") for offset in (-6, 6)],
                "snapshot": "2026-07-27T00:00:00Z", "optical_scenes": [],
                "osm": {"type": "FeatureCollection", "features": []}}
    monkeypatch.setattr("app.pipeline.acquire_live", acquire)
    run = run_analysis(params, "test-missing-optical", tmp_path)
    assert run["stats"]["water_km2"] == (0.0 if radar_observed else None)
    assert run["stats"]["debris_km2"] is None
    assert run["analysis_status"] == "partial"
    assert run["class_availability"]["debris"]["status"] == "unavailable"
    assert run["class_availability"]["water"]["status"] == ("available" if radar_observed else "unavailable")
    for field in ("buildings_exposed", "roads_exposed", "bridges_exposed", "road_exposed_km", "settlements_cut_off"):
        assert run["stats"][field] == (0 if radar_observed else None)
    assert run["stats"]["buildings_total"] == 0  # The known mapped inventory is retained.
    assert any("unknown, not zero" in warning for warning in run["warnings"])
    saved = json.loads((tmp_path / "result.json").read_text())
    assert saved["stats"]["debris_km2"] is None


@pytest.fixture
def evaluation_case(tmp_path):
    grid = Grid("EPSG:32645", from_origin(400000, 3100000, 30, 30), 10, 10, 30)
    classes = np.zeros((10, 10), dtype="uint8")
    classes[:, :5] = 2
    classes[:, 5:] = 3
    (tmp_path / "result.json").write_text(json.dumps({"id": "evaluation-test", "mode": "live"}))
    def write_raster(name, data):
        with rasterio.open(tmp_path / name, "w", driver="GTiff", width=10, height=10, count=data.shape[0],
                           dtype="uint8", transform=grid.transform, crs=grid.crs) as dst:
            dst.write(data)
    write_raster("change-mask.tif", classes[None])
    write_raster("eligibility.tif", np.ones((3, 10, 10), dtype="uint8"))
    def feature(left, right, props):
        geom = box(400000 + left * 30, 3099700, 400000 + right * 30, 3100000)
        return {"type": "Feature", "geometry": mapping(grid.geographic(geom)), "properties": props}
    def write_json(name, features):
        path = tmp_path / name
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return path
    coverage = write_json("coverage.geojson", [feature(0, 10, {})])
    return tmp_path, feature, write_json, write_raster, coverage


def test_both_empty_and_no_coverage_are_not_perfect_accuracy():
    empty = np.zeros((3, 3), dtype=bool)
    for valid in (np.ones_like(empty), empty):
        result = binary_metrics(empty, empty, valid)
        assert result["iou"] is None
        assert result["f1"] is None
        assert result["status"] == "unavailable"


def test_water_reference_does_not_produce_debris_accuracy(evaluation_case):
    folder, feature, write_json, _, coverage = evaluation_case
    reference = write_json("reference.geojson", [feature(0, 5, {"obj_desc": "Flooded area"})])
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    result = evaluate_run(folder, reference, coverage)
    assert result["metrics"]["iou"] == 1
    assert result['activation'] is None and result['attribution'] is None
    credited = evaluate_run(folder, reference, coverage, activation='custom-reference',reference_attribution='Independent reviewed data')
    assert credited['activation']=='custom-reference' and credited['attribution']=='Independent reviewed data'
    assert result["separate_classes"]["new_water"]["iou"] == 1
    assert result["separate_classes"]["debris_candidate"]["iou"] is None
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


def test_landslide_reference_never_scores_water_as_landslide(evaluation_case):
    folder, feature, write_json, _, coverage = evaluation_case
    reference = write_json("reference.geojson", [feature(5, 10, {"obj_desc": "Landslide"})])
    result = evaluate_run(folder, reference, coverage)
    assert result["metrics"]["iou"] == 1
    assert result["metrics"]["false_positive_pixels"] == 0
    assert result["water_validation"]["status"] == "unavailable"
    assert result["separate_classes"]["new_water"]["iou"] is None


def test_class_matched_mixed_reference_has_separate_scores(evaluation_case):
    folder, feature, write_json, _, coverage = evaluation_case
    reference = write_json("reference.geojson", [feature(0, 5, {"kind": "water"}), feature(5, 10, {"kind": "debris"})])
    result = evaluate_run(folder, reference, coverage)
    assert result["metrics"]["iou"] == 1
    assert result["separate_classes"]["new_water"]["iou"] == 1
    assert result["separate_classes"]["debris_candidate"]["iou"] == 1


def test_unknown_reference_requires_reviewed_class_mapping(evaluation_case):
    folder, feature, write_json, _, coverage = evaluation_case
    reference = write_json("reference.geojson", [feature(0, 5, {})])
    result = evaluate_run(folder, reference, coverage)
    assert result["status"] == "class_review_required"
    assert result["metrics"]["iou"] is None
    assert all(item["iou"] is None for item in result["separate_classes"].values())
    reviewed = evaluate_run(folder, reference, coverage, reference_class="water")
    assert reviewed["metrics"]["iou"] == 1


def test_unobserved_reference_positive_area_is_reported(evaluation_case):
    folder, feature, write_json, write_raster, coverage = evaluation_case
    reference = write_json("reference.geojson", [feature(5, 10, {"obj_desc": "Landslide"})])
    masks = np.ones((3, 10, 10), dtype="uint8")
    masks[2, :, 5:8] = 0
    write_raster("eligibility.tif", masks)
    result = evaluate_run(folder, reference, coverage)
    assert result["metrics"]["iou"] == 1
    assert result["reference_positive_evaluated_pct"] == 40
    assert result["valid_reviewed_coverage_pct"] == 70
    masks[2] = 0
    write_raster("eligibility.tif", masks)
    unavailable = evaluate_run(folder, reference, coverage)
    assert unavailable["status"] == "unavailable"
    assert unavailable["metrics"]["iou"] is None
    assert unavailable["reference_positive_evaluated_pct"] == 0


def test_missing_class_eligibility_does_not_borrow_union_coverage(evaluation_case):
    folder, feature, write_json, _, coverage = evaluation_case
    reference = write_json("reference.geojson", [feature(5, 10, {"kind": "debris"})])
    (folder / "eligibility.tif").unlink()
    result = evaluate_run(folder, reference, coverage)
    assert result["status"] == "unavailable"
    assert "eligibility" in result["reason"]
    assert result["metrics"]["iou"] is None
