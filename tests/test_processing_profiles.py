"""Processing policies affect calculations, extraction scope and saved evidence."""
import json

import httpx
import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, mapping

from app import geo, impact, providers, public_data
from app.copilot import answer
from app.geo import Grid
from app.models import AnalysisRequest
from app.settings import MAPPING, MappingSettings, asset_selection_covers, LEGACY_ASSET_SELECTION


def configured(**updates):
    return MappingSettings.model_validate({**MAPPING.model_dump(), **updates})


@pytest.mark.parametrize('updates', [
    {'radar_median_window_pixels': 4},
    {'radar_median_window_pixels': 1, 'radar_median_minimum_support': 3},
    {'road_snap_distance_m': 0, 'road_on_network_tolerance_m': 1},
    {'settlement_places': ['village', 'village']},
    {'destination_profiles': {'all': MAPPING.destination_profiles['hospital'].model_dump()}},
    {'destination_profiles': {'clinic': {'label_en': 'Clinic', 'label_ne': 'क्लिनिक', 'keywords': ['clinic'],
                                        'tags': {'amenity': ['clinic) or building=*']}}}},
])
def test_invalid_processing_policies_fail_before_acquisition(updates):
    with pytest.raises(ValueError):
        configured(**updates)


def test_median_configuration_changes_support_and_keeps_invalid_centers_missing(monkeypatch):
    values = np.arange(49, dtype='float32').reshape(7, 7)
    valid = np.ones_like(values, dtype=bool)
    valid[1, 1] = False
    monkeypatch.setattr(geo, 'MAPPING', configured(radar_median_window_pixels=5, radar_median_minimum_support=20))
    filtered, support = geo.masked_median(values, valid)
    assert support[3, 3] == 24
    assert filtered[3, 3] == np.median(values[1:6, 1:6][valid[1:6, 1:6]])
    assert np.isnan(filtered[1, 1]) and np.isnan(filtered[0, 0])


def clinic_policy():
    return configured(destination_profiles={**MAPPING.model_dump()['destination_profiles'], 'clinic': {
        'label_en': 'Clinic', 'label_ne': 'क्लिनिक', 'keywords': ['clinic', 'clinics', 'क्लिनिक'],
        'tags': {'amenity': ['clinic']}}})


def test_custom_destination_profile_is_used_for_extraction_graph_inventory_and_chat(evidence_run, monkeypatch):
    policy = clinic_policy()
    assert 'clinic' in policy.osm_filter()
    assert not asset_selection_covers(LEGACY_ASSET_SELECTION, policy.asset_selection())
    grid = Grid('EPSG:32645', from_origin(400000, 3101000, 30, 30), 50, 50, 30)
    items = [(LineString([(400100, 3100500), (400900, 3100500)]), {'highway': 'primary'}),
             (Point(400800, 3100502), {'place': 'village', 'name': 'Test Village'}),
             (Point(400200, 3100500), {'amenity': 'clinic', 'name': 'Test Clinic'})]
    osm = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'id': f'element-{index}', 'geometry': mapping(grid.geographic(geom)), 'properties': props}
        for index, (geom, props) in enumerate(items)]}
    empty = {'type': 'FeatureCollection', 'features': []}
    monkeypatch.setattr(impact, 'MAPPING', policy)
    layers, stats = impact.analyze_impacts(osm, empty, grid)
    props = layers['settlements']['features'][0]['properties']
    assert props['connectivity_by_destination_type']['clinic']['status'] == 'uncertain_access'
    monkeypatch.setattr(impact, 'MAPPING', configured(**{**policy.model_dump(), 'road_on_network_tolerance_m': 3}))
    layers, stats = impact.analyze_impacts(osm, empty, grid)
    assert layers['settlements']['features'][0]['properties']['connectivity_by_destination_type']['clinic']['status'] == 'connected'
    assert layers['destinations']['features'][0]['properties']['destination_type'] == 'clinic'
    assert 'hospital' not in layers['destinations']['features'][0]['properties']['destination_types']
    evidence_run.update(layers={**evidence_run['layers'], **layers}, stats={**evidence_run['stats'], **stats})
    evidence_run['provenance']['mapping_configuration'] = policy.model_dump()
    monkeypatch.setenv('LLM_ENABLED', 'false')
    reply = answer(evidence_run, 'Which villages can reach a clinic?')
    assert reply['lists'][0]['total_count'] == 1
    assert reply['lists'][0]['filters']['destination_type'] == 'clinic'
    inventory = answer(evidence_run, 'How many clinics are mapped?')
    assert next(f['value'] for f in inventory['facts'] if f['field_name'] == 'inventory_count') == 1
    assert any(f['label_en'] == 'Mapped clinic elements' for f in inventory['facts'])


def test_asset_selection_change_invalidates_public_cache_but_timeout_does_not(monkeypatch):
    params = AnalysisRequest(bbox=[91.35, 23, 91.4, 23.05], flood_date='2024-08-21', mode='live')
    original = public_data.public_cache_key(params)
    monkeypatch.setattr(public_data, 'MAPPING', clinic_policy())
    assert public_data.public_cache_key(params) != original


def test_historical_extraction_uses_configured_selectors_and_records_scope(monkeypatch):
    policy = clinic_policy()
    monkeypatch.setattr(providers, 'MAPPING', policy)
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/metadata'):
            return httpx.Response(200, json={'temporalExtent': {'fromTimestamp': '2020-01-01T00:00:00Z',
                                                              'toTimestamp': '2024-08-01T00:00:00Z'}})
        return httpx.Response(200, json={'type': 'FeatureCollection', 'features': []})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    params = AnalysisRequest(bbox=[91.35, 23, 91.4, 23.05], flood_date='2024-08-21', mode='live')
    osm, _ = providers.osm_snapshot(params)
    assert 'clinic' in seen[1].content.decode()
    assert osm['metadata']['asset_selection'] == policy.asset_selection()
