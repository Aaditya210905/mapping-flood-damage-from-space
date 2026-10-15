from datetime import date, datetime, timezone

import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box, mapping

from app.evaluation import binary_metrics
from app.geo import Grid, detect_changes, trace_downhill
from app.impact import analyze_impacts
from app.models import AnalysisRequest, Scene, validate_pair
from app.providers import select_pair


@pytest.fixture
def grid():
    return Grid("EPSG:32645", from_origin(400000, 3101000, 30, 30), 30, 30, 30)


def radar(db, size=30):
    return np.stack([np.full((size, size), 10 ** (db / 10)), np.ones((size, size)), np.zeros((size, size))])


def test_new_water_excludes_permanent_water_and_nodata(grid):
    pre, post = radar(-10), radar(-10)
    pre[0, 4:10, 4:10] = post[0, 4:10, 4:10] = 10 ** (-23 / 10)
    post[0, 16:24, 16:24] = 10 ** (-23 / 10)
    post[0, 2:8, 19:25] = 10 ** (-23 / 10)
    post[1, 2:8, 19:25] = 0
    out = detect_changes(pre, post, grid, AnalysisRequest(case_id="trishuli", mode="demo"))
    assert out["permanent"][6, 6]
    assert not out["water"][6, 6]
    assert out["water"][20, 20]
    assert not out["water"][4, 21]
    assert out["classes"][4, 21] == 255


def test_shadow_and_steep_slopes_are_excluded(grid):
    pre, post = radar(-10), radar(-23)
    post[2, 5:10, 5:10] = 1
    out = detect_changes(pre, post, grid, AnalysisRequest(case_id="trishuli", mode="demo"))
    assert not out["water"][7, 7]
    dem = np.tile(np.arange(30) * 60., (30, 1))
    out = detect_changes(pre, post, grid, AnalysisRequest(case_id="trishuli", mode="demo"), dem=dem)
    assert not out["valid"].any()


def test_optical_cloud_snow_and_shadow_cannot_supply_debris(grid):
    pre, post = radar(-10), radar(-5)
    optical = np.stack([np.full((30,30), v) for v in [.15, .1, .5, .25, 4, 1]])
    after = optical.copy(); after[2] = .12; after[1] = .16
    after[4, :10] = 9; after[4, 10:20] = 11; after[4, 20:25] = 3
    out = detect_changes(pre, post, grid, AnalysisRequest(case_id="trishuli", mode="demo"), optical_before=optical, optical_after=after)
    assert not out["debris"][:25].any()
    assert out["debris"][27, 10]


def scene(when, orbit=19, platform="sentinel-1a"):
    return Scene(id=f"s-{when}", acquired=datetime.fromisoformat(when).replace(tzinfo=timezone.utc), relative_orbit=orbit, direction="ascending", platform=platform)


@pytest.mark.parametrize("after", [scene("2026-09-01", orbit=20), scene("2026-09-01", platform="sentinel-1c"), scene("2026-08-26")])
def test_incompatible_or_same_day_radar_pairs_are_rejected(after):
    with pytest.raises(ValueError):
        validate_pair(scene("2026-08-20"), after, date(2026, 8, 26))


def test_catalog_selects_compatible_track_with_coverage():
    def item(day, orbit):
        return {"id": day, "geometry": mapping(box(85, 27, 86, 29)), "properties": {"datetime": day + "T00:00:00Z", "sat:relative_orbit": orbit, "sat:orbit_state": "ascending", "platform": "sentinel-1a", "sar:polarizations": ["VV", "VH"], "sar:instrument_mode": "IW"}}
    before, after = select_pair([item("2026-08-20",19), item("2026-08-28",20), item("2026-09-01",19)], AnalysisRequest(case_id="trishuli"))
    assert before.relative_orbit == after.relative_orbit == 19
    assert after.acquired.date() == date(2026, 9, 1)


def fc(grid, geometries):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(grid.geographic(geom)), "properties": props} for geom, props in geometries]}


def road_fixture(grid, alternative=False, baseline_disconnected=False):
    x, y = 400100, 3100300
    objects = [(LineString([(x,y),(x+300,y)]), {"highway": "primary", "name": "Main road"}),
               (Point(x,y), {"amenity": "hospital", "name": "Hospital"}),
               (Point(x+300,y), {"place": "village", "name": "Village"})]
    if alternative:
        objects.append((LineString([(x,y),(x,y+200),(x+300,y+200),(x+300,y)]), {"highway":"secondary","name":"Bypass"}))
    if baseline_disconnected:
        objects.extend([(LineString([(x+600,y),(x+700,y)]), {"highway":"secondary"}), (Point(x+700,y), {"place":"village","name":"Already disconnected"})])
    return fc(grid, objects), fc(grid, [(box(x+140,y-15,x+160,y+15), {"kind":"water"})])


def test_removing_hit_road_causes_new_isolation(grid):
    osm, extent = road_fixture(grid)
    layers, stats = analyze_impacts(osm, extent, grid)
    assert stats["settlements_cut_off"] == 1
    village = layers["settlements"]["features"][0]["properties"]
    assert village["nearest_destination"] == "Hospital"
    assert village["before_distance_km"] == .3
    assert village["after_distance_km"] is None


def test_intact_alternative_road_preserves_connectivity(grid):
    osm, extent = road_fixture(grid, alternative=True)
    layers, stats = analyze_impacts(osm, extent, grid)
    assert stats["settlements_cut_off"] == 0
    assert layers["settlements"]["features"][0]["properties"]["after_distance_km"] == .7


def test_preexisting_disconnection_is_not_a_new_flood_impact(grid):
    osm, extent = road_fixture(grid, baseline_disconnected=True)
    _, stats = analyze_impacts(osm, extent, grid)
    assert stats["settlements_cut_off"] == 1
    assert stats["settlements_baseline_disconnected"] == 1


def test_geometry_crossings_do_not_create_false_road_junctions(grid):
    x, y = 400100, 3100300
    osm = fc(grid, [(LineString([(x,y),(x+400,y)]), {"highway":"primary"}), (LineString([(x+200,y-100),(x+200,y+100)]), {"highway":"secondary","bridge":"yes"}), (Point(x,y), {"amenity":"hospital","name":"Hospital"}), (Point(x+200,y+100), {"place":"village","name":"Village"})])
    _, stats = analyze_impacts(osm, {"type":"FeatureCollection","features":[]}, grid)
    assert stats["settlements_baseline_disconnected"] == 1
    assert stats["settlements_cut_off"] == 0


def test_settlement_snap_uses_segment_interior(grid):
    osm, extent = road_fixture(grid)
    # Place village halfway along the main road, beyond the mapped water crossing.
    osm["features"][2]["geometry"] = mapping(grid.geographic(Point(400350,3100300)))
    layers, stats = analyze_impacts(osm, extent, grid)
    assert stats["settlements_cut_off"] == 1
    assert layers["settlements"]["features"][0]["properties"]["before_distance_km"] == .25


def test_dem_trace_follows_downhill_and_rejects_outside(grid):
    dem = np.tile(np.arange(30,0,-1)[:,None], (1,30)).astype(float)
    point = grid.center(2,15)
    traced = trace_downhill(dem, grid, point.x, point.y, [])
    assert traced["properties"]["length_km"] > .5
    assert traced["properties"]["stopped_reason"] == "boundary"
    outside = grid.geographic(Point(grid.transform.c - 1, grid.transform.f - 50))
    with pytest.raises(ValueError):
        trace_downhill(dem, grid, outside.x, outside.y, [])


def test_metrics_exclude_unreviewed_pixels():
    prediction = np.array([1,1,0,1], dtype=bool)
    reference = np.array([1,0,1,0], dtype=bool)
    out = binary_metrics(prediction, reference, np.array([1,1,1,0], dtype=bool))
    assert out["iou"] == pytest.approx(1/3)
    assert out["precision"] == .5
    assert out["recall"] == .5
    assert out["f1"] == .5
