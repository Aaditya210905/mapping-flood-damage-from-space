"""Primary query interpretation through the native SDK, using mocked transport."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from groq import Groq
import httpx
import pytest

from app.copilot import answer
from app.copilot_types import AnswerDraft, AnswerSection
from app.evidence import EvidenceContext
from app.grounding import claim_for
from app.llm import LLMConfig


def query(**updates):
    return {'response_kind': 'answer', 'topics': ['connectivity'], 'asset_types': [],
        'status': 'connected', 'exposure': 'exposed', 'destination_type': 'hospital',
        'inventory_layers': [], 'features': [], 'road_id': None, 'requested_fields': [],
        'listing': True, 'clarification_reason': None, **updates}


@pytest.fixture
def native(monkeypatch, evidence_run):
    monkeypatch.setenv('LLM_ENABLED', 'true')
    monkeypatch.setenv('GROQ_API_KEY', 'mock-key-no-network')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.setenv('LLM_QUERY_PLANNING', 'semantic')
    monkeypatch.setenv('LLM_APPLICATION_RETRIEVAL', 'true')
    monkeypatch.setenv('LLM_RESPONSE_CACHE_TTL_SECONDS', '300')
    state = {'requests': [], 'query': query(), 'language': 'en'}

    def handle(request):
        body = json.loads(request.content)
        state['requests'].append(body)
        title = body['response_format']['json_schema']['schema']['title']
        if state.get('http_status'):
            return httpx.Response(state['http_status'], headers={'Retry-After': '20'}, json={'error': {'message': 'PRIVATE DETAIL'}})
        if title == 'SemanticQuery':
            if state.get('entered'):
                state['entered'].set()
                assert state['release'].wait(2)
            content = state['query']
        elif state.get('wrong_draft'):
            content = state['wrong_draft']
        else:
            saved = next(json.loads(message['content'])['saved_evidence'] for message in body['messages']
                         if message['role'] == 'user' and 'saved_evidence' in json.loads(message['content']))
            by_topic = defaultdict(list)
            topics = {'flood_surface_change': 'flood', 'infrastructure_impacts': 'infrastructure',
                      'settlement_connectivity': 'connectivity', 'mapped_inventory': 'inventory',
                      'feature_evidence': 'feature', 'sources_limitations': 'sources'}
            for result in saved:
                for fact in result['facts']:
                    topic = 'limitations' if fact['field_name'] in ('warnings', 'limitations') else topics[result['tool']]
                    by_topic[topic].append({'fact_id': fact['fact_id'], 'operation': 'exact'})
            content = {'run_id': evidence_run['id'], 'language': state['language'],
                'kind': 'report' if state['query']['response_kind'] == 'report' else 'answer',
                'sections': [{'topic': topic, 'claims': claims, 'explanations': [], 'narrative': []}
                             for topic, claims in by_topic.items()]}
        return httpx.Response(200, json={'id': 'mock-completion', 'object': 'chat.completion', 'created': 0,
            'model': 'openai/gpt-oss-120b', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(content)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}})

    monkeypatch.setattr('app.llm.Groq', lambda **kwargs: Groq(api_key='mock-key-no-network', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    return state


def test_semantic_is_the_default_primary_planner(monkeypatch):
    monkeypatch.delenv('LLM_QUERY_PLANNING', raising=False)
    assert LLMConfig.from_env().query_planning == 'semantic'


@pytest.mark.parametrize('question,language,selection', [
    ('Which hamlets retain a path to medical care?', 'en', query()),
    ('Identify places the event separated from services.', 'en', query(status='cut_off', destination_type='all')),
    ('अस्पतालतर्फ बाटो नपुग्ने बस्तीको सूची दिनुहोस्।', 'ne', query(status='not_connected')),
    ('What fraction of captured terrain supports water inference?', 'en', query(topics=['flood'],
        status='all', destination_type='all', listing=False, requested_fields=['water_eligible_coverage_pct'])),
    ('Identify transport links that escaped mapped exposure.', 'en', query(topics=['infrastructure'],
        status='all', destination_type='all', asset_types=['roads'], exposure='not_exposed')),
])
def test_semantic_scope_drives_authoritative_retrieval_and_lists(evidence_run, native, question, language, selection):
    native.update(query=selection, language=language)
    reply = answer(evidence_run, question, language)
    assert reply['generation']['mode'] == 'llm'
    assert reply['generation']['query_planning'] == 'semantic'
    assert reply['generation']['provider_requests'] == 2
    assert len(native['requests']) == 2
    assert native['requests'][0]['max_completion_tokens'] <= 1024
    assert all('tools' not in body and body['response_format']['type'] == 'json_schema' for body in native['requests'])
    fields = {fact['field_name']: fact for fact in reply['facts']}
    if selection['topics'] == ['connectivity']:
        assert fields['filter_status']['value'] == selection['status']
        assert fields['destination_type']['value'] == selection['destination_type'] if selection['destination_type'] != 'all' else True
        assert reply['lists'][0]['filters']['status'] == selection['status']
        assert 'water_km2' not in fields
    elif selection['topics'] == ['infrastructure']:
        assert fields['filter_exposure']['value'] == 'not_exposed'
        assert 'roads_exposed' not in fields
    else:
        assert set(fields) == {'water_eligible_coverage_pct'}


def test_semantic_plan_and_answer_are_reused_together_and_invalidate_on_changed_properties(evidence_run, native):
    question = 'Which hamlets retain a path to medical care?'
    first = answer(evidence_run, question)
    repeated = answer(evidence_run, question)
    assert first['facts'] == repeated['facts'] and first['answer'] == repeated['answer']
    assert repeated['generation']['cache_hit'] and repeated['generation']['provider_requests'] == 0
    assert repeated['generation']['prompt_tokens'] == repeated['generation']['completion_tokens'] == 0
    assert len(native['requests']) == 2
    props = evidence_run['layers']['settlements']['features'][0]['properties']
    props['connectivity_by_destination_type']['hospital']['status'] = 'connected'
    changed = answer(evidence_run, question)
    assert not changed['generation']['cache_hit']
    assert len(native['requests']) == 4


def test_simultaneous_semantic_queries_share_planning_and_composition(evidence_run, native):
    native.update(entered=threading.Event(), release=threading.Event())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(answer, evidence_run, 'Which hamlets retain a path to medical care?')
        assert native['entered'].wait(1)
        second = executor.submit(answer, evidence_run, 'Which hamlets retain a path to medical care?')
        native['release'].set()
        replies = [first.result(), second.result()]
    assert len(native['requests']) == 2
    assert sorted(reply['generation']['cache_hit'] for reply in replies) == [False, True]


@pytest.mark.parametrize('selection', [
    query(destination_type='invented_facility'),
    query(topics=['flood'], requested_fields=['population'], status='all', destination_type='all'),
    query(topics=['feature'], features=[{'layer': 'roads', 'feature_id': 'another-run-road'}]),
    query(response_kind='report'),
])
def test_invalid_semantic_scopes_do_not_retrieve_invented_or_cross_run_evidence(evidence_run, native, selection):
    native['query'] = selection
    reply = answer(evidence_run, 'Which hamlets retain a path to medical care?')
    assert reply['generation']['reason'] == 'invalid_query_plan'
    assert len(native['requests']) == 1
    assert not reply['facts']


def test_failed_composition_uses_the_validated_semantic_scope_for_fallback(evidence_run, native):
    context = EvidenceContext(evidence_run)
    native['wrong_draft'] = AnswerDraft(run_id=context.run_id, language='en', kind='answer', sections=[
        AnswerSection(topic='flood', claims=[claim_for(context.global_fields['water_km2'])], explanations=[])]).model_dump(mode='json')
    reply = answer(evidence_run, 'Which hamlets retain a path to medical care?')
    assert reply['generation']['reason'] == 'validation_failed'
    assert reply['generation']['query_planning'] == 'semantic'
    assert reply['generation']['provider_requests'] == 3  # Plan + composition + one bounded repair.
    assert reply['lists'][0]['filters']['destination_type'] == 'hospital'
    assert not any(f['field_name'] == 'water_km2' for f in reply['facts'])


def test_rate_limit_on_planning_enters_account_cooldown(evidence_run, native, monkeypatch):
    native['http_status'] = 429
    monkeypatch.setenv('LLM_MAX_RATE_LIMIT_RETRIES', '0')
    monkeypatch.setenv('LLM_PROVIDER_COOLDOWN_SECONDS', '30')
    first = answer(evidence_run, 'Which hamlets retain a path to medical care?')
    second = answer(evidence_run, 'Which hamlets retain a path to medical care?')
    assert first['generation']['reason'] == second['generation']['reason'] == 'rate_limited'
    assert len(native['requests']) == 1
    assert second['generation']['provider_requests'] == 0


def test_obviously_unavailable_casualties_spend_no_planning_or_composition_calls(evidence_run, native):
    reply = answer(evidence_run, 'How many people died?')
    assert reply['intent'] == 'unsupported' and not reply['facts']
    assert not native['requests']


def test_unavailable_measurement_is_not_authored_by_the_planner(evidence_run, native):
    native['query'] = query(topics=['flood'], status='all', destination_type='all', listing=False, requested_fields=['water_km2'])
    evidence_run['stats'].pop('water_km2')
    reply = answer(evidence_run, 'What area was newly submerged?')
    fact = next(f for f in reply['facts'] if f['field_name'] == 'water_km2')
    assert fact['value'] is None and fact['status'] == 'missing'
    assert 'Unavailable' in reply['answer']


@pytest.mark.parametrize('missing', [False, True])
def test_semantic_context_distinguishes_empty_inventory_from_missing_layer(evidence_run, native, missing):
    native['query'] = query(topics=['inventory'], status='all', inventory_layers=['destinations'], listing=False)
    if missing:
        evidence_run['layers'].pop('destinations')
    else:
        evidence_run['layers']['destinations']['features'] = []
    reply = answer(evidence_run, 'How many medical facilities are recorded?')
    payload = json.loads(native['requests'][0]['messages'][1]['content'])
    assert ('destinations' not in payload['available_layers']) == missing
    fact = next(f for f in reply['facts'] if f['field_name'] == 'inventory_count')
    assert fact['value'] == (None if missing else 0)


@pytest.mark.parametrize('language', ['en', 'ne'])
def test_primary_semantic_situation_report_remains_bound_to_saved_metrics(evidence_run, native, language):
    from app.report import build_report_data
    native.update(language=language, query=query(response_kind='report',
        topics=['flood', 'infrastructure', 'connectivity', 'sources', 'limitations'],
        status='all', destination_type='all', listing=False))
    reply = answer(evidence_run, 'Give a short situation report.', language)
    assert reply['generation']['mode'] == 'llm'
    report = build_report_data(evidence_run, language, reply)
    values = {fact.field_name: fact.value for fact in report.facts}
    assert values['water_km2'] == evidence_run['stats']['water_km2']
    assert values['roads_exposed'] == evidence_run['stats']['roads_exposed']
    assert report.generation.query_planning == 'semantic'


def test_primary_named_feature_query_contains_only_that_subject(evidence_run, native):
    road = evidence_run['layers']['roads']['features'][0]['properties']
    native['query'] = query(topics=['feature'], status='all', destination_type='all', listing=False,
        features=[{'layer': 'roads', 'feature_id': road['id']}])
    reply = answer(evidence_run, f"Tell me about {road['name']}.")
    assert reply['generation']['mode'] == 'llm'
    assert {fact['subject_id'] for fact in reply['facts']} == {road['id']}


def test_all_exposure_categories_survive_semantic_composition_failure(evidence_run, native):
    native['query'] = query(topics=['infrastructure'], status='all', destination_type='all',
        asset_types=['roads'], exposure='all')
    context = EvidenceContext(evidence_run)
    native['wrong_draft'] = AnswerDraft(run_id=context.run_id, language='en', kind='answer', sections=[
        AnswerSection(topic='flood', claims=[claim_for(context.global_fields['water_km2'])], explanations=[])]).model_dump(mode='json')
    reply = answer(evidence_run, 'Show every transport link with its assessment.')
    assert reply['generation']['reason'] == 'validation_failed'
    assert reply['lists'][0]['filters']['exposure'] == 'all'
    assert reply['lists'][0]['total_count'] == len(evidence_run['layers']['roads']['features'])


def test_semantic_planning_preserves_supported_clause_without_requesting_unavailable_statistics(evidence_run, native):
    native['query'] = query(topics=['flood'], status='all', destination_type='all', listing=False, requested_fields=['water_km2'])
    reply = answer(evidence_run, 'How much new water was detected, and how many people died?')
    payload = json.loads(native['requests'][0]['messages'][1]['content'])
    assert 'died' not in payload['question']
    assert reply['unsupported_parts']
    assert next(f['value'] for f in reply['facts'] if f['field_name'] == 'water_km2') == evidence_run['stats']['water_km2']
