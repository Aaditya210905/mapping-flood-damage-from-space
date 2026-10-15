"""Shared, versioned mapping configuration; no credentials or case outputs."""
from pathlib import Path
import os
import tomllib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    optical_excluded_scl_classes:list[int]=Field(min_length=1,max_length=12)
    vegetation_baseline_threshold: float = Field(ge=-1, le=1)
    vegetation_loss_threshold: float = Field(gt=0, le=2)
    bare_surface_increase_threshold: float = Field(gt=0, le=2)
    optical_only_vegetation_loss_threshold: float = Field(gt=0, le=2)
    optical_only_bare_increase_threshold: float = Field(gt=0, le=2)
    road_overlap_cell_fraction: float = Field(gt=0, le=1)
    road_snap_distance_m: float = Field(ge=0, le=1000)

    @field_validator('optical_excluded_scl_classes')
    @classmethod
    def scl_classes(cls,value):
        if len(value)!=len(set(value)) or any(number<0 or number>11 for number in value):
            raise ValueError('SCL exclusions require unique official class codes between zero and eleven')
        return value


class AcquisitionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    version:str
    default_provider:Literal['auto','public','copernicus']
    cdse_stac_url:str
    cdse_process_url:str
    cdse_token_url:str
    osm_url:str
    osm_v2_url:str
    catalog_page_size:int=Field(ge=1,le=1000)
    catalog_max_pages:int=Field(ge=1,le=100)
    catalog_timeout_seconds:float=Field(gt=0,le=300)
    process_timeout_seconds:float=Field(gt=0,le=600)
    http_retries:int=Field(ge=0,le=5)
    max_retry_wait_seconds:float=Field(ge=0,le=60)
    token_expiry_margin_seconds:float=Field(ge=0,le=300)
    minimum_scene_coverage:float=Field(gt=0,le=1)
    radar_repeat_days:float=Field(gt=0,le=48)
    radar_pre_gap_weight:float=Field(ge=0,le=10)
    radar_repeat_weight:float=Field(ge=0,le=10)
    optical_day_weight:float=Field(ge=0,le=10)
    optical_pass_tolerance_seconds:float=Field(gt=0,le=1800)
    radar_pass_tolerance_seconds:float=Field(gt=0,le=900)
    scene_window_before_seconds:float=Field(ge=0,le=120)
    scene_window_after_seconds:float=Field(ge=0,le=120)
    terrain_ray_steps:int=Field(ge=1,le=512)
    trace_settlement_buffer_m:float=Field(gt=0,le=10000)

    @field_validator('cdse_stac_url','cdse_process_url','cdse_token_url','osm_url','osm_v2_url')
    @classmethod
    def endpoint(cls,value):
        parsed=urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1','::1')))):
            raise ValueError('Service endpoints require HTTPS or local HTTP without credentials')
        return value.rstrip('/')


def configured_file(variable,name):
    return Path(os.getenv(variable) or Path(__file__).with_name(name))


MAPPING = MappingSettings.model_validate(tomllib.loads(configured_file('FLOOD_LENS_MAPPING_CONFIG','mapping.toml').read_text()))
ACQUISITION = AcquisitionSettings.model_validate(tomllib.loads(configured_file('FLOOD_LENS_ACQUISITION_CONFIG','acquisition.toml').read_text()))


def satellite_provider(requested):
    """Resolve auto once per new analysis; never switch after a CDSE failure."""
    if requested!='auto':return requested
    client_id,secret=os.getenv('CDSE_CLIENT_ID'),os.getenv('CDSE_CLIENT_SECRET')
    if bool(client_id)!=bool(secret):raise ValueError('Configure both CDSE_CLIENT_ID and CDSE_CLIENT_SECRET, or explicitly choose public access')
    return 'copernicus' if client_id and secret else 'public'
