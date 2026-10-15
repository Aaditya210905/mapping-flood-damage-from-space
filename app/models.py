from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from app.presets import PRESETS
from app.settings import MAPPING, ACQUISITION


ATTRIBUTIONS = [
    "Contains modified Copernicus Sentinel data.",
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.",
    "© OpenStreetMap contributors.",
]

LIMITATIONS = [
    "Educational prototype. Satellite estimates require independent field verification.",
    "Satellite revisits take days; this system cannot warn of a sudden glacier collapse minutes ahead.",
    "Radar shadow, layover, vegetation and urban double-bounce affect inference. Annotation/orbit and DEM screening covers local geometry over a finite horizon; distant occlusion and subpixel terrain remain uncertain. The recorded source warnings describe the screening distance. Water targets use a separate slope eligibility mask.",
    "Optical cloud, snow and cloud-shadow pixels are excluded. Debris is a low-confidence change candidate, not a confirmed debris-flow classification.",
    "Intersecting buildings, roads and bridges are potentially exposed, not verified damaged. Counts cover unique mapped OSM elements, may miss rural assets and may count one physical bridge more than once. Mapped hospitals are not verified operational.",
    "Road isolation is an assumed scenario: continuous candidate overlap of at least half a raster cell removes unbridged road segments; bridge closure candidates require debris overlap. Water beneath bridges and surface changes above tunnels give uncertain access, not confirmed failure.",
    f"Mapped oneway and unconditional vehicle restrictions are used; turn restrictions and original OSM node identity are unavailable. Snaps are limited to {MAPPING.road_snap_distance_m:g} m; off-road connectors are unverified. Unobserved routes and AOI edges can hide alternative access or missing connections.",
    "Cached real analyses retain their original area, acquisition dates and historical OSM snapshot. They do not establish current conditions or results for a different area/date; missing observations are not zero impact.",
    "Population, deaths, missing people, injuries and rescue priorities cannot be inferred from these inputs.",
    "Downhill DEM tracing is a terrain screening tool, not a hydraulic flood or travel-time simulation.",
]


class AnalysisRequest(BaseModel):
    name: str = Field(default='Custom flood analysis', min_length=1, max_length=120)
    bbox: list[float] = Field(min_length=4, max_length=4)
    flood_date: date
    case_id: str | None = Field(default=None,pattern=r'^[a-z0-9_-]{1,60}$')
    mode: Literal["demo", "live"] = "live"
    satellite_provider: Literal["auto", "public", "copernicus"] = ACQUISITION.default_provider
    resolution_m: int = Field(default=MAPPING.resolution_m, ge=10, le=100)
    search_days: int = Field(default=MAPPING.search_days, ge=12, le=60)
    water_threshold_db: float = Field(default=MAPPING.water_threshold_db, ge=-30, le=-5)
    change_threshold_db: float = Field(default=MAPPING.change_threshold_db, ge=1, le=12)
    max_slope_deg: float = Field(default=MAPPING.max_slope_deg, ge=5, le=45)
    min_component_pixels: int = Field(default=MAPPING.min_component_pixels, ge=1, le=1000)

    @model_validator(mode="before")
    @classmethod
    def reject_segmentation_mode(cls, values):
        # Older baseline clients may still send water_model="threshold".
        # Reject removed modes instead of silently changing their method.
        if isinstance(values, dict) and values.get("water_model", "threshold") != "threshold":
            raise ValueError("This project uses the situation-report copilot route with rule-based mapping. Flood-segmentation models are not supported.")
        if isinstance(values,dict) and values.get('case_id') not in (None,'custom'):
            preset=PRESETS.get(values['case_id'])
            if preset is None:raise ValueError('Unknown preset; supply custom bounds/date or a configured preset ID')
            defaults={key:preset[key] for key in ('name','bbox','flood_date')}
            if (values.get('bbox',preset['bbox'])!=preset['bbox'] or str(values.get('flood_date',preset['flood_date']))!=preset['flood_date']):
                defaults['name']='Custom flood analysis'
            values={**defaults,**values}
        return values

    @model_validator(mode="after")
    def valid_extent(self):
        west, south, east, north = self.bbox
        if not (-180 <= west < east <= 180 and -75 <= south < north <= 75):
            raise ValueError("Use west,south,east,north bounds within 75° latitude; antimeridian areas must be split.")
        if (east - west) > 1 or (north - south) > 1:
            raise ValueError("This prototype handles tiles up to one degree per side. Split larger areas.")
        if self.flood_date >= datetime.now(timezone.utc).date():
            raise ValueError("Choose a past flood date with post-event acquisitions available.")
        return self


class Scene(BaseModel):
    id: str
    acquired: datetime
    relative_orbit: int
    direction: Literal["ascending", "descending"]
    platform: str = "sentinel-1a"
    polarization: str = "VV"
    instrument_mode: str = "IW"
    catalog_ids:list[str]=Field(default_factory=list)
    acquisition_end:datetime|None=None

    @model_validator(mode='after')
    def coherent_window(self):
        if self.acquisition_end is not None:
            if self.acquired.tzinfo is None or self.acquisition_end.tzinfo is None or self.acquisition_end<self.acquired:
                raise ValueError('A pass mosaic needs an ordered timezone-aware acquisition window')
        if self.catalog_ids and (self.catalog_ids[0]!=self.id or len(self.catalog_ids)!=len(set(self.catalog_ids))):
            raise ValueError('A pass mosaic must record unique catalog IDs beginning with its primary product')
        return self


class CopilotRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    language: Literal["en", "ne"] = "en"
    history: list["ConversationTurn"] = Field(default_factory=list, max_length=6)


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)
    run_id: str = Field(min_length=36, max_length=36)


class ReportRequest(BaseModel):
    language: Literal["en", "ne"] = "en"
    use_llm: bool = True


class TraceRequest(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


def validate_pair(before: Scene, after: Scene, event: date) -> None:
    if (before.acquired.date() >= event or (before.acquisition_end or before.acquired).date()>=event
            or after.acquired.date() <= event):
        raise ValueError("Radar scenes must be strictly before and after the flood date.")
    for key in ("relative_orbit", "direction", "platform", "polarization", "instrument_mode"):
        if getattr(before, key) != getattr(after, key):
            raise ValueError(f"Incompatible radar pair: {key} differs.")
