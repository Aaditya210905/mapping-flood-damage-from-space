"""Reference comparison is a separate, read-only path after prediction."""
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import shape

from app.geo import Grid
from app.storage import sha256


def binary_metrics(prediction, reference, valid):
    prediction, reference = np.asarray(prediction, dtype=bool)[valid], np.asarray(reference, dtype=bool)[valid]
    tp = int(np.sum(prediction & reference))
    fp = int(np.sum(prediction & ~reference))
    fn = int(np.sum(~prediction & reference))
    tn = int(np.sum(~prediction & ~reference))
    reason = ("No shared eligible reviewed pixels." if not prediction.size else
              "Neither prediction nor reference contains a positive pixel in the evaluated area." if not tp + fp + fn else None)
    return {"true_positive_pixels": tp, "false_positive_pixels": fp, "false_negative_pixels": fn, "true_negative_pixels": tn,
            "iou": tp / (tp + fp + fn) if tp + fp + fn else None,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "status": "available" if tp + fp + fn else "unavailable", "reason": reason}


def _reference_kind(properties):
    """Recognise explicit labels only; an unspecified polygon is not flood truth."""
    for field in ("prediction_class", "kind", "obj_desc", "class", "classification"):
        value = str(properties.get(field, "")).strip().lower().replace("_", " ").replace("-", " ")
        if value in {"water", "new water", "flood water", "flooded area", "inundation", "flood inundation"}:
            return "water"
        if value in {"debris", "debris candidate", "landslide", "landslide area", "debris flow"}:
            return "debris"
    return None


def evaluate_run(folder, reference_path, coverage_path, activation="EMSR927", allow_demo=False, reference_class=None):
    if reference_class not in {None, "water", "debris", "combined"}:
        raise ValueError("reference_class must be water, debris or combined after reviewing the reference legend.")
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
        records = []
        for feature in data["features"]:
            geom = shape(feature["geometry"])
            if geom.geom_type not in ("Polygon", "MultiPolygon") or not geom.is_valid or geom.is_empty:
                raise ValueError("Supply valid reference/coverage polygons; extract inundation/debris separately from published asset damage layers.")
            west, south, east, north = geom.bounds
            if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
                raise ValueError("Evaluation inputs must be WGS84 longitude/latitude, not projected coordinates.")
            records.append((grid.project(geom), feature.get("properties") or {}))
        return records

    reference_records = read_polygons(reference_path)
    coverage_records = read_polygons(coverage_path)
    if not coverage_records:
        raise ValueError("Reviewed reference coverage cannot be empty.")

    def burn(geometries):
        if not geometries:
            return np.zeros(classes.shape, dtype=bool)
        return rasterize([(g, 1) for g in geometries], out_shape=classes.shape, transform=grid.transform, fill=0, dtype="uint8").astype(bool)

    reviewed = burn([g for g, _ in coverage_records])
    if not reviewed.any():
        raise ValueError("No reviewed reference coverage overlaps the prediction grid.")
    observed = classes != 255
    reference = burn([g for g, _ in reference_records])
    raw_labels = sorted({str(props.get("obj_desc", props.get("kind", props.get("class", "unspecified")))) for _, props in reference_records})
    kinds = [_reference_kind(props) for _, props in reference_records]
    unclassified = sum(kind is None for kind in kinds)
    # Empty reference sets require an explicit declaration of the reviewed
    # class. Absence of a polygon does not identify what was reviewed.
    matched_kinds = set(kinds) - {None}
    class_reviewed = reference_class is not None or (bool(kinds) and not unclassified)
    targets = {reference_class} if reference_class in {"water", "debris"} else matched_kinds
    combined = reference_class == "combined" or len(targets) > 1
    primary_target = ("combined new-water and debris extent" if combined else
                      "new water versus supplied water extent" if targets == {"water"} else
                      "debris candidate versus supplied debris/landslide extent" if targets == {"debris"} else
                      "unreviewed reference class")

    eligibility = {}
    eligibility_path = folder / "eligibility.tif"
    if eligibility_path.exists():
        with rasterio.open(eligibility_path) as src:
            if src.count < 3 or (src.height, src.width) != classes.shape or src.crs != rasterio.crs.CRS.from_string(grid.crs) or src.transform != grid.transform:
                raise ValueError("Class eligibility must use the prediction grid and contain water/debris bands.")
            eligibility = {"water": (src.read(2) > 0) & observed, "debris": (src.read(3) > 0) & observed}

    empty = np.zeros(classes.shape, dtype=bool)
    class_results = {}
    for kind, code in (("water", 2), ("debris", 3)):
        # An explicit combined reference without individual labels cannot
        # support per-class scores. Never compare water to landslide polygons.
        confirmed = class_reviewed and (reference_class == kind or (reference_class is None and kind in matched_kinds))
        valid = reviewed & eligibility.get(kind, empty) if confirmed else empty
        geoms = [geom for (geom, _), label in zip(reference_records, kinds) if label == kind]
        class_reference = reference if reference_class == kind else burn(geoms)
        metrics = binary_metrics(classes == code, class_reference, valid)
        reason = ("Reference has no reviewed matching class." if not confirmed else
                  "Saved per-class eligibility is unavailable." if not eligibility else
                  "No shared eligible reviewed pixels for this class." if not valid.any() else None)
        class_results["new_water" if kind == "water" else "debris_candidate"] = {
            **metrics, "status": "unavailable" if reason else metrics["status"],
            "reason": reason or metrics["reason"], "evaluated_pixels": int(valid.sum()),
        }

    if not class_reviewed:
        primary_valid = empty
        status, reason = "class_review_required", "Review the reference legend and supply explicit matching classes; unclassified polygons are not used for accuracy."
    elif not eligibility:
        primary_valid = empty
        status, reason = "unavailable", "Saved per-class eligibility is unavailable; union coverage cannot establish class-specific observability."
    else:
        if combined:
            primary_valid = reviewed & eligibility["water"] & eligibility["debris"]
        else:
            primary_valid = reviewed & eligibility[next(iter(targets))]
        status, reason = ("available", None) if primary_valid.any() else ("unavailable", "No shared class-eligible prediction and reviewed reference coverage.")
    primary_prediction = np.isin(classes, [2, 3]) if combined else classes == (2 if targets == {"water"} else 3)
    metrics = binary_metrics(primary_prediction, reference, primary_valid)
    if status == "available" and metrics["status"] == "unavailable":
        status, reason = "unavailable", metrics["reason"]
    reference_reviewed = reference & reviewed
    reference_evaluated = reference_reviewed & primary_valid
    result = {
        "activation": activation, "mode": "synthetic sanity check" if run["mode"] == "demo" else "reference comparison",
        "status": status, "reason": reason, "run_id": run["id"],
        "reference_file": str(Path(reference_path).name), "coverage_file": str(Path(coverage_path).name),
        "reference_class_override": reference_class,
        "input_sha256": {"prediction": sha256(folder / "change-mask.tif"),
                         "result": sha256(folder / "result.json"),
                         "eligibility": sha256(eligibility_path) if eligibility_path.exists() else None,
                         "reference": sha256(reference_path), "reviewed_coverage": sha256(coverage_path)},
        "reference_classes": raw_labels, "unclassified_reference_features": unclassified,
        "evaluated_pixels": int(primary_valid.sum()), "evaluated_area_km2": float(primary_valid.sum() * grid.resolution ** 2 / 1e6),
        "reviewed_pixels_inside_grid": int(reviewed.sum()),
        "valid_reviewed_coverage_pct": float(100 * primary_valid.sum() / reviewed.sum()),
        "reference_positive_pixels_inside_reviewed": int(reference_reviewed.sum()),
        "reference_positive_evaluated_pixels": int(reference_evaluated.sum()),
        "reference_positive_evaluated_pct": float(100 * reference_evaluated.sum() / reference_reviewed.sum()) if reference_reviewed.any() else None,
        "primary_target": primary_target, "primary_evaluated_pixels": int(primary_valid.sum()), "metrics": metrics,
        "separate_classes": class_results,
        "water_validation": {"status": class_results["new_water"]["status"], "reason": class_results["new_water"]["reason"]},
        "comparison_type": "class-matched supplied reference comparison" if class_reviewed else "reference class review pending",
        "attribution": "European Union, Copernicus Emergency Management Service data",
        "limitations": "Pixel-center comparison inside reviewed and class-eligible coverage. Combined scores require both water and debris eligibility. Empty positive classes have no IoU/F1 score. Acquisition timing, candidate-versus-confirmed class definitions and map scale differ. Structural damage and actual road closures remain unverified.",
    }
    return result
