"""Replay saved CDSE acquisition inputs without using prediction or reference layers.

This intentionally supports the application's CDSE processed-TIFF cache format
only. Public original-GRD caches require a different calibration/RTC replay and
are refused rather than interpreted as CDSE arrays.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import uuid

import numpy as np
import rasterio

from app.geo import Grid
from app.models import AnalysisRequest, Scene, validate_pair
from app.pipeline import ANALYSIS_SOURCE_POLICY, run_analysis
from app.storage import sha256
from app.presets import osm_cutoff


CDSE_PROVIDER = "Copernicus Data Space Sentinel Hub Processing API"
RADAR_PRODUCT = re.compile(r"^S1[A-D]_IW_GRD[HMF]_1SDV_\d{8}T\d{6}_\d{8}T\d{6}_")


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("Cached acquisition timestamps must be ISO dates with a timezone.") from error
    if parsed.tzinfo is None:
        raise ValueError("Cached acquisition timestamps must include a timezone.")
    return parsed.astimezone(timezone.utc)


def load_cached_cdse_inputs(source_folder):
    """Validate a frozen acquisition and return its original parameters/inputs.

    Only source metadata from result.json is consumed. Its predicted layers,
    statistics and evaluation are never used for the new analysis.
    """
    source = Path(source_folder).resolve()
    result_path = source / "result.json"
    if not result_path.is_file() or result_path.is_symlink():
        raise ValueError("Cached source must contain a regular result.json file.")
    source_result_sha = sha256(result_path)
    try:
        run = json.loads(result_path.read_text(encoding="utf-8"))
        params = AnalysisRequest.model_validate(run["parameters"])
        provenance = run["provenance"]
        original_id = str(uuid.UUID(run["id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Cached result has invalid run parameters or source metadata.") from error
    if run.get("mode") != "live" or params.mode != "live" or params.satellite_provider != "copernicus":
        raise ValueError("Only real live CDSE caches are supported; synthetic and public-GRD caches are refused.")
    acquisition = provenance.get("acquisition") or {}
    if acquisition.get("provider") != CDSE_PROVIDER or provenance.get("source_policy") != ANALYSIS_SOURCE_POLICY:
        raise ValueError("Cached acquisition does not declare the approved CDSE/source-input policy.")
    dem_source = acquisition.get("dem") or {}
    if not ((dem_source.get("provider") == "Copernicus Data Space Sentinel Hub" and dem_source.get("product") == "COPERNICUS_30") or
            (dem_source.get("provider") == "Public Copernicus DEM GLO-30 mirror" and dem_source.get("tiles") and
             all(str(url).startswith("https://copernicus-dem-30m.s3.amazonaws.com/") for url in dem_source["tiles"]))):
        raise ValueError("Cached elevation does not identify an approved Copernicus GLO-30 source.")
    if run.get("bbox") != params.bbox or str(run.get("flood_date")) != str(params.flood_date):
        raise ValueError("Cached AOI or event date disagrees with its saved acquisition parameters.")
    grid = Grid.for_bbox(params.bbox, params.resolution_m)
    if provenance.get("crs") != grid.crs or provenance.get("resolution_m") != grid.resolution or not np.allclose(provenance.get("grid_bounds", []), grid.bounds, rtol=0, atol=1e-6):
        raise ValueError("Cached acquisition grid disagrees with its saved parameters.")
    try:
        scenes = [Scene.model_validate(item) for item in provenance["radar_scenes"]]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Cached radar scene metadata is invalid.") from error
    if len(scenes) != 2 or any(not RADAR_PRODUCT.match(scene.id) for scene in scenes):
        raise ValueError("Cached source must identify two actual Sentinel-1 IW/DV products.")
    validate_pair(*scenes, params.flood_date)
    if acquisition.get("radar_scene_ids") != [scene.id for scene in scenes]:
        raise ValueError("Cached acquisition scene IDs disagree with the scene metadata.")
    for scene in scenes:
        if scene.platform != "sentinel-1" + scene.id[2].lower():
            raise ValueError("Cached scene platform disagrees with its product ID.")
        product_time = datetime.strptime(scene.id.split("_")[4], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        if scene.acquired.tzinfo is None or abs((scene.acquired-product_time).total_seconds()) >= 1:
            raise ValueError("Cached scene acquisition time disagrees with its product ID.")
    snapshot = provenance.get("osm_snapshot")
    snapshot_instant = _instant(snapshot)
    event = datetime.combine(params.flood_date, datetime.min.time(), tzinfo=timezone.utc)
    cutoff = osm_cutoff(params)
    if snapshot_instant >= event or (cutoff is not None and snapshot_instant > cutoff):
        raise ValueError("Cached OSM does not satisfy the pre-event snapshot rule.")
    optical_scenes = provenance.get("optical_scenes", [])
    optical_observations = provenance.get("optical_observations", [])
    if not isinstance(optical_scenes, list) or len(optical_scenes) not in (0, 2):
        raise ValueError("Cached optical metadata must record either a complete pair or no pair.")
    if optical_scenes:
        if len(optical_observations) != 2 or [item.get("id") for item in optical_observations] != optical_scenes or any("synthetic" in str(identifier).lower() for identifier in optical_scenes):
            raise ValueError("Recorded optical pair lacks matching acquisition metadata.")
        optical_dates = [_instant(item.get("acquired")) for item in optical_observations]
        if optical_dates[0].date() >= params.flood_date or optical_dates[1].date() <= params.flood_date:
            raise ValueError("Cached optical scenes must strictly bracket the flood date.")
    elif optical_observations or any((source / name).exists() for name in ("s2-before.tif", "s2-after.tif")):
        raise ValueError("Unrecorded optical files or dates make this cache ambiguous; refusing replay.")

    names = ["s1-before.tif", "s1-after.tif", "dem.tif", "osm-snapshot.geojson"]
    if optical_scenes:
        names += ["s2-before.tif", "s2-after.tif"]
    for name in names:
        if not (source / name).is_file() or (source / name).is_symlink():
            raise ValueError(f"Cached acquisition is missing a regular input file: {name}.")
    hashes = {name: sha256(source / name) for name in names}

    def read_raster(name, bands):
        with rasterio.open(source / name) as src:
            if src.count != bands or src.crs != rasterio.crs.CRS.from_string(grid.crs) or (src.height, src.width) != (grid.height, grid.width) or not np.allclose(tuple(src.transform), tuple(grid.transform), rtol=0, atol=1e-6):
                raise ValueError(f"Cached {name} does not match the original grid or band contract.")
            if src.tags().get("synthetic", "false").lower() == "true":
                raise ValueError("Synthetic raster caches cannot be replayed as live data.")
            array = src.read().astype("float32")
            if bands > 1 and not np.any(array[-1 if bands == 6 else 1] > 0):
                raise ValueError(f"Cached {name} has no acquisition data-mask support.")
            return array

    before, after = read_raster("s1-before.tif", 4), read_raster("s1-after.tif", 4)
    dem = read_raster("dem.tif", 1)[0]
    optical_before = read_raster("s2-before.tif", 6) if optical_scenes else None
    optical_after = read_raster("s2-after.tif", 6) if optical_scenes else None
    osm = json.loads((source / "osm-snapshot.geojson").read_text(encoding="utf-8"))
    if osm.get("type") != "FeatureCollection" or not isinstance(osm.get("features"), list):
        raise ValueError("Cached OSM source is not a FeatureCollection.")
    metadata = osm.get("metadata") or {}
    if metadata.get("snapshot") and _instant(metadata["snapshot"]) != snapshot_instant:
        raise ValueError("Cached OSM metadata disagrees with the saved historical timestamp.")
    if metadata.get("sourceApi") not in {"https://api.ohsome.org/v1", "https://api.heigit.org/ohsome-api/v2-rc"}:
        raise ValueError("Cached OSM does not identify a historical ohsome source.")
    for feature in osm["features"]:
        properties = feature.get("properties") or {}
        if not re.fullmatch(r"(?:node|way|relation)/\d+", str(properties.get("@osmId", ""))):
            raise ValueError("Cached OSM feature lacks its real historical element ID.")
        if properties.get("@validFrom") or properties.get("@validTo"):
            if not (_instant(properties.get("@validFrom")) <= snapshot_instant < _instant(properties.get("@validTo"))):
                raise ValueError("Cached OSM geometry was not valid at the recorded snapshot.")
        if properties.get("@lastEdit") and _instant(properties["@lastEdit"]) > snapshot_instant:
            raise ValueError("Cached OSM contains edits after its historical snapshot.")
    if source_result_sha != sha256(result_path) or any(hashes[name] != sha256(source / name) for name in names):
        raise ValueError("Cached source changed during validation; retry with immutable inputs.")
    reprocessing = {"mode": "cached_cdse_inputs", "source_run_id": original_id,
                    "source_created_at": run.get("created_at"), "source_result_sha256": source_result_sha,
                    "input_sha256": hashes, "new_acquisition": False}
    warnings = [message for message in run.get("warnings", []) if isinstance(message, str) and
                ("CDSE denied" in message or "optical" in message.lower())]
    warnings.append("This run reprocesses recorded real CDSE inputs using the current algorithm. No new satellite or OSM acquisition was performed.")
    inputs = {"before": before, "after": after, "vh_before": before[3], "vh_after": after[3], "dem": dem,
              "osm": osm, "scenes": scenes, "snapshot": snapshot, "optical_before": optical_before,
              "optical_after": optical_after, "optical_scenes": optical_scenes,
              "optical_observations": optical_observations, "acquisition": copy.deepcopy(acquisition),
              "warnings": warnings, "reprocessing": reprocessing}
    return params, inputs


def reprocess_saved_cdse(source_folder, output_folder, run_id=None, progress=lambda label, percent: None):
    source, destination = Path(source_folder).resolve(), Path(output_folder).resolve()
    if source == destination or source in destination.parents or destination.exists():
        raise ValueError("Reprocessing needs a new separate output folder; existing runs are never overwritten.")
    params, inputs = load_cached_cdse_inputs(source)
    new_id = str(uuid.UUID(str(run_id))) if run_id is not None else str(uuid.uuid4())
    if new_id == inputs["reprocessing"]["source_run_id"]:
        raise ValueError("Reprocessing must use a new run ID.")
    destination.mkdir(parents=True, exist_ok=False)
    for name, expected in inputs["reprocessing"]["input_sha256"].items():
        shutil.copy2(source / name, destination / name)
        if sha256(destination / name) != expected:
            raise ValueError("An input changed while copying the frozen cache; no new result was produced.")
    return run_analysis(params, new_id, destination, progress, acquired_inputs=inputs)
