import sys
import types
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app import main, pipeline
from app.demo import make_demo
from app.geo import Grid, detect_changes
from app.models import AnalysisRequest
from app.segmentation import model_status, water_probabilities


@pytest.fixture
def adapter(monkeypatch):
    module = types.ModuleType("test_water_adapter")
    info = {"configured": True, "algorithm": "Test-only predictor", "threshold": .5,
            "requires_vh": False, "training_datasets": ["test fixture"], "citations": ["Test fixture citation"], "limitations": []}
    module.model_info = lambda: info
    module.predict_water = lambda vv, vh, valid: np.where(vv < -17, .9, .1)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("FLOOD_LENS_MODEL_ADAPTER", module.__name__)
    return module, info


def test_placeholder_rejected_before_acquisition_or_job_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOOD_LENS_MODEL_ADAPTER", "models.water_model")
    monkeypatch.setattr(main, "DATA", tmp_path)
    def unexpected_acquisition(*args, **kwargs):
        pytest.fail("An unconfigured model must fail before acquiring data")
    monkeypatch.setattr(pipeline, "acquire_public", unexpected_acquisition)
    assert model_status()["status"] == "not configured"
    with pytest.raises(ValueError, match="models/water_model.py"):
        pipeline.run_analysis(AnalysisRequest(water_model="external"), str(uuid.uuid4()), tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["water_model"] == "not configured"
        assert client.get("/api/data-sources").status_code == 200
        before_jobs = set(main.jobs)
        response = client.post("/api/analyses", json={"water_model": "external"})
        assert response.status_code == 422
        assert "models/water_model.py" in response.json()["detail"]
        assert set(main.jobs) == before_jobs


def test_adapter_water_change_and_metadata_use_observed_inputs(adapter):
    module, info = adapter
    calls = []
    def predict(vv, vh, valid):
        calls.append((vv.copy(), vh, valid.copy()))
        return np.where(vv < -17, .9, .1)
    module.predict_water = predict
    grid = Grid("EPSG:32645", from_origin(400000, 3100000, 30, 30), 11, 11, 30)
    before = np.stack([np.full((11, 11), .1), np.ones((11, 11))])
    after = np.stack([np.full((11, 11), .001), np.ones((11, 11))])
    result = detect_changes(before, after, grid, AnalysisRequest(water_model="external"))
    assert len(calls) == 2
    np.testing.assert_allclose(calls[0][0], -10, atol=1e-5)
    np.testing.assert_allclose(calls[1][0], -30, atol=1e-5)
    assert result["water"].all()
    assert result["water_model"]["citations"] == info["citations"]
    assert "training_pixels" not in result["water_model"]


def test_external_adapter_exports_probabilities_and_its_own_citations(adapter, monkeypatch, tmp_path):
    import json
    import rasterio
    _, info = adapter
    # Generated observations exercise the live adapter branch, not real accuracy.
    monkeypatch.setattr(pipeline, "acquire_public", lambda params, grid, folder, progress: make_demo(grid, params))
    run = pipeline.run_analysis(AnalysisRequest(water_model="external", resolution_m=100), str(uuid.uuid4()), tmp_path)
    saved = json.loads((tmp_path / "result.json").read_text())
    assert saved["provenance"]["water_model"]["algorithm"] == info["algorithm"]
    assert "Test fixture citation" in saved["attributions"]
    assert not any("Bountos" in citation for citation in saved["attributions"])
    with rasterio.open(tmp_path / "water-probability.tif") as source:
        assert source.count == 2
        assert source.crs is not None
        assert source.tags()["model"] == info["algorithm"]
    assert run["evaluation"]["status"] == "pending"


@pytest.mark.parametrize("output, message", [
    (np.zeros((1, 3, 3)), "shape"),
    (np.full((3, 3), 2.), "probabilities"),
    (np.full((3, 3), np.inf), "probabilities"),
    (np.full((3, 3), np.nan), "no valid predictions"),
])
def test_invalid_model_outputs_fail_explicitly(adapter, output, message):
    module, _ = adapter
    module.predict_water = lambda *args: output
    with pytest.raises(ValueError, match=message):
        water_probabilities(np.zeros((3, 3)), np.ones((3, 3), dtype=bool))


def test_nodata_never_becomes_a_prediction_and_inputs_are_not_mutated(adapter):
    module, _ = adapter
    vv = np.full((3, 3), -20., dtype="float32")
    valid = np.ones((3, 3), dtype=bool)
    valid[0, 0] = False
    def predict(values, cross, mask):
        values[:] = 42
        mask[:] = True
        output = np.full((3, 3), .8)
        output[0, 0] = 999  # Invalid-input outputs are ignored.
        output[1, 1] = np.nan
        return output
    module.predict_water = predict
    probabilities, usable, _ = water_probabilities(vv, valid)
    assert np.isnan(probabilities[0, 0]) and np.isnan(probabilities[1, 1])
    assert not usable[0, 0] and not usable[1, 1]
    assert (vv == -20).all() and not valid[0, 0]


def test_vh_requirements_and_array_alignment(adapter):
    _, info = adapter
    info["requires_vh"] = True
    vv = np.zeros((3, 3)); valid = np.ones((3, 3), dtype=bool)
    with pytest.raises(ValueError, match="requires VH"):
        water_probabilities(vv, valid)
    with pytest.raises(ValueError, match="shapes must match"):
        water_probabilities(vv, valid, vh_db=np.zeros((2, 3)))
    _, usable, _ = water_probabilities(vv, valid, vh_db=np.full((3, 3), np.nan))
    assert not usable.any()


@pytest.mark.parametrize("field,value", [("threshold", np.nan), ("threshold", 1.), ("citations", "not a list"), ("configured", "true")])
def test_invalid_adapter_metadata_does_not_advertise_ready(adapter, field, value):
    _, info = adapter
    info[field] = value
    status = model_status()
    assert status["status"] == "error" and status["configured"] is False


def test_missing_optional_framework_does_not_break_app_startup(monkeypatch):
    monkeypatch.setenv("FLOOD_LENS_MODEL_ADAPTER", "missing_water_framework_adapter")
    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/water-model").json()["status"] == "error"
