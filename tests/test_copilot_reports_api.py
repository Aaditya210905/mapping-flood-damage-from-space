"""Synthetic pipeline fixtures and mocked providers; no live-data accuracy claims."""
import copy
from io import BytesIO
import json
import uuid

from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from app import main
from app.evidence import EvidenceContext
from app.models import ATTRIBUTIONS
from app.report import build_report_data, report_html, report_pdf, ReportDataError


def test_report_measurements_and_missing_fields_match_in_both_languages(evidence_run):
    evidence_run['stats']['water_km2'] = None
    reports = [build_report_data(evidence_run, language) for language in ('en','ne')]
    assert [f.model_dump() for f in reports[0].facts] == [f.model_dump() for f in reports[1].facts]
    for data in reports:
        assert data.status == 'partial' and 'water_km2' in data.missing_fields
        assert set(ATTRIBUTIONS).issubset(data.attributions)
        assert next(f for f in data.facts if f.field_name=='water_km2').value is None
        assert next(f for f in data.facts if f.field_name=='buildings_exposed').value == evidence_run['stats']['buildings_exposed']
        html=report_html(evidence_run,data.language,data)
        assert ('उपलब्ध छैन' if data.language=='ne' else 'Unavailable') in html
        assert ('अपूर्ण प्रतिवेदन' if data.language=='ne' else 'INCOMPLETE REPORT') in html


@pytest.mark.parametrize('language',['en','ne'])
def test_report_is_one_page_pdf_with_embedded_fonts(evidence_run,language):
    pdf=report_pdf(evidence_run,language)
    reader=PdfReader(BytesIO(pdf))
    assert len(reader.pages)==1
    text=reader.pages[0].extract_text()
    assert evidence_run['id'] in text
    assert 'OpenStreetMap contributors' in text
    assert 'Copernicus WorldDEM-30' in text
    assert reader.pages[0]['/Resources']['/Font']
    if language=='ne':
        assert 'बाढी स्थिति प्रतिवेदन' in text
        assert any('Devanagari' in str(font.get_object()) for font in reader.pages[0]['/Resources']['/Font'].values())


def test_report_rejects_missing_critical_data_and_cross_run_response(evidence_run):
    with pytest.raises(ReportDataError):
        build_report_data(evidence_run,response={'run_id':str(uuid.uuid4()),'language':'en'})
    evidence_run.pop('name')
    with pytest.raises(ReportDataError,match='area or event date'):
        build_report_data(evidence_run)


def test_report_escapes_untrusted_names_and_preserves_credits(evidence_run):
    evidence_run['name']='<img src="http://attacker.test/secret">'
    evidence_run['attributions']=None
    html=report_html(evidence_run)
    assert '<img src=' not in html and '&lt;img' in html
    for credit in ATTRIBUTIONS:assert credit in html


@pytest.fixture
def stored_client(tmp_path,monkeypatch,evidence_run):
    monkeypatch.setattr(main,'DATA',tmp_path)
    monkeypatch.setenv('LLM_ENABLED','false')
    folder=tmp_path/evidence_run['id'];folder.mkdir()
    (folder/'result.json').write_text(json.dumps(evidence_run))
    with TestClient(main.app) as client:
        yield client,folder,evidence_run


def test_chat_api_backward_compatibility_missing_values_and_history_binding(stored_client):
    client,folder,run=stored_client;base=f"/api/analyses/{run['id']}"
    en=client.post(base+'/copilot',json={'question':'Which settlements are cut off?'}).json()
    assert all(k in en for k in ('answer','intent','intent_confidence','evidence','method'))
    assert en['generation']['mode']=='deterministic_fallback'
    ne=client.post(base+'/copilot',json={'question':'कुन गाउँ सम्पर्कविहीन छन्?','language':'ne','history':[{'role':'user','content':'Which settlements are cut off?','run_id':run['id']}]}).json()
    assert ne['language']=='ne' and en['facts']==ne['facts']
    bad=client.post(base+'/copilot',json={'question':'Summarize','history':[{'role':'assistant','content':'other analysis facts','run_id':str(uuid.uuid4())}]})
    assert bad.status_code==422 and 'traceback' not in bad.text.lower()
    run['stats'].pop('water_km2');(folder/'result.json').write_text(json.dumps(run))
    missing=client.post(base+'/copilot',json={'question':'What is the flood extent?'}).json()
    assert 'Unavailable' in missing['answer'] and missing['missing_fact_ids']


def test_api_compound_questions_unsupported_statistics_and_unknown_runs(stored_client):
    client,_,run=stored_client;base=f"/api/analyses/{run['id']}"
    reply=client.post(base+'/copilot',json={'question':'Which roads are exposed and which settlements are isolated?'}).json()
    fields={f['field_name'] for f in reply['facts']}
    assert {'roads_exposed','settlements_cut_off'}.issubset(fields)
    absent=client.post(base+'/copilot',json={'question':'How many people died?'}).json()
    assert absent['intent']=='unsupported' and not absent['facts']
    assert client.post(f'/api/analyses/{uuid.uuid4()}/copilot',json={'question':'summary'}).status_code==404
    assert client.post('/api/analyses/not-a-run/copilot',json={'question':'summary'}).status_code==404


def test_feature_api_validates_layer_and_exact_identifier(stored_client):
    client,_,run=stored_client;base=f"/api/analyses/{run['id']}"
    identifier=run['layers']['settlements']['features'][0]['properties']['id']
    response=client.get(base+'/feature-evidence',params={'layer':'settlements','feature_id':identifier})
    assert response.status_code==200 and response.json()['feature_ids']==[identifier]
    assert all(f['subject_id']==identifier for f in response.json()['facts'])
    assert client.get(base+'/feature-evidence',params={'layer':'roads','feature_id':'/etc/passwd'}).json()['status']=='not_found'
    assert client.get(base+'/feature-evidence',params={'layer':'arbitrary','feature_id':identifier}).status_code==422


def test_report_api_freezes_typed_snapshot_and_keeps_results_immutable(stored_client):
    client,folder,run=stored_client;base=f"/api/analyses/{run['id']}"
    original=(folder/'result.json').read_bytes()
    response=client.post(base+'/report',json={'language':'ne','use_llm':False})
    assert response.status_code==200,response.text
    data=response.json();assert data['language']=='ne' and data['generation']['mode']=='deterministic_fallback'
    assert client.get(data['links']['pdf']).content.startswith(b'%PDF')
    snapshot=client.get(data['links']['json']).json()
    run['stats']['water_km2']=99999999;(folder/'result.json').write_text(json.dumps(run))
    assert client.get(data['links']['json']).json()==snapshot
    assert '99999999' not in client.get(data['links']['html']).text
    (folder/'result.json').write_bytes(original)
    assert (folder/'result.json').read_bytes()==original
    assert client.get(base+'/reports/not-a-uuid?format=json').status_code==422
    assert client.get(base+f'/reports/{uuid.uuid4()}?format=json').status_code==404
    assert client.get(base+'/report?language=ne&format=pdf').status_code==200


def test_saved_result_with_another_run_id_is_rejected(stored_client):
    client,folder,run=stored_client
    run['id']=str(uuid.uuid4());(folder/'result.json').write_text(json.dumps(run))
    assert client.post(f'/api/analyses/{folder.name}/copilot',json={'question':'summary'}).status_code==422


def test_optional_corrupt_or_cross_run_evaluations_do_not_contaminate_facts(stored_client):
    client,folder,run=stored_client
    (folder/'evaluation.json').write_text('invalid json')
    (folder/'asset-evaluation.json').write_text(json.dumps({'run_id':str(uuid.uuid4()),'water_km2':987654321}))
    response=client.post(f"/api/analyses/{run['id']}/copilot",json={'question':'What is the flood extent?'})
    assert response.status_code==200
    assert '987654321' not in response.text
    assert response.json()['facts'][0]['value']==run['stats']['water_km2']
    saved=client.get(f"/api/analyses/{run['id']}").json()
    assert any('was not attached' in warning for warning in saved['warnings'])
    assert 'asset_evaluation' not in saved


def test_named_settlement_lookup_retrieves_the_requested_feature(evidence_run):
    feature=evidence_run['layers']['settlements']['features'][-1]['properties']
    context=EvidenceContext(evidence_run)
    result=context.call('settlement_connectivity',{'status':'all','offset':0,'limit':1,'query':feature['id']})
    assert result.feature_ids==[feature['id']]
    assert all(f.subject_id==feature['id'] for f in result.facts if f.subject_type=='settlements')


def test_chat_api_paraphrases_and_road_specific_followup(stored_client):
    client, _, run = stored_client
    endpoint = f"/api/analyses/{run['id']}/copilot"
    coverage = client.post(endpoint, json={'question': 'How much of the study area could actually be observed?'}).json()
    assert any(f['field_name'] == 'valid_coverage_pct' for f in coverage['facts'])
    road_id = run['layers']['roads']['features'][0]['properties']['id']
    question = f'Tell me about road {road_id}'
    first = client.post(endpoint, json={'question': question}).json()
    assert first['intent'] == 'feature'
    turns = [{'role': 'user', 'content': question, 'run_id': run['id']},
             {'role': 'assistant', 'content': first['answer'][:2000], 'run_id': run['id']}]
    response = client.post(endpoint, json={'question': 'What about the villages on that road?', 'history': turns})
    assert response.status_code == 200
    reply = response.json()
    count = next(f for f in reply['facts'] if f['field_name'] == 'matching_settlements_count')
    context = EvidenceContext(run)
    expected = sum(context.route_membership(item['properties'], road_id) is True
                   for item in run['layers']['settlements']['features'])
    assert count['value'] == expected
    assert not any(f['field_name'] == 'settlements_cut_off' for f in reply['facts'])
    ambiguous = client.post(endpoint, json={'question': 'What about the villages on that road?'}).json()
    assert ambiguous['intent'] == 'clarification' and not ambiguous['facts']


def test_recorded_optical_dates_and_dem_ids_do_not_leak_signed_urls(evidence_run):
    evidence_run['provenance']['optical_observations']=[{'id':evidence_run['provenance']['optical_scenes'][0],'acquired':'2026-08-20T00:00:00Z'}]
    evidence_run['provenance']['acquisition']={'dem':{'tiles':['https://public.example/Copernicus_DEM.tif?token=PRIVATE']}}
    facts=EvidenceContext(evidence_run).call('sources_limitations',{}).facts
    assert next(f for f in facts if f.field_name=='optical_acquisition_dates').value==['2026-08-20T00:00:00Z']
    assert next(f for f in facts if f.field_name=='dem_product_ids').value==['Copernicus_DEM.tif']
    assert 'PRIVATE' not in str(facts)


def test_configured_groq_api_request_flow_uses_mocked_sdk(stored_client,monkeypatch):
    import httpx
    from groq import Groq
    from app.grounding import claim_for
    client,_,run=stored_client;base=f"/api/analyses/{run['id']}"
    context=EvidenceContext(run);context.call('event_summary',{})
    draft={'run_id':run['id'],'language':'ne','kind':'answer','sections':[{'topic':'flood','claims':[claim_for(context.global_fields['water_km2']).model_dump()],'explanations':[]}]}
    seen=[]
    def handle(request):
        seen.append(json.loads(request.content))
        message={'role':'assistant','content':None,'tool_calls':[{'id':'facts','type':'function','function':{'name':'flood_surface_change','arguments':'{}'}}]} if len(seen)==1 else {'role':'assistant','content':'Ready' if len(seen)==2 else json.dumps(draft)}
        return httpx.Response(200,json={'id':'chat_api_test','object':'chat.completion','created':0,'model':'openai/gpt-oss-120b','choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if len(seen)==1 else 'stop'}]})
    sdk=Groq(api_key='unit-test-placeholder',http_client=httpx.Client(transport=httpx.MockTransport(handle)),max_retries=0)
    monkeypatch.setattr('app.llm.Groq',lambda **kwargs:sdk)
    monkeypatch.setenv('LLM_ENABLED','true');monkeypatch.setenv('GROQ_API_KEY','unit-test-placeholder')
    response=client.post(base+'/copilot',json={'question':'नयाँ डुबान क्षेत्रको सार बताउनुहोस्।','language':'ne'})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['generation']['mode']=='llm' and data['generation']['provider']=='groq'
    assert 'सम्भावित' in data['answer'] and data['run_id']==run['id']
    assert data['facts'][0]['value']==run['stats']['water_km2'] and len(seen)==3
