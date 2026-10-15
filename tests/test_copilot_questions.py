"""Paraphrases and contextual retrieval; all observations here are synthetic."""
import json
from types import SimpleNamespace
import uuid

import pytest

from app.copilot import answer
from app.evidence import EvidenceContext
from app.grounding import claim_for
from app.llm import ToolCall
from app.copilot_query import plan_question
from app.copilot_service import _provider_payload, _query_tools


@pytest.fixture(autouse=True)
def offline_fallback(monkeypatch):
    monkeypatch.setenv('LLM_ENABLED', 'false')


@pytest.fixture
def route_run(evidence_run):
    def feature(identifier, name, **properties):
        return {'type': 'Feature', 'geometry': None,
                'properties': {'id': identifier, 'name': name, **properties}}

    def route(*ids):
        return {'type': 'Feature', 'properties': {'road_ids': list(ids)}}

    evidence_run['layers']['roads']['features'] = [
        feature('r-1', 'River Road', exposed=True, assessment_status='potential_exposure',
                assessment_fraction=1.0, length_m=200.0, exposed_length_m=80.0),
        feature('r-2', 'Mountain Road', exposed=False, assessment_status='no_mapped_change_at_observed_samples',
                assessment_fraction=1.0, length_m=300.0, exposed_length_m=0.0),
        feature('r-3', 'Unused Road', exposed=False),
    ]
    evidence_run['layers']['settlements']['features'] = [
        feature('s-1', 'Lowland', status='cut_off', baseline_route=route('r-1'),
                nearest_destination='Hospital', before_distance_km=2.0, after_distance_km=None),
        feature('s-2', 'Ridge', status='connected', baseline_route=route('r-2'), after_route=route('r-2'),
                nearest_destination='Hospital', before_distance_km=3.0, after_distance_km=3.0),
        feature('s-3', 'Bend', status='connected', baseline_route=route('r-1'), after_route=route('r-2'),
                nearest_destination='Hospital', before_distance_km=2.0, after_distance_km=4.0),
        feature('s-4', 'Unknown', status='unassessed'),
    ]
    # An aggregate must never be used as the answer to a road-filtered query.
    evidence_run['stats']['settlements_cut_off'] = 99
    return evidence_run


def fields(reply):
    return {fact['field_name'] for fact in reply['facts']}


def history(run, *messages):
    return [{'role': role, 'content': content, 'run_id': run['id']} for role, content in messages]


@pytest.mark.parametrize('question,expected', [
    ('How much of the study area could actually be observed?', {'valid_coverage_pct'}),
    ('Give a breakdown of candidate inundation versus debris.', {'water_km2', 'debris_km2'}),
    ('How many structures overlap the change mask?', {'buildings_exposed'}),
    ('Is the evidence strong enough to call a bridge destroyed?', {'limitations', 'bridges_exposed'}),
    ('Explain road exposure and new inundation area.', {'roads_exposed', 'water_km2'}),
    ('अवलोकन क्षेत्र र डुबान क्षेत्र कति छ?', {'valid_coverage_pct', 'water_km2'}),
])
def test_supported_paraphrases_retrieve_requested_evidence(evidence_run, question, expected):
    reply = answer(evidence_run, question)
    assert reply['intent'] != 'unsupported'
    assert expected.issubset(fields(reply))
    assert all(f['run_id'] == evidence_run['id'] for f in reply['facts'])


def test_named_feature_does_not_return_unrelated_aggregate(route_run):
    reply = answer(route_run, 'What is the status of Ridge?')
    assert reply['intent'] == 'feature'
    assert {f['subject_id'] for f in reply['facts']} == {'s-2'}
    assert 'Modeled route available' in reply['answer']
    assert '99 settlements' not in reply['answer']


def test_road_followup_uses_saved_route_membership_and_ignores_history_numbers(route_run):
    turns = history(route_run, ('user', 'Tell me about River Road.'),
                    ('assistant', 'River Road r-1 has 8888 villages.'))
    reply = answer(route_run, 'What about the villages on that road?', history=turns)
    by_field = {f['field_name']: f for f in reply['facts'] if f['subject_type'] == 'analysis'}
    assert reply['intent'] == 'access'
    assert by_field['matching_settlements_count']['value'] == 2
    assert by_field['route_membership_unavailable_count']['value'] == 1
    assert by_field['filter_road_id']['value'] == 'r-1'
    assert {f['subject_id'] for f in reply['facts'] if f['subject_type'] == 'settlements'} == {'s-1', 's-3'}
    assert '8888' not in reply['answer'] and 'settlements_cut_off' not in fields(reply)


def test_explicit_road_and_status_filter(route_run):
    reply = answer(route_run, 'Which isolated villages rely on River Road?')
    count = next(f for f in reply['facts'] if f['field_name'] == 'matching_settlements_count')
    assert count['value'] == 1
    assert {f['subject_id'] for f in reply['facts'] if f['subject_type'] == 'settlements'} == {'s-1'}


@pytest.mark.parametrize('turns', [[], [('user', 'Compare River Road and Mountain Road.')]])
def test_ambiguous_road_reference_requests_clarification(route_run, turns):
    reply = answer(route_run, 'What about the villages on that road?', history=history(route_run, *turns))
    assert reply['intent'] == 'clarification' and not reply['facts']
    assert reply['generation']['reason'] == 'ambiguous_reference'
    assert 'Please give' in reply['answer']


def test_unknown_named_road_does_not_substitute_a_previous_road(route_run):
    turns = history(route_run, ('user', 'Explain River Road.'))
    reply = answer(route_run, 'Which villages rely on Atlantis Road?', history=turns)
    assert reply['intent'] == 'clarification' and not reply['facts']


def test_duplicate_feature_names_are_ambiguous(route_run):
    route_run['layers']['roads']['features'][1]['properties']['name'] = 'River Road'
    reply = answer(route_run, 'Explain River Road.')
    assert reply['intent'] == 'clarification' and not reply['facts']


def test_longer_feature_name_does_not_match_shorter_feature(route_run):
    route_run['layers']['roads']['features'][1]['properties']['name'] = 'River Road Extension'
    reply = answer(route_run, 'Explain River Road Extension.')
    assert {f['subject_id'] for f in reply['facts']} == {'r-2'}


def test_generic_followup_and_translation_retain_measurement_scope(evidence_run):
    turns = history(evidence_run, ('user', 'Give a breakdown of candidate inundation versus debris.'))
    en = answer(evidence_run, 'How much?', history=turns)
    ne = answer(evidence_run, 'Translate that into Nepali.', 'ne', history=turns)
    assert {'water_km2', 'debris_km2'}.issubset(fields(en))
    assert en['facts'] == ne['facts']


def test_cross_run_context_is_rejected_before_retrieval(route_run):
    with pytest.raises(ValueError, match='different analysis'):
        answer(route_run, 'What about that road?', history=[{'role': 'user', 'content': 'River Road', 'run_id': str(uuid.uuid4())}])


@pytest.mark.parametrize('question', ['How many villagers died?', 'List missing persons.', 'Estimate the fatalities.',
                                      'What is the village population?', 'Predict tomorrow’s weather over the flooded roads.'])
def test_unsupported_statistics_and_external_topics_remain_unavailable(route_run, question):
    reply = answer(route_run, question)
    assert reply['intent'] == 'unsupported' and not reply['facts']


def test_road_tool_counts_before_pagination_and_preserves_missing_routes(route_run):
    context = EvidenceContext(route_run)
    first = context.call('settlement_connectivity', {'status': 'all', 'offset': 0, 'limit': 1, 'road_id': 'r-1'})
    second = context.call('settlement_connectivity', {'status': 'all', 'offset': 1, 'limit': 1, 'road_id': 'r-1'})
    assert first.feature_ids == ['s-1'] and second.feature_ids == ['s-3']
    assert first.next_offset == 1 and second.next_offset is None
    assert next(f.value for f in first.facts if f.field_name == 'matching_settlements_count') == 2
    assert next(f.value for f in first.facts if f.field_name == 'route_membership_unavailable_count') == 1
    assert context.call('settlement_connectivity', {'status': 'all', 'offset': 0, 'limit': 1, 'road_id': '/etc/passwd'}).status == 'not_found'


def test_missing_settlement_layer_is_unavailable_instead_of_zero(route_run):
    route_run['layers'].pop('settlements')
    reply = answer(route_run, 'Which villages rely on River Road?')
    count = next(f for f in reply['facts'] if f['field_name'] == 'matching_settlements_count')
    assert count['value'] is None and count['status'] == 'missing'
    assert 'Unavailable' in reply['answer']


@pytest.fixture
def scoped_provider(monkeypatch):
    monkeypatch.setenv('LLM_ENABLED', 'true')
    monkeypatch.setenv('LLM_API_KEY', 'unit-test-placeholder')
    monkeypatch.setenv('LLM_APPLICATION_RETRIEVAL', 'false')

    class Provider:
        responses = []
        requests = []

        def __init__(self, config):
            pass

        def create(self, **kwargs):
            self.requests.append(kwargs)
            if kwargs['tool_choice'] == 'auto':
                return SimpleNamespace(output=[], output_text='')
            return self.responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr('app.copilot_service.GroqProvider', Provider)
    return Provider


def llm_output(draft=None, tool=None, args=None):
    return SimpleNamespace(output=[ToolCall(type='function_call', name=tool, arguments=json.dumps(args), call_id='scope')] if tool else [],
                           output_text=json.dumps(draft) if draft else '')


def test_llm_followup_receives_resolved_road_and_answers_with_filtered_evidence(route_run, scoped_provider):
    args = {'status': 'all', 'offset': 0, 'limit': 4, 'road_id': 'r-1'}
    context = EvidenceContext(route_run)
    facts = context.call('settlement_connectivity', args).facts
    selected = [f for f in facts if f.subject_type == 'analysis' or f.field_name in ('status', 'baseline_road_ids')]
    draft = {'run_id': route_run['id'], 'language': 'en', 'kind': 'answer',
             'sections': [{'topic': 'connectivity', 'claims': [claim_for(f).model_dump() for f in selected], 'explanations': []}]}
    scoped_provider.responses = [llm_output(tool='settlement_connectivity', args=args), llm_output(draft=draft)]
    turns = history(route_run, ('user', 'Tell me about River Road.'), ('assistant', 'River Road · r-1'))
    reply = answer(route_run, 'What about the villages on that road?', history=turns)
    assert reply['generation']['mode'] == 'llm'
    request = json.loads(scoped_provider.requests[0]['items'][0]['content'])
    assert request['resolved_scope']['road_id'] == 'r-1' and request['history'] == turns
    assert {f['subject_id'] for f in reply['facts'] if f['subject_type'] == 'settlements'} == {'s-1', 's-3'}


@pytest.mark.parametrize('wrong', ['aggregate', 'other_road', 'other_status'])
def test_valid_but_irrelevant_llm_facts_are_rejected(route_run, scoped_provider, wrong):
    context = EvidenceContext(route_run)
    args = {'status': 'all', 'offset': 0, 'limit': 4, 'road_id': 'r-1'}
    if wrong == 'aggregate':
        tool, args = 'event_summary', {}
        selected = [context.call(tool, args).facts[9]]
    else:
        tool = 'settlement_connectivity'
        args.update({'road_id': 'r-2'} if wrong == 'other_road' else {'status': 'cut_off'})
        selected = [f for f in context.call(tool, args).facts if f.subject_type == 'analysis']
    draft = {'run_id': route_run['id'], 'language': 'en', 'kind': 'answer',
             'sections': [{'topic': 'connectivity', 'claims': [claim_for(f).model_dump() for f in selected], 'explanations': []}]}
    scoped_provider.responses = [llm_output(tool=tool, args=args), llm_output(draft=draft), llm_output(draft=draft)]
    reply = answer(route_run, 'Which villages rely on River Road?')
    assert reply['generation']['reason'] == 'validation_failed'
    count = next(f for f in reply['facts'] if f['field_name'] == 'matching_settlements_count')
    assert count['value'] == 2 and 'settlements_cut_off' not in fields(reply)


def test_ambiguous_reference_does_not_call_llm(route_run, scoped_provider):
    reply = answer(route_run, 'What about the villages on that road?')
    assert reply['intent'] == 'clarification' and not scoped_provider.requests


def test_casualty_translation_cannot_reuse_a_supported_previous_topic(route_run, scoped_provider):
    reply = answer(route_run, 'Translate the population estimate into Nepali.', 'ne',
                   history=history(route_run, ('user', 'Explain road exposure.')))
    assert reply['intent'] == 'unsupported' and not reply['facts'] and not scoped_provider.requests


def test_compact_tool_payload_preserves_claim_identity_and_missing_values(route_run):
    context = EvidenceContext(route_run)
    for tool, args in [('event_summary', {}), ('feature_evidence', {'layer': 'settlements', 'feature_id': 's-1'})]:
        result = context.call(tool, args)
        payload = json.loads(_provider_payload(result))
        for original, compact in zip(result.facts, payload['facts'], strict=True):
            restored = {**payload['fact_defaults'], **payload['subjects'].get(compact.get('subject_ref'), {}), **compact}
            for key in ('run_id', 'subject_type', 'subject_id', 'fact_id', 'field_name', 'value', 'status', 'interpretation'):
                assert restored[key] == getattr(original, key)
        assert len(_provider_payload(result)) < len(result.model_dump_json())


def test_scoped_tool_selection_does_not_gate_general_llm_questions(route_run):
    context = EvidenceContext(route_run)
    coverage = plan_question(context, 'How much of the study area could actually be observed?', [])
    assert [tool['name'] for tool in _query_tools(coverage)] == ['flood_surface_change']
    general = plan_question(context, 'How do the road observations affect confidence?', [])
    assert len(_query_tools(general)) == 7
    for tool in _query_tools(general):
        limit = tool['parameters'].get('properties', {}).get('limit')
        if limit:
            assert limit['maximum'] == 5
