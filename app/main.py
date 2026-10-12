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

from app.copilot import answer, intent_model
from app.geo import Grid, trace_downhill
from app.models import AnalysisRequest, CopilotRequest, TraceRequest
from app.pipeline import run_analysis
from app.report import report_html, report_pdf
from app.datasets import SOURCES, DATASETS
from app.segmentation import model_status


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
    except ValueError:
        raise HTTPException(404, "Unknown run")
    return DATA / run_id


def saved_run(run_id):
    path = run_folder(run_id) / "result.json"
    if not path.exists():
        raise HTTPException(404, "Analysis result is not available")
    run = json.loads(path.read_text(encoding="utf-8"))
    evaluation = path.with_name("evaluation.json")
    if evaluation.exists():
        comparison = json.loads(evaluation.read_text())
        if comparison.get("run_id") == run["id"]:
            run["evaluation"] = comparison
            run["attributions"] = run["attributions"] + [comparison["attribution"]]
    return run


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"status": "ok", "live_configured": bool(os.getenv("CDSE_CLIENT_ID") and os.getenv("CDSE_CLIENT_SECRET")),
            "public_raw_provider": True, "water_model": model_status()["status"],
            "osm_v2_configured": bool(os.getenv("OHSOME_API_KEY")), "copilot": "local bilingual intent model"}


@app.get("/api/data-sources")
def data_sources():
    sources = []
    for source in SOURCES:
        path = DATASETS / ("evaluation/EMSR927" if source["id"] == "emsr927" else f"training/{source['id']}") / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        sources.append({**source, "downloaded_records": len(manifest.get("records", manifest.get("products", []))),
                        "selection": manifest.get("selection"), "download_manifest": str(path.relative_to(ROOT)) if path.exists() else None})
    return {"sources": sources, "water_model": model_status()}


@app.get("/api/water-model")
def water_model_card():
    return model_status()


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
    return answer(saved_run(run_id), request.question, request.language)


@app.get("/api/analyses/{run_id}/report")
def report(run_id: str, language: str = "en", format: str = "html"):
    if language not in ("en", "ne") or format not in ("html", "pdf"):
        raise HTTPException(422, "Use language=en|ne and format=html|pdf")
    run = saved_run(run_id)
    if format == "pdf":
        if language == "ne":
            raise HTTPException(422, "Open the Nepali HTML report and choose Print / Save PDF to preserve Devanagari text.")
        return Response(report_pdf(run), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="flood-lens-situation-report.pdf"'})
    return HTMLResponse(report_html(run, language))


@app.get("/api/analyses/{run_id}/files/{filename}")
def file(run_id: str, filename: str):
    allowed = {"before.png", "after.png", "change-mask.tif", "dem-grid.tif", "water-probability.tif", "evaluation.json", "s1-before.tif", "s1-after.tif", "s2-before.tif", "s2-after.tif", "result.json", "osm-snapshot.geojson", "extent.geojson", "buildings.geojson", "roads.geojson", "bridges.geojson", "settlements.geojson", "destinations.geojson"}
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
