import json

from app import main


def test_empty_workspace_distinguishes_live_inputs_from_unused_training_references(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setenv("FLOOD_LENS_MODEL_ADAPTER", "models.water_model")
    status = main.data_sources()
    sources = {source["id"]: source for source in status["sources"]}
    assert {s["id"] for s in sources.values() if s["category"] == "input"} == {"sentinel1", "sentinel2", "osm", "copernicus_dem"}
    assert sources["kuro_siwo"]["category"] == sources["sen1floods11"]["category"] == "training_reference"
    assert sources["emsr927"]["category"] == "evaluation_only"
    assert status["water_model"]["status"] == "not configured"
    assert status["workspace"]["completed_actual_runs"] == 0
    assert status["workspace"]["training_data_loading"] is False


def test_workspace_status_counts_saved_modes_without_treating_demo_as_actual(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    for name, content in [("actual", {"mode": "live"}), ("demo", {"mode": "demo"}), ("unexpected", {"mode": "other"}), ("malformed", []), ("invalid-mode", {"mode": []})]:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "result.json").write_text(json.dumps(content))
    (tmp_path / "in-progress").mkdir()
    (tmp_path / "broken-json").mkdir()
    (tmp_path / "broken-json" / "result.json").write_text("{incomplete")
    status = main.data_sources()["workspace"]
    assert status["completed_actual_runs"] == 1
    assert status["completed_synthetic_runs"] == 1
