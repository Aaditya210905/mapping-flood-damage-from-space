"""Synthetic regressions for scope shared by answers, lists and provider drafts."""
import pytest

from app.copilot import answer
from app.copilot_query import plan_question
from app.evidence import EvidenceContext


@pytest.fixture
def semantic_run(evidence_run, monkeypatch):
    monkeypatch.setenv('LLM_ENABLED', 'false')

    def feature(identifier, **props):
        return {'type': 'Feature', 'geometry': None,
                'properties': {'id': 'fixture-' + identifier, 'name': 'Test feature ' + identifier, **props}}

    evidence_run['layers']['roads']['features'] = [
        feature('exposed', exposed=True, assessment_status='potential_exposure'),
        feature('clear', exposed=False, assessment_status='no_mapped_change_at_observed_samples',
                assessment_complete=True),
        feature('partial', exposed=False, assessment_status='unassessed_or_partial',
                assessment_complete=False),
        feature('unknown', exposed=False),
    ]
    evidence_run['layers']['settlements']['features'] = [
        feature('lost', status='cut_off', connectivity_by_destination_type={
            'hospital': {'status': 'cut_off'}, 'town': {'status': 'connected'}}),
        feature('baseline', status='baseline_disconnected', connectivity_by_destination_type={
            'hospital': {'status': 'baseline_disconnected'}, 'town': {'status': 'baseline_disconnected'}}),
        feature('connected', status='connected', connectivity_by_destination_type={
            'hospital': {'status': 'connected'}, 'town': {'status': 'cut_off'}}),
        feature('uncertain', status='uncertain_access', connectivity_by_destination_type={
            'hospital': {'status': 'uncertain_access'}, 'town': {'status': 'unassessed'}}),
        feature('unassessed', status='unassessed'),
    ]
    evidence_run['stats']['settlements_cut_off'] = 999
    evidence_run['stats']['roads_exposed'] = 888
    return evidence_run


@pytest.mark.parametrize('question,exposure,ids', [
    ('Which roads are not exposed?', 'not_exposed', {'clear'}),
    ('List roads without mapped exposure.', 'not_exposed', {'clear'}),
    ('कुन सडक प्रभावित छैनन्?', 'not_exposed', {'clear'}),
    ('Which roads are unassessed?', 'unassessed', {'partial', 'unknown'}),
    ('How many roads are unassessed?', 'unassessed', {'partial', 'unknown'}),
    ('List exposed roads.', 'exposed', {'exposed'}),
])
def test_exposure_scope_does_not_turn_missing_observations_into_clear_roads(semantic_run, question, exposure, ids):
    plan = plan_question(EvidenceContext(semantic_run), question, [])
    assert plan.exposure == exposure
    reply = answer(semantic_run, question)
    if plan.listing:
        listing = next(item for item in reply['lists'] if item['layer'] == 'roads')
        assert listing['filters']['exposure'] == exposure
        assert {row['feature_id'] for row in listing['rows']} == {'fixture-' + value for value in ids}
        assert listing['total_count'] == len(ids)
    count = next(f for f in reply['facts'] if f['field_name'] == 'matching_assets_count')
    assert count['value'] == len(ids)
    assert not any(f['field_name'] == 'roads_exposed' for f in reply['facts'])


@pytest.mark.parametrize('question,status,destination,ids', [
    ('Which villages are not connected to a hospital?', 'not_connected', 'hospital', {'lost', 'baseline'}),
    ('Which villages cannot reach a hospital?', 'not_connected', 'hospital', {'lost', 'baseline'}),
    ('अस्पताल पुग्न नसक्ने गाउँ कुन हुन्?', 'not_connected', 'hospital', {'lost', 'baseline'}),
    ('Which villages were already disconnected before the flood?', 'baseline_disconnected', 'all', {'baseline'}),
    ('List villages disconnected before the event.', 'baseline_disconnected', 'all', {'baseline'}),
    ('Which villages lost access after the flood?', 'cut_off', 'all', {'lost'}),
    ('Which villages can reach either a hospital or a town?', 'connected', 'all', {'connected'}),
    ('Which villages can reach either a town or a hospital?', 'connected', 'all', {'connected'}),
    ('Which villages are not disconnected from a hospital?', 'connected', 'hospital', {'connected'}),
])
def test_access_filters_and_counts_are_query_bound(semantic_run, question, status, destination, ids):
    plan = plan_question(EvidenceContext(semantic_run), question, [])
    assert (plan.status, plan.destination_type) == (status, destination)
    assert 'flood' not in plan.topics  # A temporal modifier is not a water-area request.
    reply = answer(semantic_run, question)
    listing = next(item for item in reply['lists'] if item['layer'] == 'settlements')
    assert {row['feature_id'] for row in listing['rows']} == {'fixture-' + value for value in ids}
    assert listing['total_count'] == len(ids)
    count = next(f for f in reply['facts'] if f['field_name'] == 'matching_settlements_count')
    assert count['value'] == len(ids)
    assert not any(f['field_name'] == 'settlements_cut_off' for f in reply['facts'])
    unavailable = next(f for f in reply['facts'] if f['field_name'] == 'destination_assessment_unavailable_count')
    assert unavailable['value'] == 2


def test_legacy_destination_results_are_missing_in_answer_and_list(semantic_run):
    for item in semantic_run['layers']['settlements']['features']:
        item['properties'].pop('connectivity_by_destination_type', None)
    reply = answer(semantic_run, 'Which villages cannot reach a hospital?')
    count = next(f for f in reply['facts'] if f['field_name'] == 'matching_settlements_count')
    assert count['value'] is None and count['status'] == 'missing'
    assert reply['lists'][0]['total_count'] is None


@pytest.mark.parametrize('question,road_ids,settlement_ids', [
    ('List roads that are not exposed and villages that can reach a hospital.', {'clear'}, {'connected'}),
    ('List unassessed roads and connected villages.', {'partial', 'unknown'}, {'connected'}),
])
def test_compound_asset_and_access_predicates_do_not_overwrite_each_other(semantic_run, question, road_ids, settlement_ids):
    reply = answer(semantic_run, question)
    lists = {item['layer']: item for item in reply['lists']}
    assert set(lists) == {'roads', 'settlements'}
    for layer, expected in [('roads', road_ids), ('settlements', settlement_ids)]:
        assert {row['feature_id'] for row in lists[layer]['rows']} == {'fixture-' + identifier for identifier in expected}


@pytest.mark.parametrize('question,tool,wrong_arguments,topic', [
    ('Which roads are not exposed?', 'infrastructure_impacts',
     {'asset_type': 'roads', 'only_exposed': True, 'exposure': 'exposed', 'offset': 0, 'limit': 5}, 'infrastructure'),
    ('Which villages are not connected to a hospital?', 'settlement_connectivity',
     {'status': 'connected', 'destination_type': 'hospital', 'offset': 0, 'limit': 5}, 'connectivity'),
    ('Which villages were already disconnected before the flood?', 'settlement_connectivity',
     {'status': 'cut_off', 'offset': 0, 'limit': 5}, 'connectivity'),
    ('Which villages can reach either a town or a hospital?', 'settlement_connectivity',
     {'status': 'connected', 'destination_type': 'hospital', 'offset': 0, 'limit': 5}, 'connectivity'),
])
def test_true_but_wrong_scope_provider_claims_are_rejected(semantic_run, question, tool, wrong_arguments, topic):
    from app.copilot_service import _fallback_draft, _validate_relevance
    from app.copilot_types import AnswerDraft, AnswerSection
    from app.grounding import GroundingError, claim_for, validate_draft

    context = EvidenceContext(semantic_run)
    plan = plan_question(context, question, [])
    good = _fallback_draft(context, plan, 'en')
    _validate_relevance(good, context, plan)
    wrong = context.call(tool, wrong_arguments)
    draft = AnswerDraft(run_id=context.run_id, language='en', kind='answer', sections=[
        AnswerSection(topic=topic, claims=[claim_for(f) for f in wrong.facts if f.subject_type == 'analysis'], explanations=[])])
    validate_draft(draft, context, 'en')  # Authentic facts alone are insufficient.
    with pytest.raises(GroundingError):
        _validate_relevance(draft, context, plan)


def test_compound_inventory_counts_retain_each_layer_and_water_measurement(semantic_run):
    reply = answer(semantic_run, 'How many roads and buildings are mapped, and what is the water area?')
    counts = {f['subject_name']: f['value'] for f in reply['facts'] if f['field_name'] == 'inventory_count'}
    assert counts['roads'] == 4
    assert counts['buildings'] == len(semantic_run['layers']['buildings']['features'])
    assert any(f['field_name'] == 'water_km2' for f in reply['facts'])


def test_conjunctive_destination_requirements_are_not_silently_treated_as_either(semantic_run):
    reply = answer(semantic_run, 'Which villages can reach both a hospital and a town?')
    assert reply['intent'] == 'clarification'
    assert reply['generation']['reason'] == 'destination_combination'
    assert 'both destinations' in reply['answer']
    assert not reply['facts'] and not reply['lists']


def test_fallback_preserves_every_topic_when_feature_detail_uses_the_claim_budget(evidence_run, monkeypatch):
    import copy
    from app.copilot_service import _fallback_draft, _validate_relevance
    from app.copilot_query import QueryPlan
    from app.grounding import validate_draft
    for layer in ('roads', 'buildings', 'bridges'):
        prototype = evidence_run['layers'][layer]['features'][0]
        evidence_run['layers'][layer]['features'] = []
        for index in range(8):
            feature = copy.deepcopy(prototype)
            feature['properties'].update(id=f'{layer}-{index}', exposed=True)
            evidence_run['layers'][layer]['features'].append(feature)
    context = EvidenceContext(evidence_run)
    plan = QueryPlan('compound', None, ['flood', 'infrastructure', 'connectivity', 'sources', 'limitations'],
                     'All supported topics', assets=['roads', 'buildings', 'bridges'], listing=True)
    draft = _fallback_draft(context, plan, 'en')
    validate_draft(draft, context, 'en')
    _validate_relevance(draft, context, plan)
    assert {section.topic for section in draft.sections} == set(plan.topics)
