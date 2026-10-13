from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import threading
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import rasterio

from app.copilot import COPILOT_INFO, answer, intent_model
from app.geo import Grid, trace_downhill
from app.models import AnalysisRequest, CopilotRequest, ReportRequest, TraceRequest, ATTRIBUTIONS
from app.pipeline import run_analysis
from app.report import report_html, report_pdf, build_report_data, ReportDataError, ReportRenderingError
from app.copilot_types import SituationReport
from app.evidence import EvidenceContext, EvidenceError, TOOL_REGISTRY
from app.llm import provider_status
from app.sources import MAPPING_METHOD, SOURCES


ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("FLOOD_LENS_DATA", ROOT / "data" / "runs"))
DATA.mkdir(parents=True, exist_ok=True)
jobs, lock = {}, threading.Lock()
executor = None
capacity = threading.BoundedSemaphore(2)
logger = logging.getLogger("flood_lens")


@asynccontextmanager
async def lifespan(app):
    global executor
    executor = ThreadPoolExecutor(max_workers=2)
    intent_model()
    yield
    executor.shutdown(wait=False, cancel_futures=True)
    executor = None


app = FastAPI(title="Flood Lens", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


def run_folder(run_id):
    try:
        uuid.UUID(run_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "Unknown run")
    return DATA / run_id


def saved_run(run_id):
    path = run_folder(run_id) / "result.json"
    if not path.exists():
        raise HTTPException(404, "Analysis result is not available")
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
        EvidenceContext(run, run_id)
    except (OSError, ValueError, TypeError):
        raise HTTPException(422, "Saved analysis data are invalid or do not belong to the requested run.") from None
    run = {**run, "processing_status": "stored_result"}
    evaluation = path.with_name("evaluation.json")
    if evaluation.exists():
        try:
            comparison = json.loads(evaluation.read_text())
            if not isinstance(comparison, dict) or comparison.get("run_id") != run["id"]:
                raise ValueError("Evaluation ownership mismatch")
            run["evaluation"] = comparison
            credits = run.get("attributions")
            run["attributions"] = (credits if isinstance(credits, list) else ATTRIBUTIONS) + ([comparison["attribution"]] if isinstance(comparison.get("attribution"), str) else [])
        except (ValueError, OSError):
            run["warnings"] = list(run.get("warnings") or []) + ["Optional reference evaluation is invalid or belongs to another run; it was not attached."]
    assets = path.with_name("asset-evaluation.json")
    if assets.exists():
        try:
            comparison = json.loads(assets.read_text())
            if not isinstance(comparison, dict) or comparison.get("run_id") != run["id"]:
                raise ValueError("Evaluation ownership mismatch")
            run["asset_evaluation"] = comparison
        except (ValueError, OSError):
            run["warnings"] = list(run.get("warnings") or []) + ["Optional asset evaluation is invalid or belongs to another run; it was not attached."]
    return run


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"status": "ok", "live_configured": bool(os.getenv("CDSE_CLIENT_ID") and os.getenv("CDSE_CLIENT_SECRET")),
            "public_raw_provider": True, "mapping": MAPPING_METHOD["type"], "ai_component": COPILOT_INFO["component"],
            "osm_v2_configured": bool(os.getenv("OHSOME_API_KEY")), "copilot": COPILOT_INFO["algorithm"], "llm": provider_status()}


@app.get("/api/data-sources")
def data_sources():
    completed = {"live": 0, "demo": 0}
    for path in DATA.glob("*/result.json"):
        try:
            record = json.loads(path.read_text())
            mode = record.get("mode") if isinstance(record, dict) else None
            if isinstance(mode, str) and mode in completed:
                completed[mode] += 1
        except (OSError, ValueError):
            continue
    return {"sources": SOURCES, "mapping": MAPPING_METHOD, "copilot": {**COPILOT_INFO, "llm": provider_status()},
            "workspace": {"completed_actual_runs": completed["live"], "completed_synthetic_runs": completed["demo"],
                          "training_data_loading": False, "default_detection": "rule_based"}}


@app.get("/api/copilot")
def copilot_info():
    return {**COPILOT_INFO, "llm": provider_status(), "tools": list(TOOL_REGISTRY)}


def dashboard_view(run):
    """Send the displayed building subset; retain complete audit/layer exports."""
    buildings = run["layers"]["buildings"]
    return {**run, "layers": {**run["layers"], "buildings": {**buildings, "features": [
        f for f in buildings["features"] if f["properties"].get("exposed")]}},
        "building_layer_scope": "Potentially exposed footprints; complete building layer remains in buildings.geojson and result.json."}


@app.get("/api/analyses/latest-real")
def latest_real(view: Literal["full", "dashboard"] = "full"):
    available = []
    for path in DATA.glob("*/result.json"):
        try:
            run = json.loads(path.read_text())
            if run["mode"] == "live":
                available.append(run)
        except (ValueError, KeyError, OSError):
            continue
    if not available:
        raise HTTPException(404, "No actual-data analysis has completed yet.")
    run = saved_run(max(available, key=lambda run: run["created_at"])["id"])
    return dashboard_view(run) if view == "dashboard" else run


@app.post("/api/analyses", status_code=202)
def create_analysis(params: AnalysisRequest):
    # Validate expensive grid allocation before queueing a job.
    try:
        Grid.for_bbox(params.bbox, params.resolution_m)
    except ValueError as error:
        raise HTTPException(422, str(error))
    if params.mode == "live" and params.satellite_provider == "copernicus" and not (os.getenv("CDSE_CLIENT_ID") and os.getenv("CDSE_CLIENT_SECRET")):
        raise HTTPException(422, "Live mode needs CDSE_CLIENT_ID and CDSE_CLIENT_SECRET. Configure the Copernicus Sentinel Hub OAuth client, then restart the server.")
    if not capacity.acquire(blocking=False):
        raise HTTPException(429, "Two analyses are already running. Please wait for one to finish.")
    run_id = str(uuid.uuid4())
    with lock:
        jobs[run_id] = {"id": run_id, "status": "running", "progress": 0, "message": "Preparing inputs"}
    def work():
        def progress(message, percent):
            with lock:
                jobs[run_id].update(message=message, progress=percent)
        try:
            run_analysis(params, run_id, run_folder(run_id), progress)
            with lock:
                jobs[run_id].update(status="complete", progress=100)
        except Exception as error:
            logger.error("Analysis %s failed (%s)", run_id, type(error).__name__)
            safe_message = str(error) if isinstance(error, ValueError) else "The upstream data service or analysis failed. Check network access and server logs, then retry."
            with lock:
                jobs[run_id].update(status="failed", message=safe_message)
        finally:
            capacity.release()
    try:
        if executor is None:
            raise RuntimeError("Analysis executor is not running")
        executor.submit(work)
    except RuntimeError:
        capacity.release()
        with lock:
            jobs.pop(run_id, None)
        raise HTTPException(503, "Analysis service is restarting; try again shortly.")
    return {"id": run_id, "status": "running"}


@app.get("/api/analyses/{run_id}/status")
def status(run_id: str):
    run_folder(run_id)
    with lock:
        if run_id in jobs:
            return dict(jobs[run_id])
    if (run_folder(run_id) / "result.json").exists():
        return {"id": run_id, "status": "complete", "progress": 100, "message": "Saved analysis"}
    raise HTTPException(404, "Unknown analysis")


@app.get("/api/analyses/{run_id}")
def result(run_id: str, view: Literal["full", "dashboard"] = "full"):
    run = saved_run(run_id)
    return dashboard_view(run) if view == "dashboard" else run


@app.post("/api/analyses/{run_id}/copilot")
def ask_copilot(run_id: str, request: CopilotRequest):
    run = saved_run(run_id)
    try:
        response = answer(run, request.question, request.language,
                          [turn.model_dump() for turn in request.history])
    except (EvidenceError, ValueError):
        raise HTTPException(422, "The question or conversation history does not match valid evidence for this analysis.") from None
    if response.get("intent") == "summary":
        try:
            data = build_report_data(run, request.language, response)
            response["report_links"] = persist_report(data)
        except (ReportDataError, OSError):
            response["report_links"] = None
            response["report_error"] = "Report unavailable: verify the analysis area, event date and report storage."
    return response


@app.get("/api/analyses/{run_id}/feature-evidence")
def feature_evidence(run_id: str, layer: Literal["buildings", "roads", "bridges", "settlements", "destinations"], feature_id: str):
    try:
        return EvidenceContext(saved_run(run_id), run_id).call("feature_evidence", {"layer": layer, "feature_id": feature_id})
    except (ValueError, TypeError):
        raise HTTPException(422, "Use a valid feature identifier and layer from this analysis.") from None


def persist_report(data):
    # Only server-generated report UUIDs choose filenames. These derived reports
    # are separate from immutable result.json and never become analysis inputs.
    folder = DATA / ".reports" / data.run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{data.report_id}.json").write_text(data.model_dump_json(), encoding="utf-8")
    base = f"/api/analyses/{data.run_id}/reports/{data.report_id}"
    return {"html": base + "?format=html", "pdf": base + "?format=pdf", "json": base + "?format=json"}


@app.post("/api/analyses/{run_id}/report")
def generate_report(run_id: str, request: ReportRequest):
    run = saved_run(run_id)
    try:
        response = answer(run, "Generate a short situation report", request.language) if request.use_llm else None
        data = build_report_data(run, request.language, response)
        return {"run_id": data.run_id, "language": data.language, "generation": data.generation,
                "status": data.status, "report": data, "links": persist_report(data)}
    except (ValueError, OSError):
        raise HTTPException(422, "Report could not be created: verify the analysis area, event date and saved data.") from None


@app.get("/api/analyses/{run_id}/reports/{report_id}")
def generated_report(run_id: str, report_id: str, format: Literal["html", "pdf", "json"] = "html"):
    run = saved_run(run_id)
    try:
        identifier = str(uuid.UUID(report_id))
        path = DATA / ".reports" / str(uuid.UUID(run_id)) / f"{identifier}.json"
        if not path.exists():
            raise HTTPException(404, "Generated report is not available.")
        data = SituationReport.model_validate_json(path.read_text())
        if data.run_id != str(uuid.UUID(run_id)) or data.report_id != identifier:
            raise ValueError("Mismatched report")
    except (ValueError, OSError):
        raise HTTPException(422, "Invalid report identifier or data.") from None
    if format == "json": return data
    try:
        if format == "pdf": return Response(report_pdf(run, data.language, data), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="flood-lens-{data.language}-{identifier}.pdf"'})
        return HTMLResponse(report_html(run, data.language, data))
    except ReportRenderingError as error:
        raise HTTPException(422, str(error)) from None


@app.get("/api/analyses/{run_id}/report")
def report(run_id: str, language: str = "en", format: str = "html"):
    if language not in ("en", "ne") or format not in ("html", "pdf"):
        raise HTTPException(422, "Use language=en|ne and format=html|pdf")
    run = saved_run(run_id)
    try:
        if format == "pdf":
            return Response(report_pdf(run, language), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="flood-lens-situation-report-{language}.pdf"'})
        return HTMLResponse(report_html(run, language))
    except (ReportDataError, ReportRenderingError) as error:
        raise HTTPException(422, str(error)) from None


@app.get("/api/analyses/{run_id}/files/{filename}")
def file(run_id: str, filename: str):
    allowed = {"before.png", "after.png", "change-mask.tif", "dem-grid.tif", "water-probability.tif", "evaluation.json", "asset-evaluation.json", "eligibility.tif", "terrain-before.tif", "terrain-after.tif", "s1-before.tif", "s1-after.tif", "s1-vh-before.tif", "s1-vh-after.tif", "s2-before.tif", "s2-after.tif", "result.json", "osm-snapshot.geojson", "extent.geojson", "buildings.geojson", "roads.geojson", "bridges.geojson", "settlements.geojson", "destinations.geojson"}
    if filename not in allowed:
        raise HTTPException(404, "Unknown export")
    saved_run(run_id)
    path = run_folder(run_id) / filename
    if not path.exists():
        raise HTTPException(404, "Export unavailable")
    return FileResponse(path, filename=filename if not filename.endswith(".png") else None)


@app.post("/api/analyses/{run_id}/trace")
def trace(run_id: str, request: TraceRequest):
    run = saved_run(run_id)
    with rasterio.open(run_folder(run_id) / "dem-grid.tif") as src:
        grid = Grid(str(src.crs), src.transform, src.width, src.height, abs(src.transform.a))
        dem = src.read(1)
    try:
        return trace_downhill(dem, grid, request.longitude, request.latitude, run["layers"]["settlements"]["features"])
    except ValueError as error:
        raise HTTPException(422, str(error))
