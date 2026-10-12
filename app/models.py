from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ATTRIBUTIONS = [
    "Contains modified Copernicus Sentinel data 2026.",
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.",
    "© OpenStreetMap contributors.",
]

LIMITATIONS = [
    "Educational prototype. Satellite estimates require independent field verification.",
    "Satellite revisits take days; this system cannot warn of a sudden glacier collapse minutes ahead.",
    "Radar shadow, layover, vegetation and urban double-bounce can hide flooding or produce false changes. Steep slopes and shadow are masked when DEM and auxiliary data are available; layover is not fully modeled.",
    "Optical cloud, snow and cloud-shadow pixels are excluded. Debris is a low-confidence change candidate, not a confirmed debris-flow classification.",
    "Buildings, roads and bridges intersecting mapped changes are potentially exposed, not verified damaged or destroyed.",
    "Road isolation assumes exposed segments are impassable; incomplete OSM mapping, snapping and routes outside the analysis area can change the result.",
    "Population, deaths, missing people, injuries and rescue priorities cannot be inferred from these inputs.",
    "Downhill DEM tracing is a terrain screening tool, not a hydraulic flood or travel-time simulation.",
]


class AnalysisRequest(BaseModel):
    name: str = Field(default="Trishuli river corridor", min_length=1, max_length=120)
    bbox: list[float] = Field(default_factory=lambda: [85.19, 27.90, 85.41, 28.30], min_length=4, max_length=4)
    flood_date: date = date(2026, 8, 26)
    mode: Literal["demo", "live"] = "live"
    satellite_provider: Literal["public", "copernicus"] = "public"
    water_model: Literal["trained", "threshold"] = "trained"
    resolution_m: int = Field(default=30, ge=10, le=100)
    search_days: int = Field(default=24, ge=12, le=60)
    water_threshold_db: float = Field(default=-17, ge=-30, le=-5)
    change_threshold_db: float = Field(default=3, ge=1, le=12)
    max_slope_deg: float = Field(default=20, ge=5, le=45)
    min_component_pixels: int = Field(default=9, ge=1, le=1000)

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


class CopilotRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    language: Literal["en", "ne"] = "en"


class TraceRequest(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


def validate_pair(before: Scene, after: Scene, event: date) -> None:
    if before.acquired.date() >= event or after.acquired.date() <= event:
        raise ValueError("Radar scenes must be strictly before and after the flood date.")
    for key in ("relative_orbit", "direction", "platform", "polarization", "instrument_mode"):
        if getattr(before, key) != getattr(after, key):
            raise ValueError(f"Incompatible radar pair: {key} differs.")

