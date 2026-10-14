from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely import segmentize
from shapely.geometry import box

from app.demo import make_demo
from app.copilot import COPILOT_INFO
from app.geo import Grid, detect_changes, extent_features, image_bytes, clean_mask
from app.impact import analyze_impacts
from app.models import ATTRIBUTIONS, LIMITATIONS, validate_pair
from app.providers import acquire_live
from app.public_data import acquire_public
from app.sources import MAPPING_METHOD


def class_availability(detected, aoi, grid, optical_pair_available):
    """Describe each class's observation domain independently of detections."""
    total = int(aoi.sum())
    classes = {}
    for kind in ("water", "debris"):
        count = int(detected[f"{kind}_eligible"].sum())
        status = "unavailable" if not count else "available" if count == total else "partial"
        reason = None
        if not count:
            if kind == "debris":
                reason = ("No suitable paired optical scenes are available for debris assessment."
                          if not optical_pair_available else
                          "No paired clear, usable optical pixels remain for debris assessment.")
            else:
                reason = "No observed low-slope pixels with usable radar or paired optical evidence remain for water assessment."
        classes[kind] = {
            "status": status,
            "eligible_pixels": count,
            "aoi_pixels": total,
            "eligible_area_km2": round(count * grid.resolution ** 2 / 1e6, 3),
            "coverage_pct": round(100 * count / max(1, total), 1),
            "missing_reason": reason,
            "interpretation": ("Potential new water within eligible low-slope observations"
                               if kind == "water" else
                               "Possible debris or land-cover change within paired clear optical observations"),
        }
    return classes


def run_analysis(params, run_id, folder, progress=lambda label, percent: None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    grid = Grid.for_bbox(params.bbox, params.resolution_m)
    progress("Preparing analysis grid", 5)
    if params.mode == "demo":
        inputs = make_demo(grid, params)
    elif params.satellite_provider == "public":
        inputs = acquire_public(params, grid, folder, progress)
    else:
        inputs = acquire_live(params, grid, folder, progress)
    validate_pair(*inputs["scenes"], params.flood_date)
    if str(inputs["snapshot"])[:10] >= str(params.flood_date):
        raise ValueError("OSM snapshot must predate the event.")
    if params.mode != "demo" and str(params.flood_date) == "2026-08-26" and str(inputs["snapshot"])[:10] > "2026-07-27":
        raise ValueError("The Nepal case requires historical OSM from 27 July 2026 or earlier. Reacquire an allowed snapshot before analysis.")
    progress("Mapping new inundation and possible debris", 72)
    detected = detect_changes(inputs["before"], inputs["after"], grid, params, inputs["dem"], inputs["optical_before"], inputs["optical_after"])
    # A geographic rectangle is not axis-aligned in UTM. Exclude padding cells
    # outside the requested AOI rather than counting the larger raster envelope.
    aoi = rasterize([(grid.project(segmentize(box(*params.bbox), max_segment_length=.001)), 1)], out_shape=(grid.height, grid.width),
                    transform=grid.transform, fill=0, dtype="uint8").astype(bool)
    for key in ("water", "debris", "valid", "permanent", "optical_valid", "optical_water", "radar_valid", "water_eligible", "debris_eligible"):
        detected[key] &= aoi
    detected["classes"][~aoi] = 255
    # Clipping to the geographic AOI can leave tiny boundary fragments from a
    # previously large component. Apply the size rule to the final output too.
    for kind, number in (("water", 2), ("debris", 3)):
        detected[kind] = clean_mask(detected[kind], params.min_component_pixels)
        detected["classes"][(detected["classes"] == number) & ~detected[kind]] = 0
    if not detected["valid"].any():
        raise ValueError("No eligible radar or clear optical pixels remain after quality and terrain masks.")
    extent = extent_features(detected, grid)
    progress("Intersecting infrastructure and testing road connectivity", 86)
    layers, stats = analyze_impacts(
        inputs["osm"], extent, grid, assessment_mask=detected["valid"],
        assessment_masks={"water": detected["water_eligible"], "debris": detected["debris_eligible"]},
    )
    layers["extent"] = extent
    area_per_pixel = grid.resolution ** 2 / 1e6
    availability = class_availability(
        detected, aoi, grid,
        inputs["optical_before"] is not None and inputs["optical_after"] is not None,
    )
    stats.update({
        "water_km2": round(float(detected["water"].sum()) * area_per_pixel, 3) if availability["water"]["eligible_pixels"] else None,
        "debris_km2": round(float(detected["debris"].sum()) * area_per_pixel, 3) if availability["debris"]["eligible_pixels"] else None,
        "permanent_water_km2": round(float(detected["permanent"].sum()) * area_per_pixel, 3) if availability["water"]["eligible_pixels"] else None,
        "valid_area_km2": round(float(detected["valid"].sum()) * area_per_pixel, 3),
        "valid_coverage_pct": round(float(detected["valid"].sum() / max(1, aoi.sum())) * 100, 1),
        "optical_clear_pct": round(float(detected["optical_valid"].sum() / max(1, aoi.sum())) * 100, 1),
        "radar_valid_coverage_pct": round(float(detected["radar_valid"].sum()/max(1,aoi.sum()))*100,1),
        "water_eligible_coverage_pct": round(float(detected["water_eligible"].sum()/max(1,aoi.sum()))*100,1),
        "debris_eligible_coverage_pct": round(float(detected["debris_eligible"].sum()/max(1,aoi.sum()))*100,1),
    })
    warnings = list(inputs.get("warnings", []))
    if params.mode == "demo":
        warnings.insert(0, "All satellite-like inputs, terrain and OSM-like features in this run are synthetic. No results describe the real Trishuli event.")
    if stats["settlements_unassessed"]:
        warnings.append("Some settlements have no nearby mapped road or no mapped destination; they are unassessed, not newly cut off.")
    if stats["valid_coverage_pct"] < 50:
        warnings.append("Less than half the area has eligible water or debris observations. Unobserved infrastructure and access remain uncertain.")
    for kind, coverage in availability.items():
        if coverage["status"] == "unavailable":
            warnings.append(f"{kind.capitalize()} assessment unavailable: {coverage['missing_reason']} Its area is unknown, not zero.")
        elif coverage["status"] == "partial":
            warnings.append(f"{kind.capitalize()} assessment covers {coverage['coverage_pct']:.1f}% of the requested area. Reported area applies only to eligible observations.")
    run = {"id": run_id, "name": params.name, "mode": params.mode, "flood_date": str(params.flood_date),
           "created_at": datetime.now(timezone.utc).isoformat(), "bbox": params.bbox,
           "parameters": params.model_dump(mode="json"), "stats": stats, "layers": layers,
           "analysis_status": "complete" if all(c["status"] == "available" for c in availability.values()) else "partial",
           "class_availability": availability,
           "provenance": {"radar_scenes": [s.model_dump(mode="json") for s in inputs["scenes"]], "osm_snapshot": inputs["snapshot"],
                          "optical_scenes": inputs["optical_scenes"], "radar_processing": inputs.get("acquisition", {}).get("processing", "linear gamma0 terrain, Copernicus DEM 30, 3x3 median in dB"),
                          "optical_observations": inputs.get("optical_observations", []),
                          "acquisition": inputs.get("acquisition", {}), "mapping": MAPPING_METHOD,
                          "coverage_definitions": "radar_valid: physical screening/context; water_eligible: observed low-slope targets; debris_eligible: paired clear optical; valid: union of eligible water and debris targets",
                          "speckle_filter": {"method": "mask-aware median in dB", "window_pixels": 3,
                                             "minimum_observed_samples": 3, "invalid_neighbour_fill": None},
                          "osm_source_api": inputs["osm"].get("metadata", {}).get("sourceApi", "synthetic"),
                          "crs": grid.crs, "resolution_m": grid.resolution, "grid_bounds": grid.bounds,
                          "source_policy": "Only Sentinel-1, Sentinel-2, Copernicus DEM and pre-event OSM are analysis inputs. No flood-segmentation model or external training dataset is used. Published damage maps are evaluation-only.",
                          "ai_component": COPILOT_INFO},
           "warnings": warnings, "limitations": LIMITATIONS, "attributions": ATTRIBUTIONS,
           "evaluation": {"status": "pending", "activation": "EMSR927", "reason": "No official reference inundation geometry and reviewed coverage have been supplied. No accuracy claim is made."}}
    progress("Saving maps, audit metadata and reports", 95)
    for epoch in ("before", "after"):
        png, display_bbox = image_bytes(detected, inputs["dem"], epoch, grid)
        (folder / f"{epoch}.png").write_bytes(png)
        run["display_bbox"] = display_bbox
    # GeoTIFF preserves georeferencing for reproducibility and separate evaluation.
    with rasterio.open(folder / "change-mask.tif", "w", driver="GTiff", height=grid.height, width=grid.width,
                       count=1, dtype="uint8", crs=grid.crs, transform=grid.transform, nodata=255, compress="deflate") as dst:
        dst.write(detected["classes"], 1)
        dst.update_tags(classes="0=unmapped change,1=preexisting water,2=new water,3=debris candidate,255=invalid", synthetic=str(params.mode == "demo"))
    with rasterio.open(folder / "dem-grid.tif", "w", driver="GTiff", height=grid.height, width=grid.width,
                       count=1, dtype="float32", crs=grid.crs, transform=grid.transform, nodata=np.nan, compress="deflate") as dst:
        dst.write(inputs["dem"], 1)
    with rasterio.open(folder / "eligibility.tif", "w", driver="GTiff", height=grid.height,width=grid.width,count=3,dtype="uint8",crs=grid.crs,transform=grid.transform,compress="deflate") as dst:
        dst.write(np.stack([detected["radar_valid"],detected["water_eligible"],detected["debris_eligible"]]).astype("uint8"))
        dst.update_tags(bands="1=radar observed,2=water eligible,3=debris eligible; zeros are unassessed for that layer")
    (folder / "osm-snapshot.geojson").write_text(json.dumps(inputs["osm"], ensure_ascii=False), encoding="utf-8")
    for key, layer in layers.items():
        (folder / f"{key}.geojson").write_text(json.dumps(layer, ensure_ascii=False), encoding="utf-8")
    (folder / "result.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    progress("Analysis complete (partial observation coverage)" if run["analysis_status"] == "partial" else "Analysis complete", 100)
    return run
