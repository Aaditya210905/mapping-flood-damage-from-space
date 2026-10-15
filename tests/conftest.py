import copy
import uuid
import pytest
from app.models import AnalysisRequest
from app.pipeline import run_analysis


@pytest.fixture(autouse=True)
def isolated_provider_state(monkeypatch):
    from app import copilot_cache,cdse_runtime
    copilot_cache.clear()
    with cdse_runtime._lock:cdse_runtime._tokens.clear()
    # Ordinary regression cases test fresh mocked provider behavior. Dedicated
    # reuse/cooldown cases explicitly enable these policies.
    monkeypatch.setenv('LLM_RESPONSE_CACHE_TTL_SECONDS','0')
    monkeypatch.setenv('LLM_PROVIDER_COOLDOWN_SECONDS','0')
    # The established deterministic/tool-selection suites explicitly exercise
    # that compatibility path. Semantic planning has its own native-SDK matrix.
    monkeypatch.setenv('LLM_QUERY_PLANNING', 'local')
    yield
    copilot_cache.clear()
    with cdse_runtime._lock:cdse_runtime._tokens.clear()


@pytest.fixture(scope='session')
def generated_demo_analysis(tmp_path_factory):
    folder=tmp_path_factory.mktemp('copilot-synthetic-analysis')
    run=run_analysis(AnalysisRequest(case_id="trishuli", mode='demo',resolution_m=100),str(uuid.uuid4()),folder)
    return run,folder


@pytest.fixture
def evidence_run(generated_demo_analysis):
    return copy.deepcopy(generated_demo_analysis[0])
