"""Read-time compatibility for scientific availability, without rewriting saved runs.

A completed processing job can contain incomplete scientific observations. In
particular, an empty output mask is not a measured zero when its eligible domain
is empty. Older saved runs did not make that distinction in their area fields.
"""
import math


def _percentage(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def normalize_analysis_availability(run):
    """Return a shallow normalized view; retain the immutable on-disk evidence.

    Explicit per-class availability takes precedence. Legacy coverage metadata is
    enough to correct the old empty-mask-as-zero bug; absent metadata is not a
    license to invent a coverage value or silently discard a recorded result.
    """
    result = dict(run)
    stats = dict(run.get("stats")) if isinstance(run.get("stats"), dict) else {}
    availability = dict(run.get("class_availability")) if isinstance(run.get("class_availability"), dict) else {}
    partial = run.get("analysis_status") == "partial"
    for kind in ("water", "debris"):
        entry = availability.get(kind)
        entry = dict(entry) if isinstance(entry, dict) else {}
        field = kind + "_eligible_coverage_pct"
        coverage = entry.get("coverage_pct", stats.get(field))
        if not _percentage(coverage):
            coverage = None
        status = entry.get("status")
        if entry.get("eligible_pixels") == 0 or coverage == 0:
            status = "unavailable"
        elif status not in ("available", "partial", "unavailable") and coverage is not None:
            status = "available" if coverage == 100 else "partial"
        if status in ("available", "partial", "unavailable"):
            entry["status"] = status
            entry["coverage_pct"] = coverage
            if coverage is not None:
                stats[field] = coverage
            if status == "unavailable":
                stats[kind + "_km2"] = None
                if kind == "water":
                    stats["permanent_water_km2"] = None
                entry["missing_reason"] = entry.get("missing_reason") or (
                    "No pixels were eligible for this class; its area is unavailable, not zero."
                )
            else:
                entry["missing_reason"] = None
            availability[kind] = entry
            partial |= status != "available"
    if stats.get("valid_coverage_pct") is not None and _percentage(stats["valid_coverage_pct"]):
        partial |= stats["valid_coverage_pct"] < 100
    result["stats"] = stats
    if all(availability.get(kind, {}).get("status") == "unavailable" for kind in ("water", "debris")):
        for field in ("buildings_exposed", "roads_exposed", "bridges_exposed", "road_exposed_km", "settlements_cut_off"):
            stats[field] = None
    if availability:
        result["class_availability"] = availability
    if partial:
        result["analysis_status"] = "partial"
    return result
