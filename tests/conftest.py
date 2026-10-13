import copy
import uuid
import pytest
from app.models import AnalysisRequest
from app.pipeline import run_analysis


@pytest.fixture(scope='session')
def generated_demo_analysis(tmp_path_factory):
    folder=tmp_path_factory.mktemp('copilot-synthetic-analysis')
    run=run_analysis(AnalysisRequest(mode='demo',resolution_m=100),str(uuid.uuid4()),folder)
    return run,folder


@pytest.fixture
def evidence_run(generated_demo_analysis):
    return copy.deepcopy(generated_demo_analysis[0])
