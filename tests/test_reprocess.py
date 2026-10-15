"""Cache replay uses acquired source files only and never rewrites old runs."""
from datetime import datetime, timezone
import json
import uuid

import numpy as np
import pytest
import rasterio
from shapely.geometry import mapping

from app.geo import Grid
from app.models import AnalysisRequest, Scene
from app.pipeline import ANALYSIS_SOURCE_POLICY
from app.reprocess import CDSE_PROVIDER, load_cached_cdse_inputs, reprocess_saved_cdse
from app.storage import sha256


@pytest.fixture
def cdse_cache(tmp_path):
    source = tmp_path / str(uuid.uuid4())
    source.mkdir()
    params = AnalysisRequest(case_id="trishuli", bbox=[85.2, 28, 85.205, 28.005], satellite_provider="copernicus")
    grid = Grid.for_bbox(params.bbox, params.resolution_m)
    shape = grid.height, grid.width
    def raster(name, values, transform=None):
        with rasterio.open(source / name, "w", driver="GTiff", width=grid.width, height=grid.height,
                           count=values.shape[0], dtype="float32", crs=grid.crs, transform=transform or grid.transform) as dst:
            dst.write(values.astype("float32"))
    radar = np.stack([np.full(shape, .1), np.ones(shape), np.zeros(shape), np.full(shape, .01)])
    raster("s1-before.tif", radar)
    raster("s1-after.tif", radar)
    raster("dem.tif", np.zeros((1, *shape)))
    snapshot = "2026-07-27T00:00:00Z"
    osm = {"type": "FeatureCollection", "metadata": {"sourceApi": "https://api.ohsome.org/v1", "snapshot": snapshot},
           "features": [{"type": "Feature", "geometry": mapping(grid.center(5, 5)),
                         "properties": {"@osmId": "node/123", "@version": 1, "@validFrom": "2026-01-01T00:00:00Z",
                                        "@validTo": "2026-08-01T00:00:00Z", "place": "village", "name": "Test settlement"}}]}
    (source / "osm-snapshot.geojson").write_text(json.dumps(osm))
    scenes = [Scene(id=f"S1A_IW_GRDH_1SDV_{stamp}_{stamp}_000000_000000_0000_COG",
                    acquired=datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc),
                    relative_orbit=19, direction="ascending") for stamp in ("20260820T000000", "20260901T000000")]
    run = {"id": source.name, "name": params.name, "mode": "live", "bbox": params.bbox,
           "created_at": "2026-10-14T00:00:00Z", "flood_date": str(params.flood_date),
           "parameters": params.model_dump(mode="json"),
           "provenance": {"radar_scenes": [scene.model_dump(mode="json") for scene in scenes],
                          "acquisition": {"provider": CDSE_PROVIDER, "radar_scene_ids": [scene.id for scene in scenes],
                                          "processing": "original CDSE gamma0 terrain processing",
                                          "dem": {"provider": "Copernicus Data Space Sentinel Hub", "product": "COPERNICUS_30"}},
                          "source_policy": ANALYSIS_SOURCE_POLICY, "osm_snapshot": snapshot,
                          "optical_scenes": [], "optical_observations": [], "crs": grid.crs,
                          "resolution_m": grid.resolution, "grid_bounds": grid.bounds},
           "stats": {"water_km2": 999999999}, "layers": {"extent": "POISON: MUST NOT BE USED"},
           "evaluation": {"reference_prediction": "POISON: MUST NOT BE USED"}}
    (source / "result.json").write_text(json.dumps(run))
    (source / "change-mask.tif").write_bytes(b"not a raster; prediction is not an input")
    (source / "extent.geojson").write_bytes(b"not JSON; prediction is not an input")
    return source, run, grid, raster


def test_reprocessing_uses_only_frozen_acquired_inputs_and_preserves_source(cdse_cache, tmp_path, monkeypatch):
    source, saved, _, _ = cdse_cache
    original_hashes = {path.name: sha256(path) for path in source.iterdir()}
    def no_acquisition(*args):
        raise AssertionError("No network acquisition may occur during cache replay")
    monkeypatch.setattr("app.pipeline.acquire_live", no_acquisition)
    monkeypatch.setattr("app.pipeline.acquire_public", no_acquisition)
    destination = tmp_path / str(uuid.uuid4())
    run = reprocess_saved_cdse(source, destination)
    assert run["id"] != saved["id"]
    assert run["mode"] == "live"
    assert run["acquisition_mode"] == "cached_reprocessing"
    assert run["stats"]["water_km2"] == 0
    assert run["stats"]["debris_km2"] is None
    assert run["evaluation"]["status"] == "pending"
    assert run["provenance"]["acquisition"] == saved["provenance"]["acquisition"]
    audit = run["provenance"]["reprocessing"]
    assert audit["source_run_id"] == saved["id"]
    assert audit["source_result_sha256"] == original_hashes["result.json"]
    assert audit["new_acquisition"] is False
    assert set(audit["input_sha256"]) == {"s1-before.tif", "s1-after.tif", "dem.tif", "osm-snapshot.geojson"}
    assert {path.name: sha256(path) for path in source.iterdir()} == original_hashes
    assert all(sha256(destination / name) == digest for name, digest in audit["input_sha256"].items())


def test_broader_asset_profile_requires_new_acquisition_and_preserves_old_cache(cdse_cache, monkeypatch):
    from app import reprocess
    from app.settings import MAPPING, MappingSettings
    source, _, _, _ = cdse_cache
    original_hashes = {path.name: sha256(path) for path in source.iterdir()}
    policy = MAPPING.model_dump()
    policy['destination_profiles']['clinic'] = {'label_en': 'Clinic', 'label_ne': 'क्लिनिक',
        'keywords': ['clinic'], 'tags': {'amenity': ['clinic']}}
    monkeypatch.setattr(reprocess, 'MAPPING', MappingSettings.model_validate(policy))
    with pytest.raises(ValueError, match='exceeds the recorded OSM extract'):
        reprocess.load_cached_cdse_inputs(source)
    assert {path.name: sha256(path) for path in source.iterdir()} == original_hashes


@pytest.mark.parametrize("mutation", ["demo", "public", "policy", "orbit", "grid", "scene_time", "post_osm", "dem_source"])
def test_reprocessing_refuses_incompatible_or_unapproved_source_metadata(cdse_cache, mutation):
    source, saved, _, _ = cdse_cache
    if mutation == "demo":
        saved["mode"] = "demo"
    elif mutation == "public":
        saved["parameters"]["satellite_provider"] = "public"
    elif mutation == "policy":
        saved["provenance"]["source_policy"] = "External maps allowed"
    elif mutation == "orbit":
        saved["provenance"]["radar_scenes"][1]["relative_orbit"] = 21
    elif mutation == "grid":
        saved["provenance"]["grid_bounds"][0] += 30
    elif mutation == "scene_time":
        saved["provenance"]["radar_scenes"][0]["acquired"] = "2026-08-21T00:00:00Z"
    elif mutation == "post_osm":
        saved["provenance"]["osm_snapshot"] = "2026-08-27T00:00:00Z"
    elif mutation == "dem_source":
        saved["provenance"]["acquisition"]["dem"]["provider"] = "Unapproved external elevation"
    (source / "result.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        load_cached_cdse_inputs(source)


def test_reprocessing_refuses_raster_grid_mismatch(cdse_cache):
    source, _, grid, raster = cdse_cache
    raster("s1-after.tif", np.ones((4, grid.height, grid.width)), grid.transform * rasterio.Affine.translation(1, 0))
    with pytest.raises(ValueError, match="grid or band"):
        load_cached_cdse_inputs(source)


def test_reprocessing_refuses_missing_recorded_optical_pair(cdse_cache):
    source, saved, _, _ = cdse_cache
    saved["provenance"]["optical_scenes"] = ["S2-before", "S2-after"]
    saved["provenance"]["optical_observations"] = [{"id": "S2-before", "acquired": "2026-08-25T00:00:00Z"},
                                                  {"id": "S2-after", "acquired": "2026-08-27T00:00:00Z"}]
    (source / "result.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="missing a regular input"):
        load_cached_cdse_inputs(source)


def test_reprocessing_refuses_unrecorded_optical_files(cdse_cache):
    source, _, grid, raster = cdse_cache
    raster("s2-before.tif", np.ones((6, grid.height, grid.width)))
    with pytest.raises(ValueError, match="Unrecorded optical"):
        load_cached_cdse_inputs(source)


def test_reprocessing_keeps_recorded_clear_optical_inputs(cdse_cache):
    source, saved, grid, raster = cdse_cache
    saved["provenance"]["optical_scenes"] = ["S2-before", "S2-after"]
    saved["provenance"]["optical_observations"] = [{"id": "S2-before", "acquired": "2026-08-25T00:00:00Z"},
                                                  {"id": "S2-after", "acquired": "2026-08-27T00:00:00Z"}]
    (source / "result.json").write_text(json.dumps(saved))
    optical = np.stack([np.full((grid.height, grid.width), value) for value in (.15, .1, .5, .25, 4, 1)])
    for name in ("s2-before.tif", "s2-after.tif"):
        raster(name, optical)
    _, inputs = load_cached_cdse_inputs(source)
    assert inputs["optical_scenes"] == ["S2-before", "S2-after"]
    assert np.array_equal(inputs["optical_before"], optical.astype("float32"))
    assert "s2-after.tif" in inputs["reprocessing"]["input_sha256"]


def test_reprocessing_refuses_output_overwrite_and_old_identity(cdse_cache, tmp_path):
    source, saved, _, _ = cdse_cache
    existing = tmp_path / "existing"
    existing.mkdir()
    for destination in (source, source / "nested", existing):
        with pytest.raises(ValueError, match="new separate output"):
            reprocess_saved_cdse(source, destination)
    with pytest.raises(ValueError, match="new run ID"):
        reprocess_saved_cdse(source, tmp_path / "new", saved["id"])


def test_reprocessing_accepts_recorded_multi_tile_optical_epochs(cdse_cache):
    source,saved,grid,raster=cdse_cache
    observations=[{'id':f'CDSE-SH-{index}','acquired':instant,'epoch':epoch}
                  for index,(instant,epoch) in enumerate([
                      ('2026-08-25T00:01:00Z','before'),('2026-08-25T00:02:00Z','before'),
                      ('2026-08-27T00:01:00Z','after'),('2026-08-27T00:02:00Z','after')])]
    saved['provenance']['optical_observations']=observations
    saved['provenance']['optical_scenes']=[record['id'] for record in observations]
    (source/'result.json').write_text(json.dumps(saved))
    optical=np.stack([np.full((grid.height,grid.width),value) for value in (.15,.1,.5,.25,4,1)])
    for name in ('s2-before.tif','s2-after.tif'):raster(name,optical)
    _,inputs=load_cached_cdse_inputs(source)
    assert inputs['optical_scenes']==saved['provenance']['optical_scenes']
    assert np.array_equal(inputs['optical_after'],optical.astype('float32'))


def test_reprocessing_refuses_osm_version_outside_snapshot(cdse_cache):
    source, _, _, _ = cdse_cache
    path = source / "osm-snapshot.geojson"
    osm = json.loads(path.read_text())
    osm["features"][0]["properties"]["@validFrom"] = "2026-08-01T00:00:00Z"
    path.write_text(json.dumps(osm))
    with pytest.raises(ValueError, match="not valid"):
        load_cached_cdse_inputs(source)
def test_reprocessing_accepts_the_supported_historical_v2_source(cdse_cache):
    source, _, _, _ = cdse_cache
    path = source / 'osm-snapshot.geojson'
    osm = json.loads(path.read_text())
    osm['metadata']['sourceApi'] = 'https://api.heigit.org/ohsome-api/v2-rc'
    path.write_text(json.dumps(osm))
    _, inputs = load_cached_cdse_inputs(source)
    assert inputs['osm']['metadata']['sourceApi'] == 'https://api.heigit.org/ohsome-api/v2-rc'


def test_replay_accepts_explicitly_configured_source_endpoints(cdse_cache,monkeypatch):
    from app import reprocess
    source,saved,_,_=cdse_cache
    policy=reprocess.ACQUISITION.model_copy(update={'public_dem_base_url':'https://dem.example/glo30',
        'osm_url':'https://history.example/v1'})
    monkeypatch.setattr(reprocess,'ACQUISITION',policy)
    saved['provenance']['acquisition']['dem']={'provider':'Public Copernicus DEM GLO-30 mirror',
        'tiles':['https://dem.example/glo30/mock-tile.tif']}
    (source/'result.json').write_text(json.dumps(saved))
    osm_path=source/'osm-snapshot.geojson'
    osm=json.loads(osm_path.read_text());osm['metadata']['sourceApi']=policy.osm_url
    osm_path.write_text(json.dumps(osm))
    _,inputs=load_cached_cdse_inputs(source)
    assert inputs['osm']['metadata']['sourceApi']==policy.osm_url
