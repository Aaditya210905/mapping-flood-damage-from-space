"""Shared, versioned mapping configuration; no credentials or case outputs."""
from pathlib import Path
import tomllib

from pydantic import BaseModel, ConfigDict, Field


class MappingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    resolution_m: int = Field(ge=10, le=100)
    search_days: int = Field(ge=12, le=60)
    water_threshold_db: float = Field(ge=-30, le=-5)
    change_threshold_db: float = Field(ge=1, le=12)
    max_slope_deg: float = Field(ge=5, le=45)
    min_component_pixels: int = Field(ge=1, le=1000)
    optical_water_threshold: float = Field(ge=-1, le=1)
    vegetation_baseline_threshold: float = Field(ge=-1, le=1)
    vegetation_loss_threshold: float = Field(gt=0, le=2)
    bare_surface_increase_threshold: float = Field(gt=0, le=2)
    optical_only_vegetation_loss_threshold: float = Field(gt=0, le=2)
    optical_only_bare_increase_threshold: float = Field(gt=0, le=2)
    road_overlap_cell_fraction: float = Field(gt=0, le=1)
    road_snap_distance_m: float = Field(ge=0, le=1000)


MAPPING = MappingSettings.model_validate(tomllib.loads(Path(__file__).with_name("mapping.toml").read_text()))
