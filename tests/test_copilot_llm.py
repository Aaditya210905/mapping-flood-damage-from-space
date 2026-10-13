import json
from types import SimpleNamespace
import httpx
import pytest
from groq import Groq

from app.copilot import answer
from app.evidence import EvidenceContext
from app.grounding import claim_for


class SDKItem(SimpleNamespace):
    def model_dump(self,**kwargs):return vars(self)


def call(name,args,call_id='call_test'):
    return SDKItem(type='function_call',name=name,arguments=json.dumps(args),call_id=call_id)


def result(items=None,draft=None):
    return SimpleNamespace(output=items or [],output_text=json.dumps(draft) if isinstance(draft,dict) else draft or '')


def good_draft(run,field='water_km2',language='en',topic='flood'):
    c=EvidenceContext(run);c.call('event_summary',{})
    return {'run_id':c.run_id,'language':language,'kind':'answer','sections':[{'topic':topic,'claims':[claim_for(c.global_fields[field]).model_dump()],'explanations':[]}]}


@pytest.fixture
def fake_provider(monkeypatch):
    monkeypatch.setenv('LLM_ENABLED','true')
    monkeypatch.setenv('LLM_API_KEY','unit-test-placeholder-not-a-real-key')
    class Fake:
        responses=[]
        requests=[]
        closed=False
        def __init__(self,config):pass
        def create(self,**kwargs):
            self.requests.append(kwargs)
            if kwargs["tool_choice"]=="auto" and self.responses and not isinstance(self.responses[0],Exception) and not self.responses[0].output:
                return result()
            output=self.responses.pop(0)
            if isinstance(output,Exception):raise output
            return output
        def close(self):self.closed=True
    monkeypatch.setattr('app.copilot_service.GroqProvider',Fake)
    return Fake


def test_llm_uses_tools_and_validated_facts(evidence_run,fake_provider):
    fake_provider.responses=[result([call('flood_surface_change',{})]),result(draft=good_draft(evidence_run))]
    reply=answer(evidence_run,'What do the new-water measurements indicate?')
    assert reply['generation']['mode']=='llm'
    assert reply['generation']['validation']=='validated_claims'
    assert reply['facts'][0]['value']==evidence_run['stats']['water_km2']
    assert reply['run_id']==evidence_run['id']
    assert all(t['type']=='function' for t in fake_provider.requests[0]['tools'])


def test_compound_question_retrieves_both_tools(evidence_run,fake_provider):
    c=EvidenceContext(evidence_run)
    d={'run_id':c.run_id,'language':'en','kind':'answer','sections':[
        {'topic':'infrastructure','claims':[claim_for(c.global_fields['roads_exposed']).model_dump()],'explanations':[]},
        {'topic':'connectivity','claims':[claim_for(c.global_fields['settlements_cut_off']).model_dump()],'explanations':[]}]}
    fake_provider.responses=[result([call('infrastructure_impacts',{'asset_type':'roads','only_exposed':True,'offset':0,'limit':2},'call_roads'),call('settlement_connectivity',{'status':'all','offset':0,'limit':2},'call_access')]),result(draft=d)]
    reply=answer(evidence_run,'List the potentially exposed roads and villages with modeled loss of access.')
    assert reply['intent']=='compound' and reply['generation']['mode']=='llm'
    assert {f['field_name'] for f in reply['facts']}=={'roads_exposed','settlements_cut_off'}
    assert 'Actual closures' in reply['answer']
    assert 'not confirmed damage' in reply['answer']


@pytest.mark.parametrize('bad', ['invalid_json','wrong_run','wrong_location','invented_fact','invented_value','physical_destroyed','wrong_language'])
def test_bad_output_retries_once_then_falls_back_without_releasing_it(evidence_run,fake_provider,bad):
    d=good_draft(evidence_run)
    if bad=='invalid_json':d='PRIVATE MALFORMED RESPONSE'
    elif bad=='wrong_run':d['run_id']='other-run'
    elif bad=='wrong_language':d['language']='ne'
    elif bad=='wrong_location':d['sections'][0]['claims'][0]['subject_id']='other-location'
    elif bad=='invented_fact':d['sections'][0]['claims'][0]['fact_id']='invented'
    elif bad=='invented_value':d['sections'][0]['claims'][0]['value']=987654321
    else:d['sections'][0]['text']='Buildings are confirmed destroyed.'
    fake_provider.responses=[result([call('flood_surface_change',{})]),result(draft=d),result(draft=d)]
    reply=answer(evidence_run,'What is the flood extent?')
    assert reply['generation']['mode']=='deterministic_fallback'
    assert reply['generation']['reason']=='validation_failed'
    assert len(fake_provider.requests)==4
    assert '987654321' not in reply['answer'] and 'confirmed destroyed' not in reply['answer'] and 'PRIVATE' not in reply['answer']


def test_validation_repair_can_succeed(evidence_run,fake_provider):
    fake_provider.responses=[result([call('flood_surface_change',{})]),result(draft='bad'),result(draft=good_draft(evidence_run))]
    assert answer(evidence_run,'How much new water?')['generation']['mode']=='llm'


@pytest.mark.parametrize('reason',['timeout','rate_limited','provider_unavailable','credentials_rejected'])
def test_provider_failures_use_original_safe_fallback(evidence_run,fake_provider,reason):
    from app.llm import ProviderFailure
    fake_provider.responses=[ProviderFailure(reason)]
    reply=answer(evidence_run,'Which settlements are cut off?')
    assert reply['intent']=='access'
    assert reply['generation']['reason']==reason
    assert f"{evidence_run['stats']['settlements_cut_off']} settlements" in reply['answer']


def test_unsupported_casualties_do_not_call_provider(evidence_run,fake_provider):
    reply=answer(evidence_run,'How many people died?')
    assert reply['intent']=='unsupported' and not reply['facts'] and not fake_provider.requests


def test_missing_key_and_missing_metrics_are_explicit(evidence_run,monkeypatch):
    monkeypatch.setenv('LLM_ENABLED','true')
    for name in ('LLM_API_KEY','GROQ_API_KEY'):monkeypatch.delenv(name,raising=False)
    evidence_run['stats'].pop('water_km2')
    reply=answer(evidence_run,'What is the flood extent?')
    assert reply['generation']['reason']=='missing_credentials'
    assert 'Unavailable' in reply['answer']
    assert reply['missing_fact_ids']


def test_nepali_selected_language_and_identical_evidence(evidence_run,fake_provider):
    records=[]
    for language in ('en','ne'):
        fake_provider.responses=[result([call('flood_surface_change',{})]),result(draft=good_draft(evidence_run,language=language))]
        reply=answer(evidence_run,'Explain these measurements.',language)
        assert ('सम्भावित' in reply['answer']) if language=='ne' else ('Potential new inundation' in reply['answer'])
        records.append(reply['facts'])
    assert records[0]==records[1]


def test_actual_groq_sdk_contract_roundtrip_uses_mock_transport(evidence_run,monkeypatch):
    monkeypatch.setenv('LLM_ENABLED','true');monkeypatch.setenv('LLM_API_KEY','unit-test-placeholder')
    requests=[]
    def handle(request):
        data=json.loads(request.content);requests.append(data)
        assert str(request.url)=='https://api.groq.com/openai/v1/chat/completions'
        message={'role':'assistant','content':None,'tool_calls':[{'id':'call_1','type':'function','function':{'name':'flood_surface_change','arguments':'{}'}}]} if len(requests)==1 else {'role':'assistant','content':'Ready to compose facts.' if len(requests)==2 else json.dumps(good_draft(evidence_run))}
        return httpx.Response(200,json={'id':'chat_test','object':'chat.completion','created':0,'model':'openai/gpt-oss-120b','choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if len(requests)==1 else 'stop'}]})
    client=Groq(api_key='unit-test-placeholder',base_url='https://api.groq.com',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('app.llm.Groq',lambda **kwargs:client)
    reply=answer(evidence_run,'Describe the newly detected water.')
    assert reply['generation']['mode']=='llm' and reply['generation']['provider']=='groq'
    assert len(requests)==3 and requests[0]['tool_choice']=='required' and requests[0]['store'] is False
    assert 'response_format' not in requests[0]
    assert any(item['role']=='tool' for item in requests[1]['messages'])
    assert requests[2]['response_format']['json_schema']['strict'] is True
    assert 'tools' not in requests[2]


@pytest.mark.parametrize('tool,args',[('exec_python',{'code':'print(1)'}),('event_summary',{'run_id':'another-analysis'}),('feature_evidence',{'layer':'roads','feature_id':'/etc/passwd','path':'/etc/passwd'})])
def test_unapproved_execution_cross_run_and_path_arguments_fall_back(evidence_run,fake_provider,tool,args):
    fake_provider.responses=[result([call(tool,args)])]
    reply=answer(evidence_run,'What is the flood extent?')
    assert reply['generation']['mode']=='deterministic_fallback'
    assert reply['generation']['reason']=='invalid_tool_call'
    assert '/etc/passwd' not in reply['answer']


def test_second_tool_round_can_retrieve_specific_feature_evidence(evidence_run,fake_provider):
    c=EvidenceContext(evidence_run)
    feature=evidence_run['layers']['settlements']['features'][0]['properties']['id']
    fact=next(f for f in c.call('feature_evidence',{'layer':'settlements','feature_id':feature}).facts if f.field_name=='status')
    draft={'run_id':c.run_id,'language':'en','kind':'answer','sections':[{'topic':'feature','claims':[claim_for(fact).model_dump()],'explanations':[]}]}
    fake_provider.responses=[result([call('settlement_connectivity',{'status':'all','offset':0,'limit':1},'find')]),result([call('feature_evidence',{'layer':'settlements','feature_id':feature},'detail')]),result(draft=draft)]
    reply=answer(evidence_run,'What supports this settlement connectivity assessment?')
    assert reply['generation']['mode']=='llm' and feature in reply['answer']
    assert reply['facts'][0]['subject_id']==feature
