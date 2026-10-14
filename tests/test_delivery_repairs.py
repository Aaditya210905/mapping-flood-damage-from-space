import csv
import io
import json
import uuid

from fastapi.testclient import TestClient

from app import main
from app.run_store import clear_cache, load_result, run_metadata


def test_result_cache_invalidates_changed_sources_and_validates_metadata_reads(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"id": "first", "mode": "live"}))
    clear_cache()
    assert run_metadata(path)["mode"] == "live"
    checks = []
    assert load_result(path, lambda data: checks.append(data["id"]))["id"] == "first"
    load_result(path, lambda data: checks.append(data["id"]))
    assert checks == ["first"]
    path.write_text(json.dumps({"id": "second-longer", "mode": "demo"}))
    assert run_metadata(path)["mode"] == "demo"
    assert load_result(path)["id"] == "second-longer"


def test_restart_preserves_failed_and_interrupted_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    identifiers = [str(uuid.uuid4()), str(uuid.uuid4())]
    for identifier, status in zip(identifiers, ("running", "failed")):
        folder = tmp_path / identifier
        folder.mkdir()
        (folder / "job.json").write_text(json.dumps({"id": identifier, "status": status, "progress": 23, "message": "Recorded upstream failure"}))
    with TestClient(main.app) as client:
        restarted = client.get(f"/api/analyses/{identifiers[0]}/status").json()
        assert restarted["status"] == "interrupted" and "restarted" in restarted["message"]
        failed = client.get(f"/api/analyses/{identifiers[1]}/status").json()
        assert failed["status"] == "failed" and failed["message"] == "Recorded upstream failure"


def test_dashboard_and_feature_detail_preserve_full_geometry_and_safe_csv(evidence_run, tmp_path, monkeypatch):
    run = evidence_run
    identifier = run["layers"]["settlements"]["features"][0]["properties"]["id"]
    props = run["layers"]["settlements"]["features"][0]["properties"]
    props["name"] = "=HYPERLINK(unsafe)"
    props["baseline_route"] = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[85.2, 28], [85.3, 28.1]]}, "properties": {}}
    before = json.dumps(run, sort_keys=True)
    folder = tmp_path / run["id"]
    folder.mkdir()
    (folder / "result.json").write_text(json.dumps(run))
    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client:
        base = f"/api/analyses/{run['id']}"
        compact = client.get(base + "?view=dashboard").json()
        assert "baseline_route" not in compact["layers"]["settlements"]["features"][0]["properties"]
        detail = client.get(base + "/features/settlements", params={"feature_id": identifier})
        assert detail.status_code == 200
        assert detail.json()["feature"]["properties"]["baseline_route"] == props["baseline_route"]
        assert client.get(base + "/features/settlements", params={"feature_id": "not-in-this-run"}).status_code == 404
        exported = client.get(base + "/exports/settlements.csv")
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert rows[0]["name"].startswith("'=")
        assert rows[0]["run_id"] == run["id"] and "ODbL" in rows[0]["database_license"]
    assert json.dumps(run, sort_keys=True) == before


def test_read_time_normalization_does_not_rewrite_real_evidence(evidence_run, tmp_path, monkeypatch):
    run = evidence_run
    run["mode"] = "live"  # Controlled software fixture, never an actual-data claim.
    run["stats"].update(debris_eligible_coverage_pct=0, optical_clear_pct=0, debris_km2=0)
    run.pop("class_availability", None)
    folder = tmp_path / run["id"]
    folder.mkdir()
    source = folder / "result.json"
    source.write_text(json.dumps(run))
    original = source.read_bytes()
    monkeypatch.setattr(main, "DATA", tmp_path)
    loaded = main.saved_run(run["id"])
    assert loaded["stats"]["debris_km2"] is None
    assert loaded["analysis_status"] == "partial"
    assert source.read_bytes() == original
