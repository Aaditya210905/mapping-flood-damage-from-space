"""Case-specific inputs shared by the API, acquisition and dashboard."""
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator

class CasePreset(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[a-z0-9_-]{1,60}$')
    name:str=Field(min_length=1,max_length=120)
    bbox:list[float]=Field(min_length=4,max_length=4)
    flood_date:date
    osm_snapshot_cutoff:datetime|None=None

    @model_validator(mode='after')
    def valid_geography_and_cutoff(self):
        west,south,east,north=self.bbox
        if not (-180<=west<east<=180 and -75<=south<north<=75 and east-west<=1 and north-south<=1):
            raise ValueError('Preset bounds must fit the supported geography and one-degree tile limit')
        cutoff=self.osm_snapshot_cutoff
        if cutoff is not None and (cutoff.tzinfo is None or cutoff.astimezone(timezone.utc).date()>=self.flood_date):
            raise ValueError('Preset OSM cutoff must be timezone-aware and strictly before the event')
        return self


PRESETS={key:CasePreset.model_validate(value).model_dump(mode='json') for key,value in json.loads(
    Path(os.getenv('FLOOD_LENS_PRESETS_FILE') or Path(__file__).with_name('presets.json')).read_text()).items()}
if any(key!=preset['id'] or key=='custom' for key,preset in PRESETS.items()):
    raise ValueError('Preset IDs must match their keys and cannot be custom')
# Compatibility for imports in dated case-study utilities; not a request default.
TRISHULI=PRESETS.get('trishuli',{})


def osm_cutoff(params):
    case_id = getattr(params, "case_id", None)
    # A custom request never acquires a preset's policy by matching its location.
    preset = PRESETS.get(case_id)
    if preset and preset.get('osm_snapshot_cutoff') and params.flood_date == date.fromisoformat(preset["flood_date"]):
        w, s, e, n = params.bbox
        pw, ps, pe, pn = preset["bbox"]
        if not (pw <= w < e <= pe and ps <= s < n <= pn):
            return None
        return datetime.fromisoformat(preset["osm_snapshot_cutoff"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return None
