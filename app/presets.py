"""Case-specific inputs shared by the API, acquisition and dashboard."""
from datetime import date, datetime, timezone

TRISHULI = {"id": "trishuli", "name": "Trishuli river corridor", "bbox": [85.19, 27.90, 85.41, 28.30],
            "flood_date": "2026-08-26", "osm_snapshot_cutoff": "2026-07-27T23:59:59Z"}
PRESETS = {TRISHULI["id"]: TRISHULI}


def osm_cutoff(params):
    case_id = getattr(params, "case_id", None)
    # Old clients/caches have no preset ID. Recognise this case only by its
    # event AND location, never by a date shared with an unrelated flood.
    if case_id is None:
        w, s, e, n = params.bbox
        pw, ps, pe, pn = TRISHULI["bbox"]
        if str(params.flood_date) == TRISHULI["flood_date"] and pw <= w < e <= pe and ps <= s < n <= pn:
            case_id = "trishuli"
    preset = PRESETS.get(case_id)
    if preset and params.flood_date == date.fromisoformat(preset["flood_date"]):
        w, s, e, n = params.bbox
        pw, ps, pe, pn = preset["bbox"]
        if not (pw <= w < e <= pe and ps <= s < n <= pn):
            return None
        return datetime.fromisoformat(preset["osm_snapshot_cutoff"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return None
