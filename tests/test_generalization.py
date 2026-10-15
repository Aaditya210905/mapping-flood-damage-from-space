"""Generalization, acquisition provenance and quota controls; synthetic/mocked only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from io import BytesIO
import json
import threading
import tarfile

from fastapi.testclient import TestClient
import httpx
import numpy as np
from pydantic import ValidationError
import pytest
from rasterio.io import MemoryFile
from shapely.geometry import box,mapping

from app import providers,main,cdse_runtime,copilot_cache
from app.copilot import answer
from app.copilot_types import AnswerDraft,AnswerSection,NarrativeSentence
from app.geo import Grid,detect_changes
from app.grounding import claim_for
from app.llm import ProviderFailure
from app.models import AnalysisRequest
from app.pipeline import run_analysis
from app.scene_selection import optical_pair
from app.settings import satellite_provider


def params(**changes):
    return AnalysisRequest(bbox=[10,10,10.05,10.05],flood_date='2024-08-21',**changes)


@pytest.mark.parametrize('body',[{}, {'bbox':[10,10,10.05,10.05]}, {'flood_date':'2024-08-21'}])
def test_live_input_cannot_silently_select_nepal(body,tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path)
    with TestClient(main.app) as client:
        assert client.post('/api/analyses',json=body).status_code==422
    assert not list(tmp_path.glob('*/request.json'))


def test_presets_are_opt_in_and_custom_requests_have_no_case_label():
    assert params().name=='Custom flood analysis'
    assert params().case_id is None
    assert AnalysisRequest(case_id='trishuli').name!='Custom flood analysis'
    with pytest.raises(ValidationError,match='Unknown preset'):
        AnalysisRequest(case_id='not-a-configured-preset')
    from app.presets import PRESETS,osm_cutoff
    preset=PRESETS['trishuli']
    assert osm_cutoff(AnalysisRequest(bbox=preset['bbox'],flood_date=preset['flood_date'])) is None


def test_auto_provider_uses_cdse_and_does_not_change_on_acquisition_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('CDSE_CLIENT_ID','synthetic-client')
    monkeypatch.setenv('CDSE_CLIENT_SECRET','synthetic-secret')
    captured=[]
    def cdse(request,*args):
        captured.append(request.satellite_provider)
        raise ValueError('Synthetic upstream failure')
    monkeypatch.setattr('app.pipeline.acquire_live',cdse)
    monkeypatch.setattr('app.pipeline.acquire_public',lambda *args:pytest.fail('No public fallback after a CDSE error'))
    with pytest.raises(ValueError,match='Synthetic upstream failure'):
        run_analysis(params(), 'synthetic-validation',tmp_path)
    assert captured==['copernicus']
    assert satellite_provider('public')=='public'
    monkeypatch.delenv('CDSE_CLIENT_SECRET')
    with pytest.raises(ValueError,match='both CDSE'):satellite_provider('auto')
    monkeypatch.delenv('CDSE_CLIENT_ID')
    assert satellite_provider('auto')=='public'


def item(day,west,east,minute=0,platform='sentinel-2a',orbit=3,cloud=95):
    return {'id':f'SYNTHETIC-{day}-{west}-{minute}-{platform}-{orbit}',
            'geometry':mapping(box(west,10,east,10.05)),
            'properties':{'datetime':f'{day}T12:{minute:02d}:00Z','platform':platform,
                          'sat:relative_orbit':orbit,'eo:cloud_cover':cloud,
                          'sat:orbit_state':'ascending','sar:polarizations':['VV','VH'],'sar:instrument_mode':'IW'}}


def test_optical_tile_edges_and_cloudy_metadata_do_not_hide_real_aoi_samples():
    records=[record for day in ('2024-08-10','2024-08-22')
             for record in (item(day,10,10.03),item(day,10.025,10.05,minute=2))]
    pair=optical_pair(records,params())
    assert [len(pass_tiles) for pass_tiles in pair]==[2,2]
    assert all(tile['properties']['eo:cloud_cover']==95 for tiles in pair for tile in tiles)


@pytest.mark.parametrize('changes',[{'platform':'sentinel-2b'},{'orbit':4},{'minute':30}])
def test_optical_mosaic_never_combines_incompatible_passes(changes):
    pair=optical_pair([item('2024-08-10',10,10.03),item('2024-08-10',10.025,10.05,**changes)],params())
    assert pair==[None,None]


def test_cdse_radar_mosaics_keep_same_platform_track_and_each_source_id():
    records=[record for day in ('2024-08-10','2024-08-22')
             for record in (item(day,10,10.03,platform='sentinel-1a'),item(day,10.025,10.05,minute=1,platform='sentinel-1a'))]
    with pytest.raises(ValueError,match='No same-platform'):providers.select_pair(records,params())
    pair=providers.select_pair(records,params(),allow_mosaics=True)
    assert [len(scene.catalog_ids) for scene in pair]==[2,2]
    assert all(scene.polarization=='VH+VV' for scene in pair)
    records[1]['properties']['sat:relative_orbit']=99
    with pytest.raises(ValueError,match='No same-platform'):providers.select_pair(records,params(),allow_mosaics=True)


def install_http(monkeypatch,handler):
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))


def test_oauth_reuses_expiry_and_invalidates_on_credential_change(monkeypatch):
    calls=[]
    clock=[0.0]
    monkeypatch.setattr(cdse_runtime.time,'monotonic',lambda:clock[0])
    monkeypatch.setenv('CDSE_CLIENT_ID','synthetic-client')
    monkeypatch.setenv('CDSE_CLIENT_SECRET','synthetic-secret')
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'access_token':f'synthetic-token-{len(calls)}','expires_in':120})
    install_http(monkeypatch,handler)
    assert cdse_runtime.access_token(providers.TOKEN_URL)=='synthetic-token-1'
    clock[0]=10
    assert cdse_runtime.access_token(providers.TOKEN_URL)=='synthetic-token-1'
    assert len(calls)==1
    clock[0]=121
    assert cdse_runtime.access_token(providers.TOKEN_URL)=='synthetic-token-2'
    monkeypatch.setenv('CDSE_CLIENT_SECRET','changed-synthetic-secret')
    assert cdse_runtime.access_token(providers.TOKEN_URL)=='synthetic-token-3'


@pytest.mark.parametrize('header,expected_requests,expected_wait',[('1500',2,[1.5]),('15000',1,[])])
def test_cdse_retry_header_is_milliseconds_and_long_delays_stop(monkeypatch,header,expected_requests,expected_wait):
    waits=[];requests=[]
    monkeypatch.setattr(cdse_runtime.time,'sleep',waits.append)
    def handler(request):
        requests.append(request)
        return httpx.Response(429,headers={'retry-after':header}) if len(requests)==1 else httpx.Response(200)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cdse_runtime.request_with_retry(client,'POST',providers.PROCESS_URL,milliseconds=True)
    assert len(requests)==expected_requests and waits==expected_wait


def processing_tar(grid,instant,*,optical=False):
    pixels=(.2,.1,.3,.1,4,1) if optical else (.1,1,0,.01)
    values=np.stack([np.full((grid.height,grid.width),value) for value in pixels]).astype('float32')
    with MemoryFile() as memory:
        with memory.open(driver='GTiff',width=grid.width,height=grid.height,count=len(pixels),dtype='float32',
                         crs=grid.crs,transform=grid.transform) as target:target.write(values)
        tiff=memory.read()
    output=BytesIO()
    metadata=json.dumps({'tiles':[{'date':instant,'shId':123,'dataPath':'https://data.example/product?private=omitted'}]}).encode()
    with tarfile.open(fileobj=output,mode='w') as archive:
        for name,content in [('default.tif',tiff),('userdata.json',metadata)]:
            member=tarfile.TarInfo(name);member.size=len(content);archive.addfile(member,BytesIO(content))
    return output.getvalue()


def test_optical_local_sensing_time_can_differ_from_catalog_datatake_time(monkeypatch,tmp_path):
    grid=Grid.for_bbox(params().bbox,100)
    body=processing_tar(grid,'2024-08-10T12:15:09Z',optical=True)
    requests=[]
    monkeypatch.setenv('CDSE_CLIENT_ID','synthetic-client');monkeypatch.setenv('CDSE_CLIENT_SECRET','synthetic-secret')
    def handler(request):
        if str(request.url)==providers.TOKEN_URL:
            return httpx.Response(200,json={'access_token':'synthetic-token','expires_in':300})
        requests.append(json.loads(request.content))
        return httpx.Response(200,content=body)
    install_http(monkeypatch,handler)
    provider=providers.CopernicusProvider(grid,tmp_path)
    result=provider.process('sentinel-2-l2a','s2-before',[item('2024-08-10',10,10.05)])
    assert result.shape==(6,grid.height,grid.width)
    window=requests[0]['input']['data'][0]['dataFilter']['timeRange']
    assert window=={'from':'2024-08-10T00:00:00+00:00','to':'2024-08-10T23:59:59.999999+00:00'}
    metadata=json.loads((tmp_path/'s2-before-sources.json').read_text())
    assert metadata['processed_tiles'][0]['date']=='2024-08-10T12:15:09+00:00'
    assert metadata['selected_catalog_ids']==[item('2024-08-10',10,10.05)['id']]


def test_cdse_catalog_get_continuation_is_followed_and_duplicate_ids_removed(monkeypatch):
    calls=[]
    first=item('2024-08-10',10,10.05);second=item('2024-08-22',10,10.05)
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,json={'features':[first], 'links':[{'rel':'next','method':'GET','href':providers.STAC_URL+'?token=synthetic-continuation'}]})
        return httpx.Response(200,json={'features':[first,second],'links':[]})
    install_http(monkeypatch,handler)
    result=providers.search_catalog(params().bbox,'2024-08-01','2024-09-01')
    assert result==json.loads(json.dumps([first,second]))
    assert [request.method for request in calls]==['POST','GET']
    assert calls[1].url.params['token']=='synthetic-continuation'


def test_source_attribution_uses_actual_years_and_preserves_attached_credits():
    from app.attribution import run_attributions
    run={'mode':'live','flood_date':'2026-08-21','provenance':{
        'radar_scenes':[{'acquired':'2024-12-29T12:00:00Z'},{'acquired':'2025-01-10T12:00:00Z'}]},
        'attributions':['Contains modified Copernicus Sentinel data 2026.','Independent evaluation credit']}
    credits=run_attributions(run)
    assert credits[0]=='Contains modified Copernicus Sentinel data 2024, 2025.'
    assert 'Independent evaluation credit' in credits
    assert not any('2026' in credit for credit in credits)
    run['provenance']={}
    assert run_attributions(run)[0]=='Contains modified Copernicus Sentinel data.'


def test_configured_presets_validate_bounds_and_pre_event_cutoff():
    from app.presets import CasePreset
    value={'id':'sample','name':'Configured area','bbox':[10,10,10.5,10.5],'flood_date':'2024-08-21',
           'osm_snapshot_cutoff':'2024-08-20T00:00:00Z'}
    assert CasePreset.model_validate(value).id=='sample'
    for change in ({'bbox':[10,10,12,11]},{'osm_snapshot_cutoff':'2024-08-20T00:00:00'},
                   {'osm_snapshot_cutoff':'2024-08-21T00:00:00Z'}):
        with pytest.raises(ValidationError):CasePreset.model_validate({**value,**change})


@pytest.mark.parametrize('returned_date,valid',[('2024-08-10T12:00:00Z',True),('2024-08-11T12:00:00Z',False)])
def test_processed_observation_dates_are_checked_before_accepting_pixels(monkeypatch,tmp_path,returned_date,valid):
    p=params();grid=Grid.for_bbox(p.bbox,100)
    body=processing_tar(grid,returned_date)
    monkeypatch.setenv('CDSE_CLIENT_ID','synthetic-client');monkeypatch.setenv('CDSE_CLIENT_SECRET','synthetic-secret')
    def handler(request):
        if str(request.url)==providers.TOKEN_URL:
            return httpx.Response(200,json={'access_token':'synthetic-token','expires_in':300})
        return httpx.Response(200,content=body)
    install_http(monkeypatch,handler)
    client=providers.CopernicusProvider(grid,tmp_path)
    scene=providers.scene_from_item(item('2024-08-10',10,10.05,platform='sentinel-1a'))
    if valid:
        assert client.process('sentinel-1-grd','s1-before',scene).shape==(4,grid.height,grid.width)
        record=json.loads((tmp_path/'s1-before-sources.json').read_text())
        assert record['processed_tiles'][0]['date'].startswith('2024-08-10')
        assert '?' not in record['processed_tiles'][0]['dataPath']
    else:
        with pytest.raises(ValueError,match='unverified observations'):client.process('sentinel-1-grd','s1-before',scene)
        assert not (tmp_path/'s1-before.tif').exists()


def test_cdse_refreshes_an_expired_or_rejected_token_once(monkeypatch,tmp_path):
    p=params();grid=Grid.for_bbox(p.bbox,100)
    body=processing_tar(grid,'2024-08-10T12:00:00Z')
    tokens=[];processing=[]
    monkeypatch.setenv('CDSE_CLIENT_ID','synthetic-client');monkeypatch.setenv('CDSE_CLIENT_SECRET','synthetic-secret')
    def handler(request):
        if str(request.url)==providers.TOKEN_URL:
            tokens.append(request);return httpx.Response(200,json={'access_token':f'token-{len(tokens)}','expires_in':300})
        processing.append(request)
        return httpx.Response(401) if len(processing)==1 else httpx.Response(200,content=body)
    install_http(monkeypatch,handler)
    client=providers.CopernicusProvider(grid,tmp_path)
    client.process('sentinel-1-grd','s1-before',providers.scene_from_item(item('2024-08-10',10,10.05,platform='sentinel-1a')))
    assert len(tokens)==2 and len(processing)==2
    assert processing[1].headers['Authorization']=='Bearer token-2'


@pytest.mark.parametrize('scl',[2,99])
def test_topographic_shadow_and_unknown_optical_classes_remain_unobserved(scl):
    p=params();grid=Grid.for_bbox(p.bbox,100);size=(grid.height,grid.width)
    radar=np.stack([np.full(size,.1),np.ones(size),np.zeros(size)])
    optical=np.stack([np.full(size,.4),np.full(size,.1),np.full(size,.5),np.full(size,.1),np.full(size,scl),np.ones(size)])
    detected=detect_changes(radar,radar,grid,p,optical_before=optical,optical_after=optical)
    assert not detected['optical_valid'].any()


def test_public_mosaic_prefers_clear_tile_over_topographic_shadow(tmp_path):
    from app.public_data import optical_mosaic,write_grid
    grid=Grid.for_bbox(params().bbox,100)
    shadow=item('2024-08-10',10,10.05,minute=0,cloud=0)
    clear=item('2024-08-10',10,10.05,minute=1,cloud=1)
    for record,scl in ((shadow,2),(clear,4)):
        values=np.stack([np.full((grid.height,grid.width),value) for value in (.1,.1,.1,.1,scl,1)])
        write_grid(tmp_path/(record['id']+'-optical.tif'),values.astype('float32'),grid)
    result=optical_mosaic([shadow,clear],grid,tmp_path)
    assert np.all(result[4]==4)


def mocked_generation(monkeypatch):
    monkeypatch.setenv('LLM_ENABLED','true');monkeypatch.setenv('GROQ_API_KEY','synthetic-groq-key')
    monkeypatch.delenv('LLM_API_KEY',raising=False)
    monkeypatch.setenv('LLM_RESPONSE_CACHE_TTL_SECONDS','300')
    calls=[]
    def generate(context,question,language,config,history,plan):
        calls.append(question)
        context.provider_telemetry={'provider_requests':1,'prompt_tokens':100,'completion_tokens':30}
        fact=context.call('flood_surface_change',{}).facts[0]
        return AnswerDraft(run_id=context.run_id,language=language,kind='answer',sections=[AnswerSection(topic='flood',
            claims=[claim_for(fact)],explanations=[],narrative=[NarrativeSentence(text='{fact:'+fact.fact_id+'}.',fact_ids=[fact.fact_id])])])
    monkeypatch.setattr('app.copilot_service._run_llm',generate)
    return calls,generate


def test_groq_reuse_requires_identical_evidence_language_and_credentials(monkeypatch,evidence_run):
    calls,_=mocked_generation(monkeypatch)
    q='How much new water was detected?'
    first=answer(evidence_run,q);second=answer(evidence_run,q)
    assert not first['generation']['cache_hit'] and second['generation']['cache_hit']
    assert second['generation']['provider_requests']==0 and len(calls)==1
    evidence_run['stats']['water_km2']+=1
    assert not answer(evidence_run,q)['generation']['cache_hit'] and len(calls)==2
    assert not answer(evidence_run,q,'ne')['generation']['cache_hit'] and len(calls)==3
    monkeypatch.setenv('GROQ_API_KEY','changed-synthetic-key')
    assert not answer(evidence_run,q)['generation']['cache_hit'] and len(calls)==4


def test_simultaneous_identical_questions_share_a_single_generation(monkeypatch,evidence_run):
    calls,generate=mocked_generation(monkeypatch)
    entered=threading.Event();release=threading.Event()
    def blocked(*args):
        entered.set();assert release.wait(2)
        return generate(*args)
    monkeypatch.setattr('app.copilot_service._run_llm',blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first=executor.submit(answer,evidence_run,'How much new water was detected?')
        assert entered.wait(1)
        second=executor.submit(answer,evidence_run,'How much new water was detected?')
        release.set()
        replies=[first.result(),second.result()]
    assert len(calls)==1 and sorted(r['generation']['cache_hit'] for r in replies)==[False,True]


def test_account_rate_limit_cooldown_avoids_more_provider_calls(monkeypatch,evidence_run):
    calls,_=mocked_generation(monkeypatch)
    monkeypatch.setenv('LLM_PROVIDER_COOLDOWN_SECONDS','30')
    def rejected(context,*args):
        calls.append('attempt');context.provider_telemetry={'provider_requests':1}
        raise ProviderFailure('rate_limited',20,429)
    monkeypatch.setattr('app.copilot_service._run_llm',rejected)
    first=answer(evidence_run,'How much new water was detected?')
    second=answer(evidence_run,'How much new water was detected?')
    assert first['generation']['reason']==second['generation']['reason']=='rate_limited'
    assert len(calls)==1 and second['generation']['provider_requests']==0
    assert second['facts'] and second['generation']['retry_after_seconds']>0
