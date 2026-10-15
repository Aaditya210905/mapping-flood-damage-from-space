"""Access scenarios must not turn missing observations into operational certainty."""
import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString, MultiLineString, Point, box, mapping

from app.geo import Grid
from app.impact import analyze_impacts


@pytest.fixture
def grid():
    return Grid('EPSG:32645', from_origin(400000, 3101000, 30, 30), 50, 50, 30)


def collection(grid, items):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'id': props.get('test_id', f'element-{index}'),
         'geometry': mapping(grid.geographic(geometry)), 'properties': props}
        for index, (geometry, props) in enumerate(items)]}


def road_case(grid, *, bridge=False, village_offset=0, start=400100):
    road = {'highway': 'primary', 'test_id': 'way/road'}
    if bridge:
        road['bridge'] = 'yes'
    osm = collection(grid, [
        (LineString([(start, 3100500), (400900, 3100500)]), road),
        (Point(400800, 3100500+village_offset), {'place': 'village', 'name': 'Village'}),
        (Point(400200, 3100500), {'amenity': 'hospital', 'name': 'Hospital'})])
    return osm


@pytest.mark.parametrize('highway',['motorway_link','trunk_link','primary_link','secondary_link','tertiary_link'])
def test_link_roads_preserve_a_mapped_route_to_a_hospital(grid,highway):
    osm=road_case(grid)
    osm['features'][0]['properties']['highway']=highway
    layers,stats=analyze_impacts(osm,collection(grid,[]),grid)
    assert stats['roads_total']==1
    assert layers['settlements']['features'][0]['properties']['connectivity_by_destination_type']['hospital']['status']=='connected'


def test_driving_profile_is_configurable_and_keeps_vehicle_restrictions(grid,monkeypatch):
    from app import impact
    monkeypatch.setattr(impact,'MAPPING',impact.MAPPING.model_copy(update={'drivable_highways':['service']}))
    osm=road_case(grid)
    assert analyze_impacts(osm,collection(grid,[]),grid)[1]['roads_total']==0
    osm['features'][0]['properties']['highway']='service'
    assert analyze_impacts(osm,collection(grid,[]),grid)[1]['roads_total']==1
    osm['features'][0]['properties']['motor_vehicle']='no'
    layers,stats=analyze_impacts(osm,collection(grid,[]),grid)
    assert stats['roads_total']==0
    assert layers['settlements']['features'][0]['properties']['status']=='unassessed'


def test_building_assessment_uses_whole_footprint_not_one_point(grid):
    mask = np.zeros((50, 50), dtype=bool)
    mask[:, :10] = True
    osm = collection(grid, [(box(400270, 3100600, 400330, 3100660), {'building': 'yes'})])
    layers, stats = analyze_impacts(osm, collection(grid, []), grid, assessment_mask=mask)
    building = layers['buildings']['features'][0]['properties']
    assert building['assessment_fraction'] == .5
    assert building['assessment_status'] == 'unassessed_or_partial'
    assert stats['buildings_unassessed_or_partial'] == 1


def test_multiline_assessment_is_length_weighted(grid):
    mask = np.zeros((50, 50), dtype=bool)
    mask[:, :10] = True
    line = MultiLineString([[(400100, 3100500), (400200, 3100500)], [(400400, 3100500), (400700, 3100500)]])
    layers, _ = analyze_impacts(collection(grid, [(line, {'highway': 'primary'})]), collection(grid, []), grid, assessment_mask=mask)
    assert layers['roads']['features'][0]['properties']['assessment_fraction'] == .25


def test_overall_assessment_needs_each_available_hazard_not_union(grid):
    water, debris = np.zeros((50, 50), dtype=bool), np.ones((50, 50), dtype=bool)
    water[:, :10] = True
    osm = collection(grid, [(box(400450, 3100600, 400480, 3100630), {'building': 'yes'})])
    layers, _ = analyze_impacts(osm, collection(grid, []), grid, assessment_masks={'water': water, 'debris': debris})
    props = layers['buildings']['features'][0]['properties']
    assert props['assessment_fraction'] == 0
    assert props['assessment_fraction_by_hazard'] == {'water': 0., 'debris': 1.}
    assert props['assessment_status'] == 'unassessed_or_partial'
    layers, _ = analyze_impacts(osm, collection(grid, []), grid, assessment_masks={'water': debris, 'debris': np.zeros_like(debris)})
    props = layers['buildings']['features'][0]['properties']
    assert props['assessment_fraction'] == 1
    assert props['unavailable_hazards'] == ['debris']
    assert props['assessment_scope'] == ['water']


def test_duplicate_osm_elements_are_not_counted_twice_and_bridge_outlines_recognized(grid):
    footprint = (box(400450, 3100600, 400480, 3100630), {'building': 'yes', 'test_id': 'way/1'})
    osm = collection(grid, [footprint, footprint, (box(400500, 3100600, 400530, 3100630), {'man_made': 'bridge', 'test_id': 'way/2'})])
    layers, stats = analyze_impacts(osm, collection(grid, []), grid)
    assert stats['buildings_total'] == 1
    assert stats['bridges_total'] == 1
    assert 'not a verified physical-object' in layers['bridges']['features'][0]['properties']['counting_unit']


def test_water_under_bridge_is_exposure_and_unknown_access_not_proven_failure(grid):
    extent = collection(grid, [(box(400480, 3100480, 400520, 3100520), {'kind': 'water'})])
    layers, stats = analyze_impacts(road_case(grid, bridge=True), extent, grid)
    assert stats['bridges_exposed'] == 1
    assert stats['settlements_cut_off'] == 0
    assert layers['settlements']['features'][0]['properties']['status'] == 'uncertain_access'
    assert layers['roads']['features'][0]['properties']['closure_candidate'] is False


def test_tiny_contact_does_not_assert_road_closure_and_threshold_is_explicit(grid):
    extent = collection(grid, [(box(400499, 3100480, 400501, 3100520), {'kind': 'water'})])
    layers, stats = analyze_impacts(road_case(grid), extent, grid)
    assert stats['settlements_cut_off'] == 0
    assert layers['settlements']['features'][0]['properties']['status'] == 'uncertain_access'
    assert layers['roads']['features'][0]['properties']['closure_min_length_m'] == 15


def test_surface_change_over_tunnel_does_not_prove_tunnel_closure(grid):
    osm = road_case(grid)
    osm['features'][0]['properties']['tunnel'] = 'yes'
    extent = collection(grid, [(box(400480, 3100480, 400520, 3100520), {'kind': 'debris'})])
    layers, stats = analyze_impacts(osm, extent, grid)
    assert stats['roads_exposed'] == 1
    assert stats['settlements_cut_off'] == 0
    assert layers['settlements']['features'][0]['properties']['status'] == 'uncertain_access'


def test_no_eligible_hazard_support_is_not_confirmed_clear_or_connected(grid):
    empty = np.zeros((50, 50), dtype=bool)
    layers, _ = analyze_impacts(road_case(grid), collection(grid, []), grid,
                               assessment_masks={'water': empty, 'debris': empty})
    props = layers['roads']['features'][0]['properties']
    assert props['assessment_fraction'] == 0
    assert props['assessment_complete'] is False
    assert props['assessment_scope'] == []
    assert props['unavailable_hazards'] == ['debris', 'water']
    assert layers['settlements']['features'][0]['properties']['status'] == 'uncertain_access'


def test_overlap_threshold_is_independent_of_graph_anchor_subdivision(grid):
    osm = road_case(grid)
    # The anchor divides a 20 m candidate crossing into two 10 m pieces.
    osm['features'][1]['geometry'] = mapping(grid.geographic(Point(400500, 3100500)))
    extent = collection(grid, [(box(400490, 3100480, 400510, 3100520), {'kind': 'water'})])
    layers, stats = analyze_impacts(osm, extent, grid)
    assert stats['settlements_cut_off'] == 1
    _, stats = analyze_impacts(osm, extent, grid, closure_min_length_m=30)
    assert stats['settlements_cut_off'] == 0


def test_distant_snaps_are_unassessed_and_nearby_connectors_stay_unverified(grid):
    layers, _ = analyze_impacts(road_case(grid, village_offset=500), collection(grid, []), grid)
    props = layers['settlements']['features'][0]['properties']
    assert props['status'] == 'unassessed'
    assert props['snap_status'] == 'outside_snap_limit'
    assert props['snap_limit_m'] == 100
    layers, _ = analyze_impacts(road_case(grid, village_offset=50), collection(grid, []), grid)
    props = layers['settlements']['features'][0]['properties']
    assert props['status'] == 'uncertain_access'
    assert props['snap_status'] == 'unverified_off_road_connector'
    assert props['snap_connector']['geometry']['type'] == 'LineString'


def test_hospital_and_town_routes_remain_separate_and_detour_compares_same_target(grid):
    osm = collection(grid, [
        (LineString([(400100, 3100500), (400500, 3100500), (401200, 3100500)]), {'highway': 'primary', 'test_id': 'way/main'}),
        (LineString([(400100, 3100500), (400100, 3100800), (400500, 3100800), (400500, 3100500)]), {'highway': 'secondary', 'test_id': 'way/bypass'}),
        (Point(400500, 3100500), {'place': 'village', 'name': 'Village'}),
        (Point(400100, 3100500), {'amenity': 'hospital', 'name': 'Hospital', 'test_id': 'node/hospital'}),
        (Point(401200, 3100500), {'place': 'town', 'name': 'Town', 'test_id': 'node/town'})])
    extent = collection(grid, [(box(400280, 3100480, 400320, 3100520), {'kind': 'water'})])
    layers, _ = analyze_impacts(osm, extent, grid)
    props = layers['settlements']['features'][0]['properties']
    assert props['before_destination_id'] == 'node/hospital'
    assert props['after_destination_id'] == 'node/town'
    assert props['before_distance_km'] == .4
    assert props['after_distance_km'] == .7
    assert props['same_destination_after_distance_km'] == 1.
    assert props['same_destination_extra_distance_km'] == .6
    hospital = props['connectivity_by_destination_type']['hospital']
    assert hospital['after_destination_type'] == 'hospital'
    assert hospital['after_distance_km'] == 1.
    assert hospital['after_route']['properties']['road_ids'] == ['way/bypass']
    assert props['connectivity_by_destination_type']['town']['before_distance_km'] == .7


def test_missing_hospital_does_not_substitute_town_access(grid):
    osm = road_case(grid)
    osm['features'][2]['properties'] = {'place': 'town', 'name': 'Town'}
    layers, _ = analyze_impacts(osm, collection(grid, []), grid)
    hospital = layers['settlements']['features'][0]['properties']['connectivity_by_destination_type']['hospital']
    assert hospital['status'] == 'unassessed'
    assert hospital['assessment_reason'] == 'no_mapped_destination'
    assert hospital['nearest_destination'] is None


def test_aoi_edge_can_hide_exterior_alternative_so_isolation_stays_uncertain(grid):
    extent = collection(grid, [(box(400480, 3100480, 400520, 3100520), {'kind': 'water'})])
    layers, stats = analyze_impacts(road_case(grid, start=400000), extent, grid)
    props = layers['settlements']['features'][0]['properties']
    assert props['boundary_limited'] is True
    assert props['status'] == 'uncertain_access'
    assert stats['settlements_cut_off'] == 0
