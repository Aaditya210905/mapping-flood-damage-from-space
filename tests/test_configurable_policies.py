"""Synthetic/mocked policy regressions; no CDSE, Groq or reference downloads."""
import json
from pathlib import Path
import subprocess
import sys

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString,Point,box,mapping
from shapely.strtree import STRtree

from app import cdse_runtime,providers,public_data,reference
from app.asset_evaluation import evaluate_assets,match_asset
from app.geo import Grid
from app.models import AnalysisRequest
from app.settings import ACQUISITION,EVALUATION,EvaluationSettings,MappingSettings,MAPPING
from app.storage import sha256
from scripts.fetch_reference import fetch_reference


def install_http(monkeypatch,handler):
    original=httpx.Client
    options=[]
    def client(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(httpx,'Client',client)
    return options


def test_reference_activations_get_separate_directories_and_reject_cache_relabeling(tmp_path,monkeypatch):
    monkeypatch.setattr(reference,'REFERENCE_ROOT',tmp_path)
    first=reference.reference_directory('EMSR123');second=reference.reference_directory('emsr456')
    assert first==tmp_path/'EMSR123' and second==tmp_path/'EMSR456'
    first.mkdir()
    manifest=first/'manifest.json';manifest.write_text(json.dumps({'activation':'EMSR123'}))
    before=manifest.read_bytes()
    assert reference.reference_directory('EMSR123',first)==first
    with pytest.raises(ValueError,match='different activation'):reference.reference_directory('EMSR456',first)
    assert manifest.read_bytes()==before


def test_reference_download_requires_explicit_activation_before_network():
    result=subprocess.run([sys.executable,'scripts/fetch_reference.py'],capture_output=True,text=True)
    assert result.returncode==2 and '--activation' in result.stderr


def test_asset_cli_requires_an_explicit_reference_before_reading_run():
    result=subprocess.run([sys.executable,'scripts/evaluate_assets.py','not-a-run'],capture_output=True,text=True)
    assert result.returncode==2 and '--reference' in result.stderr


@pytest.mark.parametrize('record',['[]','{"activation":"EMSR456"}'])
def test_invalid_or_foreign_reference_directory_is_preserved(tmp_path,record):
    marker=tmp_path/'manifest.json';marker.write_text(record)
    with pytest.raises(ValueError):reference.reference_directory('EMSR123',tmp_path)
    assert marker.read_text()==record


def test_reference_fetch_uses_requested_code_and_derived_folder(tmp_path,monkeypatch):
    monkeypatch.setattr(reference,'REFERENCE_ROOT',tmp_path)
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'count':1,'results':[{'code':request.url.params['code'],'aois':[]}]})
    install_http(monkeypatch,handler)
    first=fetch_reference('EMSR123');second=fetch_reference('EMSR456')
    assert first['output']!=second['output']
    assert [request.url.params['code'] for request in requests]==['EMSR123','EMSR456']
    for code in ('EMSR123','EMSR456'):
        assert json.loads((tmp_path/code/'manifest.json').read_text())['activation']==code
    recorded=(tmp_path/'EMSR123'/'manifest.json').read_bytes()
    with pytest.raises(ValueError,match='different activation'):fetch_reference('EMSR456',tmp_path/'EMSR123')
    assert len(requests)==2 and (tmp_path/'EMSR123'/'manifest.json').read_bytes()==recorded


def test_wrong_remote_activation_is_rejected_before_creating_output(tmp_path,monkeypatch):
    install_http(monkeypatch,lambda request:httpx.Response(200,json={'count':1,'results':[{'code':'EMSR456','aois':[]}]}))
    output=tmp_path/'reference'
    with pytest.raises(ValueError,match='requested activation'):fetch_reference('EMSR123',output)
    assert not output.exists()


def test_public_catalog_uses_configured_endpoint_page_policy_and_get_continuation(monkeypatch):
    policy=ACQUISITION.model_copy(update={'public_stac_url':'https://catalog.example/search',
        'public_catalog_page_size':7,'public_catalog_max_pages':2,'public_catalog_timeout_seconds':9})
    monkeypatch.setattr(public_data,'ACQUISITION',policy)
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,json={'features':[{'id':'mock-a'}],
                'links':[{'rel':'next','method':'GET','href':'?page=two'}]})
        return httpx.Response(200,json={'features':[{'id':'mock-a'},{'id':'mock-b'}]})
    options=install_http(monkeypatch,handler)
    params=AnalysisRequest(bbox=[10,10,10.05,10.05],flood_date='2024-08-21')
    assert public_data.catalog(params,'sentinel-1-grd')==[{'id':'mock-a'},{'id':'mock-b'}]
    assert [request.method for request in calls]==['POST','GET']
    assert calls[0].url.host=='catalog.example' and json.loads(calls[0].content)['limit']==7
    assert calls[1].url.params['page']=='two' and options[0]['timeout']==9


def test_public_page_limit_and_origin_guard_are_enforced(monkeypatch):
    policy=ACQUISITION.model_copy(update={'public_catalog_max_pages':1})
    monkeypatch.setattr(public_data,'ACQUISITION',policy)
    params=AnalysisRequest(bbox=[10,10,10.05,10.05],flood_date='2024-08-21')
    target=[policy.public_stac_url+'?page=2']
    install_http(monkeypatch,lambda request:httpx.Response(200,json={'features':[],
        'links':[{'rel':'next','method':'GET','href':target[0]}]}))
    with pytest.raises(ValueError,match='page limit'):public_data.catalog(params,'sentinel-1-grd')
    target[0]='https://other.example/search'
    with pytest.raises(ValueError,match='service origin'):public_data.catalog(params,'sentinel-1-grd')


def test_public_signing_respects_configured_endpoint_timeout_and_retry_limit(monkeypatch):
    monkeypatch.setattr(public_data,'ACQUISITION',ACQUISITION.model_copy(update={
        'public_sign_url':'https://sign.example/asset','public_sign_timeout_seconds':7,
        'public_http_retries':1,'public_max_retry_wait_seconds':0}))
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(429,headers={'retry-after':'0'}) if len(calls)==1 else httpx.Response(200,json={'href':'https://asset.example/mock.tif?mock-signature=1'})
    options=install_http(monkeypatch,handler)
    assert public_data.signed_url('https://asset.example/mock.tif').endswith('mock-signature=1')
    assert len(calls)==2 and calls[0].url.host=='sign.example' and options[0]['timeout']==7


def test_historical_read_retry_policy_does_not_extend_cdse_billable_retries(monkeypatch):
    monkeypatch.setattr(providers,'ACQUISITION',ACQUISITION.model_copy(update={'osm_http_retries':1,'osm_max_retry_wait_seconds':0}))
    calls=[]
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('Mock transport timeout',request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):providers.historical_request(client,'GET','https://history.example/read')
        assert len(calls)==2
        calls.clear()
        with pytest.raises(httpx.ReadTimeout):cdse_runtime.request_with_retry(client,'POST',providers.PROCESS_URL)
        assert len(calls)==1


def test_public_cache_changes_with_observation_policy_but_not_timeout(monkeypatch):
    params=AnalysisRequest(bbox=[10,10,10.05,10.05],flood_date='2024-08-21')
    original=public_data.public_cache_key(params)
    assert public_data.public_cache_key(params.model_copy(update={'search_days':36}))!=original
    monkeypatch.setattr(public_data,'ACQUISITION',ACQUISITION.model_copy(update={'public_catalog_timeout_seconds':11}))
    assert public_data.public_cache_key(params)==original
    monkeypatch.setattr(public_data,'ACQUISITION',ACQUISITION.model_copy(update={'public_dem_base_url':'https://mirror.example/dem'}))
    assert public_data.public_cache_key(params)!=original


@pytest.mark.parametrize('changes',[{'road_match_buffer_m':0},{'road_match_min_overlap':1.1},{'asset_match_distance_m':-1},{'observation_threshold':0}])
def test_invalid_matching_policy_is_rejected(changes):
    with pytest.raises(ValueError):EvaluationSettings.model_validate({**EVALUATION.model_dump(),**changes})


def test_matching_policy_changes_actual_road_and_point_matches():
    roads=[LineString([(0,0),(100,0)])];tree=STRtree(roads)
    reference_road=LineString([(0,25),(100,25)])
    assert match_asset(reference_road,'roads',roads,tree,EVALUATION)==0
    assert match_asset(reference_road,'roads',roads,tree,EVALUATION.model_copy(update={'road_match_buffer_m':10})) is None
    assert match_asset(Point(0,40),'buildings',[Point(0,0)],STRtree([Point(0,0)]),EVALUATION)==0
    assert match_asset(Point(0,40),'buildings',[Point(0,0)],STRtree([Point(0,0)]),EVALUATION.model_copy(update={'asset_match_distance_m':30})) is None
    short=[LineString([(0,0),(40,0)])];whole=LineString([(0,0),(100,0)])
    strict=EVALUATION.model_copy(update={'road_match_buffer_m':1,'road_match_min_overlap':.5})
    assert match_asset(whole,'roads',short,STRtree(short),strict) is None
    assert match_asset(whole,'roads',short,STRtree(short),strict.model_copy(update={'road_match_min_overlap':.3}))==0


def test_asset_evaluation_records_effective_policy_and_keeps_prediction_bytes(tmp_path):
    folder=tmp_path/'prediction';folder.mkdir();reference_folder=tmp_path/'reference';reference_folder.mkdir()
    grid=Grid('EPSG:32645',from_origin(400000,3101000,30,30),10,10,30)
    building=grid.geographic(box(400030,3100800,400060,3100830))
    reference_geometry=grid.geographic(box(400090,3100800,400120,3100830))
    fc=lambda features:{'type':'FeatureCollection','features':features}
    feature={'type':'Feature','geometry':mapping(building),'properties':{'id':'mock-building','exposed':True}}
    extent=fc([{'type':'Feature','geometry':mapping(building),'properties':{'kind':'debris'}}])
    run={'id':'mock-asset-policy-run','mode':'live','bbox':list(grid.geographic(box(*grid.bounds)).bounds),
        'layers':{'buildings':fc([feature]),'roads':fc([]),'bridges':fc([]),'extent':extent}}
    (folder/'result.json').write_text(json.dumps(run));(folder/'extent.geojson').write_text(json.dumps(extent))
    for name,count,value in [('change-mask.tif',1,3),('eligibility.tif',3,1)]:
        with rasterio.open(folder/name,'w',driver='GTiff',width=10,height=10,count=count,dtype='uint8',crs=grid.crs,transform=grid.transform) as output:
            output.write(np.full((count,10,10),value,dtype='uint8'))
    (reference_folder/'mock_builtupP.geojson').write_text(json.dumps(fc([{'type':'Feature','geometry':mapping(reference_geometry),'properties':{'damage_gra':'Damaged'}}])))
    (reference_folder/'manifest.json').write_text(json.dumps({'activation':'EMSR123','attribution':'Synthetic reference fixture; not official observations',
        'products':[{'monitoring':False,'aoi':1,'product_id':1,'layers':[{'file':'mock_builtupP.geojson'}]}]}))
    before={path.name:sha256(path) for path in folder.iterdir()}
    matched=evaluate_assets(folder,reference_folder)
    assert matched['scores']['buildings']['tp']==1 and matched['configuration']==EVALUATION.model_dump()
    policy=EVALUATION.model_copy(update={'asset_match_distance_m':25,'observation_threshold':.9})
    unmatched=evaluate_assets(folder,reference_folder,policy)
    assert unmatched['scores']['buildings']['unmatched']==1 and unmatched['scores']['buildings']['f1'] is None
    assert unmatched['configuration']==policy.model_dump() and '25m' in unmatched['method']
    assert matched['reference_sha256']['mock_builtupP.geojson']==sha256(reference_folder/'mock_builtupP.geojson')
    assert matched['prediction_unchanged'] and unmatched['prediction_unchanged']
    assert {path.name:sha256(path) for path in folder.iterdir()}==before


def test_driving_configuration_rejects_duplicate_or_invalid_classes():
    for values in (['primary','primary'],['primary;ignored']):
        with pytest.raises(ValueError):MappingSettings.model_validate({**MAPPING.model_dump(),'drivable_highways':values})
