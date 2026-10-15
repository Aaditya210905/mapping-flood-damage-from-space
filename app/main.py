from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import threading
import uuid
import csv
import io
from typing import Literal
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
import rasterio

from app.copilot import COPILOT_INFO, answer
from app.geo import Grid, trace_downhill
from app.models import AnalysisRequest, CopilotRequest, ReportRequest, TraceRequest
from app.attribution import run_attributions
from app.pipeline import run_analysis
from app.report import report_html, report_pdf, build_report_data, ReportDataError, ReportRenderingError
from app.copilot_types import SituationReport
from app.evidence import EvidenceContext, EvidenceError, TOOL_REGISTRY
from app.llm import provider_status
from app.sources import MAPPING_METHOD, SOURCES
from app.run_store import load_result, run_metadata
from app.storage import save_json
from app.availability import normalize_analysis_availability
from app.presets import PRESETS
from app.settings import MAPPING, ACQUISITION, EVALUATION, satellite_provider


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
    yield
    executor.shutdown(wait=False, cancel_futures=True)
    executor = None


app = FastAPI(title="Flood Lens", version="0.1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


def run_folder(run_id):
    try:
        uuid.UUID(run_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "Unknown run")
    return DATA / run_id


def update_job(run_id, **changes):
    """Persist progress atomically so a restart cannot erase the failure reason."""
    with lock:
        jobs[run_id].update(changes, updated_at=datetime.now(timezone.utc).isoformat())
        save_json(run_folder(run_id) / "job.json", jobs[run_id])


def stage_for_progress(percent):
    return next((stage for cutoff, stage in ((12, "fetch"), (72, "preprocess"), (86, "flood_map"),
                                             (95, "damage_and_access"), (100, "facts_and_exports"))
                 if percent < cutoff), "complete")


def saved_run(run_id):
    path = run_folder(run_id) / "result.json"
    if not path.exists():
        raise HTTPException(404, "Analysis result is not available")
    try:
        run = load_result(path, lambda data: EvidenceContext(data, run_id))
        if str(run.get("id")) != str(uuid.UUID(run_id)):
            raise ValueError("Analysis ownership mismatch")
        run = normalize_analysis_availability(run)
    except (OSError, ValueError, TypeError):
        raise HTTPException(422, "Saved analysis data are invalid or do not belong to the requested run.") from None
    run = {**run, "processing_status": "stored_result"}
    run['attributions'] = run_attributions(run)
    evaluation = path.with_name("evaluation.json")
    if evaluation.exists():
        try:
            comparison = json.loads(evaluation.read_text())
            if not isinstance(comparison, dict) or comparison.get("run_id") != run["id"]:
                raise ValueError("Evaluation ownership mismatch")
            run["evaluation"] = comparison
            run["attributions"] += ([comparison["attribution"]] if isinstance(comparison.get("attribution"), str) else [])
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


@app.get("/health", include_in_schema=False)
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
            record = run_metadata(path)
            mode = record.get("mode") if isinstance(record, dict) else None
            if isinstance(mode, str) and mode in completed:
                completed[mode] += 1
        except (OSError, ValueError):
            continue
    return {"sources": SOURCES, "mapping": MAPPING_METHOD, "copilot": {**COPILOT_INFO, "llm": provider_status()},
            "presets": PRESETS, "mapping_configuration": MAPPING.model_dump(),
            "acquisition_configuration":ACQUISITION.model_dump(),
            "evaluation_configuration":EVALUATION.model_dump(),
            "parameter_schema": AnalysisRequest.model_json_schema(),
            "workspace": {"completed_actual_runs": completed["live"], "completed_synthetic_runs": completed["demo"],
                          "preferred_satellite_provider": "copernicus" if os.getenv("CDSE_CLIENT_ID") and os.getenv("CDSE_CLIENT_SECRET") else "public",
                          "training_data_loading": False, "default_detection": "rule_based"}}


@app.get("/api/copilot")
def copilot_info():
    return {**COPILOT_INFO, "llm": provider_status(), "tools": list(TOOL_REGISTRY)}


def dashboard_view(run):
    """Send the displayed building subset; retain complete audit/layer exports."""
    buildings = run["layers"]["buildings"]
    settlements = run["layers"]["settlements"]
    # Long route geometries are fetched only when a settlement is inspected.
    # They otherwise dominate dashboard payloads even when no route is open.
    compact = [{**feature, "properties": {key: value for key, value in feature["properties"].items()
                if key not in ("baseline_route", "after_route", "connectivity_by_destination_type")}}
               for feature in settlements["features"]]
    return {**run, "layers": {**run["layers"], "buildings": {**buildings, "features": [
        f for f in buildings["features"] if f["properties"].get("exposed")]},
        "settlements": {**settlements, "features": compact}},
        "route_detail_scope": "Full route and destination-type evidence is available from the feature-detail endpoint.",
        "building_layer_scope": "Potentially exposed footprints; complete building layer remains in buildings.geojson and result.json."}


@app.get("/api/analyses/latest-real")
def latest_real(view: Literal["full", "dashboard"] = "full"):
    available = []
    for path in DATA.glob("*/result.json"):
        try:
            run = run_metadata(path)
            if run["mode"] == "live":
                available.append(run)
        except (ValueError, KeyError, OSError):
            continue
    if not available:
        raise HTTPException(404, "No actual-data analysis has completed yet.")
    run = saved_run(max(available, key=lambda run: run.get("created_at") or "")["id"])
    return dashboard_view(run) if view == "dashboard" else run


@app.post("/api/analyses", status_code=202)
def create_analysis(params: AnalysisRequest):
    # Validate expensive grid allocation before queueing a job.
    try:
        Grid.for_bbox(params.bbox, params.resolution_m)
        effective_provider=satellite_provider(params.satellite_provider) if params.mode=='live' else params.satellite_provider
    except ValueError as error:
        raise HTTPException(422, str(error))
    if params.mode == "live" and effective_provider == "copernicus" and not (os.getenv("CDSE_CLIENT_ID") and os.getenv("CDSE_CLIENT_SECRET")):
        raise HTTPException(422, "Live mode needs CDSE_CLIENT_ID and CDSE_CLIENT_SECRET. Configure the Copernicus Sentinel Hub OAuth client, then restart the server.")
    if not capacity.acquire(blocking=False):
        raise HTTPException(429, "Two analyses are already running. Please wait for one to finish.")
    run_id = str(uuid.uuid4())
    with lock:
        jobs[run_id] = {"id": run_id, "status": "running", "progress": 0, "message": "Preparing inputs"}
    try:
        save_json(run_folder(run_id) / "request.json", params.model_dump(mode="json"))
        update_job(run_id, stage="fetch", started_at=datetime.now(timezone.utc).isoformat())
    except OSError:
        capacity.release()
        with lock:
            jobs.pop(run_id, None)
        raise HTTPException(503, "Analysis storage is unavailable. Check available disk space and retry.") from None
    def work():
        def progress(message, percent):
            update_job(run_id, message=message, progress=percent, stage=stage_for_progress(percent))
        try:
            completed = run_analysis(params, run_id, run_folder(run_id), progress)
            update_job(run_id, status="complete", progress=100, stage="complete",
                       analysis_status=completed.get("analysis_status", "partial"),
                       message="Analysis saved. Check observation coverage and unavailable classes.")
        except Exception as error:
            logger.error("Analysis %s failed (%s)", run_id, type(error).__name__)
            safe_message = str(error) if isinstance(error, ValueError) else "The upstream data service or analysis failed. Check network access and server logs, then retry."
            try:
                update_job(run_id, status="failed", message=safe_message)
            except OSError:
                with lock:
                    jobs[run_id].update(status="failed", message="Analysis failed and progress could not be saved. Check disk space and retry.")
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
        return {"id": run_id, "status": "complete", "progress": 100, "message": "Saved analysis; inspect coverage before interpreting its numbers."}
    job_path = run_folder(run_id) / "job.json"
    if job_path.exists():
        try:
            previous = json.loads(job_path.read_text())
            if previous.get("id") != run_id:
                raise ValueError("Job ownership mismatch")
            if previous.get("status") == "running":
                previous = {**previous, "status": "interrupted", "message": "The server restarted before this analysis completed. Submit the analysis again; no result is available."}
            return previous
        except (ValueError, OSError, AttributeError):
            raise HTTPException(422, "Saved progress is invalid. Submit a new analysis.") from None
    raise HTTPException(404, "Unknown analysis")


@app.get("/api/analyses/{run_id}")
def result(run_id: str, view: Literal["full", "dashboard"] = "full"):
    run = saved_run(run_id)
    return dashboard_view(run) if view == "dashboard" else run


@app.get("/api/analyses/{run_id}/features/{layer}")
def feature_detail(run_id: str, layer: Literal["buildings", "roads", "bridges", "settlements", "destinations"], feature_id: str):
    context = EvidenceContext(saved_run(run_id), run_id)
    feature = context.index.get((layer, feature_id))
    if feature is None:
        raise HTTPException(404, "This feature is not present in the selected analysis.")
    evidence = context.call("feature_evidence", {"layer": layer, "feature_id": feature_id})
    return {"run_id": context.run_id, "layer": layer, "feature": feature, "evidence": evidence}


@app.get("/api/analyses/{run_id}/exports/settlements.csv")
def settlement_csv(run_id: str):
    run = saved_run(run_id)
    output = io.StringIO(newline="")
    fields = ["id", "name", "status", "nearest_destination", "before_distance_km", "after_distance_km",
              "same_destination_extra_distance_km", "snap_distance_m", "assessment_fraction"]
    writer = csv.writer(output)
    writer.writerow(fields + ["run_id", "event_date", "osm_snapshot", "attribution", "database_license"])
    def cell(value):
        # OSM names are untrusted text, including when opened in a spreadsheet.
        return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
    for feature in run["layers"]["settlements"]["features"]:
        props = feature["properties"]
        writer.writerow([cell(props.get(key)) for key in fields] + [run["id"], run["flood_date"],
                        run["provenance"].get("osm_snapshot"), "© OpenStreetMap contributors", "ODbL-1.0; https://www.openstreetmap.org/copyright"])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="settlement-access-{run_id}.csv"'})


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
