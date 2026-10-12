"""Reference comparison is a separate, read-only path after prediction."""
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import shape

from app.geo import Grid


def binary_metrics(prediction, reference, valid):
    prediction, reference = prediction[valid], reference[valid]
    tp = int(np.sum(prediction & reference))
    fp = int(np.sum(prediction & ~reference))
    fn = int(np.sum(~prediction & reference))
    tn = int(np.sum(~prediction & ~reference))
    return {"true_positive_pixels": tp, "false_positive_pixels": fp, "false_negative_pixels": fn, "true_negative_pixels": tn,
            "iou": tp / (tp + fp + fn) if tp + fp + fn else 1.0,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0}


def evaluate_run(folder, reference_path, coverage_path, activation="EMSR927", allow_demo=False):
    folder = Path(folder)
    run = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    if run["mode"] == "demo" and not allow_demo:
        raise ValueError("Synthetic runs cannot produce real case-study accuracy. Use a live run, or --allow-demo for explicitly synthetic sanity checks.")
    with rasterio.open(folder / "change-mask.tif") as src:
        classes = src.read(1)
        grid = Grid(str(src.crs), src.transform, src.width, src.height, abs(src.transform.a))
    def read_polygons(path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("type") != "FeatureCollection":
            raise ValueError("Reference and coverage must be WGS84 GeoJSON FeatureCollections.")
        geometries = []
        for feature in data["features"]:
            geom = shape(feature["geometry"])
            if geom.geom_type not in ("Polygon", "MultiPolygon") or not geom.is_valid:
                raise ValueError("Supply valid reference/coverage polygons; extract inundation/debris separately from published asset damage layers.")
            west, south, east, north = geom.bounds
            if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
                raise ValueError("Evaluation inputs must be WGS84 longitude/latitude, not projected coordinates.")
            geometries.append(grid.project(geom))
        return geometries
    reference_geometries, coverage_geometries = read_polygons(reference_path), read_polygons(coverage_path)
    reference_data = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    reference_classes = sorted({str(f.get("properties", {}).get("obj_desc", "unspecified")) for f in reference_data["features"]})
    landslide_only = bool(reference_classes) and all("landslide" in name.lower() for name in reference_classes)
    if not coverage_geometries:
        raise ValueError("Reviewed reference coverage cannot be empty.")
    def burn(geometries):
        if not geometries:
            return np.zeros(classes.shape, dtype=bool)
        return rasterize([(g, 1) for g in geometries], out_shape=classes.shape, transform=grid.transform, fill=0, dtype="uint8").astype(bool)
    reviewed = burn(coverage_geometries)
    reference = burn(reference_geometries)
    valid = (classes != 255) & reviewed
    if not valid.any():
        raise ValueError("No shared valid prediction and reviewed reference coverage.")
    primary_prediction = np.isin(classes,[2,3])
    primary_valid = valid
    target = "combined supplied extent"
    if landslide_only:
        primary_prediction = classes == 3
        target = "debris candidate versus official landslide extent"
        eligibility = folder / "eligibility.tif"
        if eligibility.exists():
            with rasterio.open(eligibility) as source:
                primary_valid = reviewed & (source.read(3) > 0)
        if not primary_valid.any():
            raise ValueError("No shared clear optical eligibility for the landslide/debris comparison.")
    result = {"activation": activation, "mode": "synthetic sanity check" if run["mode"] == "demo" else "reference comparison",
              "run_id": run["id"], "reference_file": str(Path(reference_path).name), "coverage_file": str(Path(coverage_path).name),
              "evaluated_pixels": int(valid.sum()), "evaluated_area_km2": float(valid.sum()*grid.resolution**2/1e6),
              "reviewed_pixels_inside_grid": int(reviewed.sum()), "valid_reviewed_coverage_pct": float(100*valid.sum()/reviewed.sum()),
              "primary_target":target, "primary_evaluated_pixels":int(primary_valid.sum()),
              "metrics": binary_metrics(primary_prediction, reference, primary_valid),
              "combined_diagnostic":binary_metrics(np.isin(classes,[2,3]),reference,valid),
              "water_validation":{"status":"unavailable" if landslide_only else "requires class review", "reason":"EMSR927 observed-event polygons are landslides; no water accuracy is inferred." if landslide_only else "Inspect reference water class before interpreting water overlap."},
              "reference_classes": reference_classes,
              "comparison_type": "class-matched debris/landslide comparison; not flood-water accuracy" if landslide_only else "supplied reference extent comparison",
              "separate_classes": {"new_water": binary_metrics(classes == 2, reference, valid),
                                   "debris_candidate": binary_metrics(classes == 3, reference, valid)},
              "attribution": "European Union, Copernicus Emergency Management Service data",
              "limitations": ("The official observed-event polygons are landslides. Primary metrics compare only debris candidates on clear optical coverage; this is not a flood-water accuracy score. " if landslide_only else "") + "Pixel-center comparison inside reviewed coverage. Acquisition timing, candidate-versus-confirmed class definitions and map scale differ. Combined change overlap is retained only as a secondary diagnostic. Structural damage and actual road closures remain unverified."}
    return result
