import copy
import uuid
import pytest
from pydantic import ValidationError

from app.copilot_types import AnswerDraft, AnswerSection
from app.evidence import EvidenceContext, EvidenceError, tool_definitions
from app.grounding import GroundingError, claim_for, validate_draft, render_draft


def draft_for(context, fact, language='en', topic='summary', **updates):
    claim=claim_for(fact).model_copy(update=updates)
    return AnswerDraft(run_id=context.run_id,language=language,kind='answer',
        sections=[AnswerSection(topic=topic,claims=[claim],explanations=[])])


def test_six_tools_are_read_only_run_bound_and_typed(evidence_run):
    before=copy.deepcopy(evidence_run)
    context=EvidenceContext(evidence_run,evidence_run['id'])
    feature=next(f for f in evidence_run['layers']['settlements']['features'])['properties']['id']
    requests=[('event_summary',{}),('flood_surface_change',{}),
        ('infrastructure_impacts',{'asset_type':'roads','only_exposed':True,'offset':0,'limit':2}),
        ('settlement_connectivity',{'status':'all','offset':0,'limit':2}),
        ('sources_limitations',{}),('feature_evidence',{'layer':'settlements','feature_id':feature})]
    for name,args in requests:
        result=context.call(name,args)
        assert result.run_id==context.run_id
        assert all(f.run_id==context.run_id and f.fact_id.startswith(context.run_id+':') for f in result.facts)
    assert evidence_run==before
    assert len(tool_definitions())==6
    with pytest.raises(ValidationError):
        context.call('event_summary',{'run_id':str(uuid.uuid4())})
    with pytest.raises(EvidenceError):
        context.call('exec_python',{'code':'print(1)'})


def test_mismatched_run_and_duplicate_features_rejected(evidence_run):
    with pytest.raises(EvidenceError):
        EvidenceContext(evidence_run,str(uuid.uuid4()))
    evidence_run['layers']['roads']['features'].append(evidence_run['layers']['roads']['features'][0])
    with pytest.raises(EvidenceError,match='Duplicate'):
        EvidenceContext(evidence_run)


def test_unknown_feature_and_path_are_not_filesystem_access(evidence_run):
    c=EvidenceContext(evidence_run)
    for value in ('not-a-feature','/etc/passwd','../../another-run/result.json'):
        r=c.call('feature_evidence',{'layer':'roads','feature_id':value})
        assert r.status=='not_found' and not r.facts


def test_metrics_units_methods_and_actual_acquisition_dates_preserved(evidence_run):
    c=EvidenceContext(evidence_run)
    facts=c.call('event_summary',{}).facts
    water=next(f for f in facts if f.field_name=='water_km2')
    assert water.value==evidence_run['stats']['water_km2']
    assert water.unit=='km²' and water.method and water.source_ids
    dates=[f.value for f in facts if f.field_name in ('radar_before_acquired','radar_after_acquired')]
    assert dates==[s['acquired'] for s in evidence_run['provenance']['radar_scenes']]


@pytest.mark.parametrize('missing', [None,'absent'])
def test_missing_values_never_become_zero_in_either_language(evidence_run,missing):
    if missing is None:evidence_run['stats']['water_km2']=None
    else:evidence_run['stats'].pop('water_km2')
    c=EvidenceContext(evidence_run)
    c.call('flood_surface_change',{})
    fact=c.global_fields['water_km2']
    assert fact.value is None and fact.status=='missing' and fact.interpretation=='unavailable'
    for language,word in [('en','Unavailable'),('ne','उपलब्ध छैन')]:
        d=draft_for(c,fact,language,'flood')
        validate_draft(d,c,language)
        text,_=render_draft(d,c)
        assert word in text and '0.000' not in text


def test_valid_claim_and_allowlisted_rounding_are_resolved_by_python(evidence_run):
    c=EvidenceContext(evidence_run);c.call('flood_surface_change',{})
    fact=c.global_fields['water_km2']
    d=draft_for(c,fact,topic='flood',operation='round_1')
    validate_draft(d,c,'en')
    text,facts=render_draft(d,c)
    from decimal import Decimal,ROUND_HALF_UP
    expected=str(Decimal(str(fact.value)).quantize(Decimal('.1'),rounding=ROUND_HALF_UP))
    assert expected+' km²' in text
    assert facts[0].value==fact.value


@pytest.mark.parametrize('update', [
    {'fact_id':'invented-date-fact'}, {'run_id':'different-analysis'},
    {'subject_id':'different-location'}, {'field_name':'population_affected'},
    {'subject_type':'settlements'}, {'interpretation':'potential_exposure'},
])
def test_wrong_fact_run_location_field_or_interpretation_rejected(evidence_run,update):
    c=EvidenceContext(evidence_run);c.call('flood_surface_change',{})
    with pytest.raises(GroundingError):
        validate_draft(draft_for(c,c.global_fields['water_km2'],**update),c,'en')


def test_facts_not_retrieved_and_wrong_language_rejected(evidence_run):
    c=EvidenceContext(evidence_run)
    d=draft_for(c,c.global_fields['water_km2'])
    with pytest.raises(GroundingError,match='approved tool'):validate_draft(d,c,'en')
    c.call('event_summary',{})
    with pytest.raises(GroundingError,match='language'):validate_draft(d,c,'ne')


@pytest.mark.parametrize('field,value', [('value',99999),('value','2027-01-01'),('interpretation','confirmed_destroyed'),('interpretation','verified_road_closure')])
def test_unsupported_values_dates_damage_and_closure_prose_are_outside_schema(evidence_run,field,value):
    c=EvidenceContext(evidence_run);c.call('event_summary',{})
    raw=draft_for(c,c.global_fields['water_km2']).model_dump()
    raw['sections'][0]['claims'][0][field]=value
    with pytest.raises(ValidationError):AnswerDraft.model_validate(raw)
    raw=draft_for(c,c.global_fields['water_km2']).model_dump()
    raw['sections'][0]['text']='The village is destroyed and roads are physically inaccessible.'
    with pytest.raises(ValidationError):AnswerDraft.model_validate(raw)


def test_counts_and_dates_cannot_be_rounded(evidence_run):
    c=EvidenceContext(evidence_run);c.call('event_summary',{})
    for key in ('buildings_exposed','radar_before_acquired'):
        with pytest.raises(GroundingError,match='Rounding'):
            validate_draft(draft_for(c,c.global_fields[key],operation='round_1'),c,'en')


def test_english_nepali_use_same_facts_names_and_units(evidence_run):
    c=EvidenceContext(evidence_run)
    identifier=evidence_run['layers']['settlements']['features'][0]['properties']['id']
    fact=c.call('feature_evidence',{'layer':'settlements','feature_id':identifier}).facts[-3]
    results=[]
    for language in ('en','ne'):
        d=draft_for(c,fact,language,'feature');validate_draft(d,c,language)
        text,facts=render_draft(d,c)
        assert identifier in text and fact.subject_name in text
        results.append([f.model_dump() for f in facts])
    assert results[0]==results[1]
