"""Regressions for the project audit; synthetic inputs and mocked HTTP only."""
import copy
from datetime import datetime, timezone
from io import BytesIO
import json
from types import SimpleNamespace

from groq import Groq
import httpx
import numpy as np
import rasterio
from pydantic import SecretStr
from pypdf import PdfReader
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from app.availability import normalize_analysis_availability
from app.copilot import answer
from app.copilot_query import plan_question, mentions
from app.copilot_service import _validate_relevance
from app.copilot_service import _retrieve
from app.copilot_service import _parse_draft
from app.copilot_types import AnswerDraft, AnswerSection, NarrativeSentence
from app.evidence import EvidenceContext
from app.geo import Grid, detect_changes
from app.grounding import GroundingError, claim_for, validate_draft, render_draft
from app.llm import GroqProvider, LLMConfig, ProviderFailure
from app.models import AnalysisRequest
from app.presets import TRISHULI, osm_cutoff
from app.providers import select_pair, choose_snapshot
from app.report import build_report_data, report_pdf
from app.settings import MAPPING
from app.evaluation import joint_hazard_observations


@pytest.fixture(autouse=True)
def no_live_provider(monkeypatch):
    monkeypatch.setenv('LLM_ENABLED', 'false')
    monkeypatch.setenv('LLM_APPLICATION_RETRIEVAL', 'true')


def fields(reply):
    return {fact['field_name']: fact for fact in reply['facts']}


def test_custom_requests_do_not_inherit_the_nepal_area_label():
    assert AnalysisRequest(case_id="trishuli", bbox=[75.0,10.0,75.01,10.01]).name=='Custom flood analysis'
    assert AnalysisRequest(case_id="trishuli", flood_date='2026-09-01').name=='Custom flood analysis'
    assert AnalysisRequest(case_id="trishuli").name==TRISHULI['name']


def test_asset_comparison_requires_both_hazard_classes_and_the_exact_grid(tmp_path):
    grid=Grid('EPSG:32645',from_origin(400000,3100000,30,30),4,4,30)
    observed=np.ones((4,4),dtype=bool)
    assert not joint_hazard_observations(tmp_path,grid,observed).any()
    bands=np.ones((3,4,4),dtype='uint8');bands[2]=0
    def write(transform):
        with rasterio.open(tmp_path/'eligibility.tif','w',driver='GTiff',width=4,height=4,count=3,
                           dtype='uint8',crs=grid.crs,transform=transform) as target:target.write(bands)
    write(grid.transform)
    assert not joint_hazard_observations(tmp_path,grid,observed).any()
    bands[2,:,2:]=1;write(grid.transform)
    assert joint_hazard_observations(tmp_path,grid,observed).sum()==8
    write(grid.transform@rasterio.Affine.translation(1,0))
    with pytest.raises(ValueError,match='match the prediction grid'):
        joint_hazard_observations(tmp_path,grid,observed)


def test_clear_optical_baseline_water_cannot_be_new_radar_water():
    grid = Grid('EPSG:32645', from_origin(400000, 3100000, 30, 30), 11, 11, 30)
    size = (11, 11)
    before = np.stack([np.full(size, .1), np.ones(size), np.zeros(size)])
    after = before.copy(); after[0] = .001
    optical = np.stack([np.full(size, .4), np.full(size, .1), np.full(size, .1), np.full(size, .1), np.full(size, 6), np.ones(size)])
    detected = detect_changes(before, after, grid, AnalysisRequest(case_id="trishuli", min_component_pixels=1), np.zeros(size), optical, optical)
    assert detected['permanent'].all()
    assert not detected['water'].any()
    assert np.all(detected['classes'] == 1)


def test_rounded_coverage_does_not_discard_observed_pixels():
    run = {'stats': {'water_km2': .009}, 'class_availability': {'water': {
        'status': 'partial', 'eligible_pixels': 10, 'aoi_pixels': 1000000, 'coverage_pct': 0.0}}}
    normalized = normalize_analysis_availability(run)
    assert normalized['class_availability']['water']['status'] == 'partial'
    assert normalized['class_availability']['water']['coverage_pct'] == .001
    assert normalized['stats']['water_km2'] == .009
    assert run['stats']['water_km2'] == .009


def test_new_saved_coverage_and_read_time_evidence_agree(evidence_run):
    normalized = normalize_analysis_availability(evidence_run)
    for kind in ('water', 'debris'):
        field = kind + '_eligible_coverage_pct'
        assert evidence_run['stats'][field] == evidence_run['class_availability'][kind]['coverage_pct']
        assert normalized['stats'][field] == evidence_run['stats'][field]


def test_scene_selection_rejects_vv_only_before_acquisition():
    params = AnalysisRequest(case_id="trishuli")
    def item(day, polarizations):
        return {'id': day + '-'.join(polarizations), 'geometry': mapping(box(*params.bbox)),
                'properties': {'datetime': day + 'T00:00:00Z', 'sar:polarizations': polarizations,
                               'sar:instrument_mode': 'IW', 'platform': 'sentinel-1a',
                               'sat:relative_orbit': 1, 'sat:orbit_state': 'ascending'}}
    vv = [item(day, ['VV']) for day in ('2026-08-25', '2026-08-27')]
    with pytest.raises(ValueError, match='No same-platform'):
        select_pair(vv, params)
    dv = [item(day, ['VV', 'VH']) for day in ('2026-08-20', '2026-09-01')]
    assert [scene.id for scene in select_pair(vv + dv, params)] == [record['id'] for record in dv]


def test_case_cutoff_is_geographic_and_custom_cases_do_not_inherit_it():
    metadata = {'temporalExtent': {'end': '2026-10-01T00:00:00Z'}}
    event = datetime(2026, 8, 26, tzinfo=timezone.utc)
    nepal = AnalysisRequest(case_id="trishuli")
    elsewhere = AnalysisRequest(case_id="trishuli", bbox=[10, 10, 10.1, 10.1])
    custom = AnalysisRequest(case_id='custom',bbox=TRISHULI['bbox'],flood_date=TRISHULI['flood_date'])
    assert osm_cutoff(nepal).isoformat().startswith('2026-07-27')
    for params in (elsewhere, custom):
        assert osm_cutoff(params) is None
        assert choose_snapshot(metadata, event, cutoff=osm_cutoff(params)).isoformat().startswith('2026-08-25')
    assert AnalysisRequest(case_id="trishuli").bbox == TRISHULI['bbox']
    assert AnalysisRequest(case_id="trishuli").water_threshold_db == MAPPING.water_threshold_db


@pytest.mark.parametrize('question,expected', [
    ('What is the radar coverage?', 'radar_valid_coverage_pct'),
    ('What is the optical coverage?', 'optical_clear_pct'),
    ('What is the water threshold?', 'water_threshold_db'),
    ('What is the radar change threshold?', 'change_threshold_db'),
])
def test_specific_measurements_do_not_become_generic_area_answers(evidence_run, question, expected):
    reply = answer(evidence_run, question)
    selected = fields(reply)
    assert expected in selected
    assert selected[expected]['value'] == (evidence_run['stats'].get(expected) if expected.endswith('pct') else evidence_run['parameters'][expected])
    assert set(selected) == {expected}


@pytest.mark.parametrize('question,required',[
    ('How much new water was detected, and how much of the area was eligible?',{'water_km2','water_eligible_coverage_pct'}),
    ('What are water and debris eligibility?',{'water_eligible_coverage_pct','debris_eligible_coverage_pct'}),
    ('What is radar coverage and water eligibility?',{'radar_valid_coverage_pct','water_eligible_coverage_pct'}),
    ('What are radar and optical coverage?',{'radar_valid_coverage_pct','optical_clear_pct'}),
    ('What is the radar coverage and water threshold?',{'radar_valid_coverage_pct','water_threshold_db'}),
])
def test_compound_coverage_qualifiers_preserve_each_requested_measurement(evidence_run,question,required):
    reply=answer(evidence_run,question)
    assert set(fields(reply))==required
    assert reply['draft']['kind']=='answer'


def test_a_question_cannot_silently_become_a_situation_report(evidence_run):
    context=EvidenceContext(evidence_run)
    draft=narrative_draft(context).model_copy(update={'kind':'report'})
    with pytest.raises(GroundingError,match='answer kind'):
        _validate_relevance(draft,context,plan_question(context,'How much new water was detected?',[]))


def test_hospital_inventory_is_not_hospital_access_or_casualty_refusal(evidence_run):
    destinations = evidence_run['layers']['destinations']['features']
    expected = sum(feature['properties'].get('amenity') == 'hospital' or feature['properties'].get('destination_type') == 'hospital' for feature in destinations)
    reply = answer(evidence_run, 'How many hospitals are mapped?')
    assert reply['intent'] == 'inventory'
    assert fields(reply)['inventory_count']['value'] == expected
    assert 'settlements_cut_off' not in fields(reply)
    assert 'Deaths' not in reply['answer']


def test_supported_part_survives_a_casualty_question(evidence_run):
    reply = answer(evidence_run, 'How many roads are exposed, and how many people died?')
    assert fields(reply)['roads_exposed']['value'] == evidence_run['stats']['roads_exposed']
    assert reply['unsupported_parts']
    assert 'Deaths' in reply['answer']
    assert all(fact['field_name'] not in ('deaths', 'population') for fact in reply['facts'])


def test_all_109_settlements_are_returned_with_bound_list_evidence(evidence_run):
    prototype = evidence_run['layers']['settlements']['features'][0]
    features = []
    for index in range(109):
        feature = copy.deepcopy(prototype)
        feature['properties'].update(id=f's-{index}', name=f'Village {index}', status='connected')
        features.append(feature)
    evidence_run['layers']['settlements']['features'] = features
    reply = answer(evidence_run, 'List all settlements and their access status.')
    listing = reply['lists'][0]
    assert listing['returned_count'] == listing['total_count'] == 109
    assert not listing['truncated']
    assert len({row['feature_id'] for row in listing['rows']}) == 109
    assert listing['rows'][-1]['name'] == 'Village 108'
    ids = {fact['fact_id'] for fact in reply['facts']}
    assert all(set(row['fact_ids']).issubset(ids) for row in listing['rows'])


def test_list_budget_is_explicit_and_has_a_full_export(evidence_run, monkeypatch):
    monkeypatch.setenv('COPILOT_MAX_LIST_RECORDS', '1')
    reply = answer(evidence_run, 'List all settlements and their access status.')
    listing = reply['lists'][0]
    assert listing['returned_count'] == 1 and listing['truncated']
    assert 'Remaining records' in listing['summary']
    assert listing['export_url'].endswith('/exports/settlements.csv')


def test_correct_values_for_the_wrong_question_fail_relevance(evidence_run):
    context = EvidenceContext(evidence_run)
    fact = context.call('flood_surface_change', {}).facts[0]
    draft = AnswerDraft(run_id=context.run_id, language='en', kind='answer', sections=[
        AnswerSection(topic='flood', claims=[claim_for(fact)], explanations=[])])
    validate_draft(draft, context, 'en')
    with pytest.raises(GroundingError, match='every supported'):
        _validate_relevance(draft, context, plan_question(context, 'What infrastructure is exposed?', []))


def narrative_draft(context, language='en', text=None):
    fact = context.call('flood_surface_change', {}).facts[0]
    prefix = 'Here is the recorded finding: ' if language == 'en' else 'अभिलेखित नतिजा: '
    sentence = NarrativeSentence(text=text or prefix + '{fact:' + fact.fact_id + '}.', fact_ids=[fact.fact_id])
    return AnswerDraft(run_id=context.run_id, language=language, kind='answer', sections=[
        AnswerSection(topic='flood', claims=[claim_for(fact)], explanations=[], narrative=[sentence])])


@pytest.mark.parametrize('prose', ['The area is 999 km²: ', 'Two villages: ', '<script>bad</script>', 'The roads are open: ', 'Confirmed damage: '])
def test_generated_narrative_cannot_insert_values_markup_or_outcomes(evidence_run, prose):
    context = EvidenceContext(evidence_run)
    fact = context.call('flood_surface_change', {}).facts[0]
    draft = narrative_draft(context, text=prose + '{fact:' + fact.fact_id + '}.')
    with pytest.raises(GroundingError):
        validate_draft(draft, context, 'en')


def test_narrative_cannot_cite_a_different_feature_or_unclaimed_fact(evidence_run):
    context = EvidenceContext(evidence_run)
    draft = narrative_draft(context)
    draft.sections[0].narrative[0].fact_ids = ['invented']
    with pytest.raises(GroundingError):
        validate_draft(draft, context, 'en')


def test_report_rebinds_compact_summary_limitation_evidence(evidence_run):
    context = EvidenceContext(evidence_run)
    plan = plan_question(context, 'Generate a short situation report', [])
    results = _retrieve(context, plan, 5)
    limitation = next(f for result in results for f in result.facts if f.path == 'limitations.0')
    assert limitation.fact_id in context.returned_fact_ids
    draft = narrative_draft(context)
    draft.sections.append(AnswerSection(topic='limitations', claims=[claim_for(limitation)], explanations=[],
        narrative=[NarrativeSentence(text='The recorded scope also includes {fact:' + limitation.fact_id + '}.', fact_ids=[limitation.fact_id])]))
    response = {'run_id':context.run_id,'language':'en','draft':draft.model_dump(mode='json'),
                'generation':{'mode':'llm','provider':'groq','model':'openai/gpt-oss-120b','reason':None,'validation':'validated_claims'}}
    data = build_report_data(evidence_run, 'en', response)
    assert any('The recorded scope also includes' in line for section in data.sections for line in section.lines)
    assert any(f.fact_id == limitation.fact_id for f in data.facts)
    assert len(PdfReader(BytesIO(report_pdf(evidence_run, 'en', data))).pages) == 1


@pytest.mark.parametrize('language', ['en', 'ne'])
@pytest.mark.parametrize('first_code', [None, 'json_validate_failed'])
def test_native_sdk_generates_grounded_chat_and_report_with_one_bounded_repair(evidence_run, monkeypatch, language, first_code):
    monkeypatch.setenv('LLM_ENABLED', 'true')
    monkeypatch.setenv('GROQ_API_KEY', 'unit-test-placeholder')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    context = EvidenceContext(evidence_run)
    draft = narrative_draft(context, language)
    requests = []
    def handle(request):
        payload = json.loads(request.content); requests.append(payload)
        if first_code and len(requests) == 1:
            return httpx.Response(400, json={'error': {'code': first_code, 'message': 'private diagnostic'}})
        assert 'tools' not in payload and payload['response_format']['json_schema']['strict']
        def check_required(node):
            if isinstance(node, dict):
                if node.get('type') == 'object' and 'properties' in node:
                    assert set(node['required']) == set(node['properties'])
                    assert node['additionalProperties'] is False
                for value in node.values(): check_required(value)
            elif isinstance(node, list):
                for value in node: check_required(value)
        check_required(payload['response_format']['json_schema']['schema'])
        compact = draft.model_dump(mode='json')
        evidence=json.loads(payload['messages'][-1 if not first_code or len(requests)==1 else -2]['content'])['saved_evidence']
        alias=next(f['fact_id'] for result in evidence for f in result['facts'] if f['field_name']=='water_km2')
        assert alias.startswith('F') and len(alias)<10
        for section in compact['sections']:
            section['claims'] = [{key: claim[key] for key in ('fact_id', 'operation')} for claim in section['claims']]
            identifier=section['claims'][0]['fact_id']
            for claim in section['claims']:claim['fact_id']=alias
            for sentence in section['narrative']:
                sentence['text']=sentence['text'].replace(identifier,alias)
                sentence['fact_ids']=[alias]
        return httpx.Response(200, json={'id': 'chat_mock', 'object': 'chat.completion', 'created': 0,
            'model': 'openai/gpt-oss-120b', 'usage': {'prompt_tokens': 300, 'completion_tokens': 100, 'total_tokens': 400},
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(compact)}}]})
    client = Groq(api_key='unit-test-placeholder', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('app.llm.Groq', lambda **kwargs: client)
    reply = answer(evidence_run, 'How much new water was detected?', language)
    assert len(requests) == (2 if first_code else 1) and reply['generation']['mode'] == 'llm'
    assert reply['generation']['provider_requests'] == len(requests)
    assert 'private diagnostic' not in reply['answer']
    prefix = 'Here is the recorded finding' if language == 'en' else 'अभिलेखित नतिजा'
    assert prefix in reply['answer'] and '{fact:' not in reply['answer']
    assert all(fact['fact_id'].startswith(context.run_id+':') for fact in reply['facts'])
    report = build_report_data(evidence_run, language, reply)
    assert any(prefix in line for section in report.sections for line in section.lines)
    assert len(PdfReader(BytesIO(report_pdf(evidence_run, language, report))).pages) == 1


def test_unknown_transport_reference_never_becomes_evidence(evidence_run):
    context=EvidenceContext(evidence_run)
    draft=narrative_draft(context).model_dump(mode='json')
    draft['sections'][0]['claims']=[{'fact_id':'F999','operation':'exact'}]
    context.provider_fact_aliases={}
    with pytest.raises(GroundingError,match='not returned'):
        _parse_draft(json.dumps(draft),context)


def test_saved_report_export_rejects_changed_source_facts(evidence_run,tmp_path):
    from scripts.export_copilot_reports import verified_snapshot
    context=EvidenceContext(evidence_run)
    report=build_report_data(evidence_run)
    run_folder=tmp_path/context.run_id
    run_folder.mkdir()
    snapshots=tmp_path/'.reports'/context.run_id
    snapshots.mkdir(parents=True)
    target=snapshots/f'{report.report_id}.json'
    target.write_text(report.model_dump_json())
    verification={'checks_complete':True,'run_id':context.run_id,'reports':[{'language':'en','links':{
        'json':f'/api/analyses/{context.run_id}/reports/{report.report_id}?format=json'}}]}
    exported,source=verified_snapshot(verification,run_folder,context,'en')
    assert exported.report_id==report.report_id and source['sha256']
    altered=report.model_dump(mode='json')
    next(f for f in altered['facts'] if f['field_name']=='water_km2')['value']=999
    target.write_text(json.dumps(altered))
    with pytest.raises(ValueError,match='facts differ'):
        verified_snapshot(verification,run_folder,context,'en')


def test_provider_retries_only_a_short_advertised_rate_limit(monkeypatch):
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={'retry-after': '0'}, json={'error': {'message': 'private'}})
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 0,
            'model': 'openai/gpt-oss-120b', 'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{}'}}]})
    client = Groq(api_key='unit-test-placeholder', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('app.llm.Groq', lambda **kwargs: client)
    provider = GroqProvider(LLMConfig(api_key=SecretStr('unit-test-placeholder')))
    try:
        provider.create(instructions='test', items=[], tools=[], schema={}, tool_choice='none')
        assert len(requests) == 2 and provider.usage['rate_limit_retries'] == 1
    finally: provider.close()


def test_long_rate_limit_is_reported_without_blocking(monkeypatch):
    def handle(request):
        return httpx.Response(429, headers={'retry-after': '90'}, json={'error': {'message': 'private'}})
    client = Groq(api_key='unit-test-placeholder', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr('app.llm.Groq', lambda **kwargs: client)
    monkeypatch.setattr('app.llm.time.sleep', lambda delay: pytest.fail('Long delays must not block the request'))
    provider = GroqProvider(LLMConfig(api_key=SecretStr('unit-test-placeholder')))
    try:
        with pytest.raises(ProviderFailure) as error:
            provider.create(instructions='test', items=[], tools=[], schema={}, tool_choice='none')
        assert error.value.reason == 'rate_limited' and error.value.retry_after_seconds == 90
        assert provider.usage['provider_requests'] == 1
    finally: provider.close()


@pytest.mark.parametrize('code,reason', [('json_validate_failed', 'structured_output_failed'), ('invalid_request_error', 'request_rejected')])
def test_provider_request_errors_keep_a_sanitized_specific_reason(monkeypatch, code, reason):
    client = Groq(api_key='unit-test-placeholder', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={'error': {'code': code, 'message': 'private diagnostic'}}))))
    monkeypatch.setattr('app.llm.Groq', lambda **kwargs: client)
    provider = GroqProvider(LLMConfig(api_key=SecretStr('unit-test-placeholder')))
    try:
        with pytest.raises(ProviderFailure) as error:
            provider.create(instructions='test', items=[], tools=[], schema={}, tool_choice='none')
        assert error.value.reason == reason and error.value.http_status == 400
        assert 'private' not in str(error.value)
    finally: provider.close()


def test_alias_cache_matches_complete_names_and_invalidates_changed_names(evidence_run):
    feature = evidence_run['layers']['roads']['features'][0]
    feature['properties']['name'] = 'River Road'
    subject = ('roads', feature['properties']['id'])
    assert mentions(EvidenceContext(evidence_run), 'Is River Road exposed?') == [subject]
    assert not mentions(EvidenceContext(evidence_run), 'River Roadway is different.')
    feature['properties']['name'] = 'Changed Road'
    context = EvidenceContext(evidence_run)
    assert not mentions(context, 'Is River Road exposed?')
    assert mentions(context, 'Is Changed Road exposed?') == [subject]
