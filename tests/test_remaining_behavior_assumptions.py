"""Regressions for behavior that remained fixed after configuration repairs."""
import json
from types import SimpleNamespace
import pytest

from app.copilot import answer
from app.copilot_types import AnswerDraft, AnswerSection
from app.evidence import EvidenceContext
from app.grounding import claim_for


@pytest.mark.parametrize('planning', ['semantic', 'local'])
def test_unrecognized_question_cannot_release_unrelated_but_true_water_evidence(evidence_run, monkeypatch, planning):
    from app.llm import ToolCall
    context = EvidenceContext(evidence_run)
    context.call('flood_surface_change', {})
    wrong = AnswerDraft(run_id=context.run_id, language='en', kind='answer', sections=[
        AnswerSection(topic='flood', claims=[claim_for(context.global_fields['water_km2'])], explanations=[])])
    class WrongScopeProvider:
        def __init__(self, config):
            self.usage = {'provider_requests': 0}
        def create(self, **kwargs):
            self.usage['provider_requests'] += 1
            if kwargs['tools']:
                return SimpleNamespace(output=[ToolCall(type='function_call', name='flood_surface_change', arguments='{}', call_id='wrong')], output_text='')
            return SimpleNamespace(output=[], output_text=wrong.model_dump_json())
        def close(self):
            pass
    monkeypatch.setenv('LLM_ENABLED', 'true')
    monkeypatch.setenv('GROQ_API_KEY', 'unit-test-not-a-real-key')
    monkeypatch.setenv('LLM_QUERY_PLANNING', planning)
    monkeypatch.setattr('app.copilot_service.GroqProvider', WrongScopeProvider)
    reply = answer(evidence_run, 'Which hamlets retain a path to medical care?')
    assert not any(f['field_name'] == 'water_km2' for f in reply['facts'])


def test_source_catalog_does_not_force_an_ems_activation():
    from app.sources import SOURCES
    assert all('EMSR927' not in json.dumps(source) for source in SOURCES)


def test_reference_links_use_configured_host_and_explicit_activation(evidence_run, monkeypatch):
    from app import sources, main
    monkeypatch.setattr(sources, 'ACQUISITION', sources.ACQUISITION.model_copy(update={'ems_public_base_url': 'https://reference.example/activations'}))
    evidence_run['evaluation']['activation'] = 'EMSR123'
    assert main.dashboard_view(evidence_run)['evaluation_reference_url'] == 'https://reference.example/activations/EMSR123/'
    evidence_run['evaluation']['activation'] = None
    assert main.dashboard_view(evidence_run)['evaluation_reference_url'] is None


def test_configured_overlap_threshold_does_not_keep_half_cell_warning(tmp_path, monkeypatch):
    from app import pipeline
    from app.models import AnalysisRequest
    import uuid
    monkeypatch.setattr(pipeline, 'MAPPING', pipeline.MAPPING.model_copy(update={'road_overlap_cell_fraction': .75}))
    run = pipeline.run_analysis(AnalysisRequest(case_id='trishuli', mode='demo', resolution_m=100), str(uuid.uuid4()), tmp_path)
    assert run['provenance']['mapping_configuration']['road_overlap_cell_fraction'] == .75
    assert not any('half a raster cell' in text for text in run['limitations'])
