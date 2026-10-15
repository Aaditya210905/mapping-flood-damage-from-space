"""Shared, versioned mapping configuration; no credentials or case outputs."""
from pathlib import Path
import os
import re
import tomllib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DestinationProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    label_en: str = Field(min_length=1, max_length=80)
    label_ne: str = Field(min_length=1, max_length=80)
    keywords: list[str] = Field(min_length=1, max_length=30)
    tags: dict[str, list[str]] = Field(min_length=1, max_length=10)

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, value):
        if any(not re.fullmatch(r'[a-z][a-z0-9_:]*', key) or not values
               or len(values) != len(set(values))
               or any(not re.fullmatch(r'[a-zA-Z0-9_:.-]+', item) for item in values)
               for key, values in value.items()):
            raise ValueError('Destination selectors require OSM tag keys and unique literal values')
        return value

    @field_validator('keywords')
    @classmethod
    def valid_keywords(cls, value):
        if any(not word.strip() or len(word) > 80 for word in value):
            raise ValueError('Destination keywords must be nonempty bounded phrases')
        return value


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
    drivable_highways:list[str]=Field(min_length=1,max_length=100)
    radar_median_window_pixels: int = Field(ge=1, le=15)
    radar_median_minimum_support: int = Field(ge=1, le=225)
    road_on_network_tolerance_m: float = Field(ge=0, le=1000)
    settlement_places: list[str] = Field(min_length=1, max_length=30)
    destination_profiles: dict[str, DestinationProfile] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def coherent_policy(self):
        if self.radar_median_window_pixels % 2 != 1 or self.radar_median_minimum_support > self.radar_median_window_pixels ** 2:
            raise ValueError('Radar median window must be odd and support must fit the window')
        if self.road_on_network_tolerance_m > self.road_snap_distance_m:
            raise ValueError('On-network tolerance cannot exceed the road snap distance')
        if len(set(self.settlement_places)) != len(self.settlement_places) or any(not re.fullmatch(r'[a-z][a-z0-9_]*', place) for place in self.settlement_places):
            raise ValueError('Settlement places require unique OSM place values')
        if any(name == 'all' or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', name) for name in self.destination_profiles):
            raise ValueError('Destination profile identifiers must be unique lowercase names other than all')
        return self

    def asset_selection(self):
        return {'settlement_places': list(self.settlement_places),
                'destination_tags': {kind: profile.tags for kind, profile in self.destination_profiles.items()}}

    def osm_filter(self):
        selectors = {'place': set(self.settlement_places)}
        for profile in self.destination_profiles.values():
            for key, values in profile.tags.items():
                selectors.setdefault(key, set()).update(values)
        return 'building=* or highway=* or bridge=* or man_made=bridge or ' + ' or '.join(
            key + ' in (' + ','.join(sorted(values)) + ')' for key, values in sorted(selectors.items()))

    def destination_types(self, properties):
        return [kind for kind, profile in self.destination_profiles.items()
                if any(properties.get(key) in values for key, values in profile.tags.items())]

    @field_validator('drivable_highways')
    @classmethod
    def highway_classes(cls,value):
        if len(value)!=len(set(value)) or any(not re.fullmatch(r'[a-z][a-z0-9_]*',tag) for tag in value):
            raise ValueError('Driving profile requires unique OSM highway class names')
        return value

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
    public_stac_url:str
    public_sign_url:str
    public_dem_base_url:str
    public_geoid_url:str
    ems_activation_url:str
    public_catalog_page_size:int=Field(ge=1,le=1000)
    public_catalog_max_pages:int=Field(ge=1,le=100)
    public_catalog_timeout_seconds:float=Field(gt=0,le=300)
    public_sign_timeout_seconds:float=Field(gt=0,le=300)
    public_download_timeout_seconds:float=Field(gt=0,le=600)
    public_http_retries:int=Field(ge=0,le=5)
    public_max_retry_wait_seconds:float=Field(ge=0,le=60)
    public_gdal_timeout_seconds:int=Field(ge=1,le=600)
    public_gdal_cache_mb:int=Field(ge=1,le=1024)
    osm_timeout_seconds:float=Field(gt=0,le=600)
    osm_connect_timeout_seconds:float=Field(gt=0,le=120)
    osm_http_retries:int=Field(ge=0,le=5)
    osm_max_retry_wait_seconds:float=Field(ge=0,le=60)
    reference_timeout_seconds:float=Field(gt=0,le=600)
    optical_warning_age_days:int=Field(ge=1,le=120)
    catalog_page_size:int=Field(ge=1,le=1000)
    catalog_max_pages:int=Field(ge=1,le=100)
    catalog_timeout_seconds:float=Field(gt=0,le=300)
    process_timeout_seconds:float=Field(gt=0,le=600)
    http_retries:int=Field(ge=0,le=5)
    max_retry_wait_seconds:float=Field(ge=0,le=60)
    token_expiry_margin_seconds:float=Field(ge=0,le=300)
    token_timeout_seconds:float=Field(gt=0,le=300)
    reference_download_timeout_seconds:float=Field(gt=0,le=600)
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

    @field_validator('cdse_stac_url','cdse_process_url','cdse_token_url','osm_url','osm_v2_url',
                     'public_stac_url','public_sign_url','public_dem_base_url','public_geoid_url','ems_activation_url')
    @classmethod
    def endpoint(cls,value):
        parsed=urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1','::1')))):
            raise ValueError('Service endpoints require HTTPS or local HTTP without credentials')
        return value.rstrip('/')


class EvaluationSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    version:str
    observation_threshold:float=Field(gt=0,le=1)
    road_match_buffer_m:float=Field(gt=0,le=1000)
    road_match_min_overlap:float=Field(gt=0,le=1)
    asset_match_distance_m:float=Field(ge=0,le=1000)


class RuntimeSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    version:str
    analysis_workers:int=Field(ge=1,le=8)
    token_cache_entries:int=Field(ge=1,le=128)
    feature_alias_cache_entries:int=Field(ge=1,le=128)
    run_cache_entries:int=Field(ge=1,le=128)
    run_cache_max_mb:int=Field(ge=1,le=2048)
    provider_account_cache_entries:int=Field(ge=1,le=1024)


def configured_file(variable,name):
    return Path(os.getenv(variable) or Path(__file__).with_name(name))


MAPPING = MappingSettings.model_validate(tomllib.loads(configured_file('FLOOD_LENS_MAPPING_CONFIG','mapping.toml').read_text()))
ACQUISITION = AcquisitionSettings.model_validate(tomllib.loads(configured_file('FLOOD_LENS_ACQUISITION_CONFIG','acquisition.toml').read_text()))
EVALUATION = EvaluationSettings.model_validate(tomllib.loads(configured_file('FLOOD_LENS_EVALUATION_CONFIG','evaluation.toml').read_text()))
RUNTIME = RuntimeSettings.model_validate(tomllib.loads(configured_file('FLOOD_LENS_RUNTIME_CONFIG','runtime.toml').read_text()))

# Compatibility contract for already acquired v1-v4 OSM extracts, rather than
# current processing defaults. Never infer broader old coverage from new settings.
LEGACY_ASSET_SELECTION = {'settlement_places': ['village', 'hamlet', 'isolated_dwelling'],
                          'destination_tags': {'hospital': {'amenity': ['hospital']}, 'town': {'place': ['town', 'city']}}}
LEGACY_DESTINATION_PROFILES = {
    'hospital': DestinationProfile(label_en='Hospital', label_ne='अस्पताल',
        keywords=['hospital', 'hospitals', 'healthcare', 'अस्पताल'], tags=LEGACY_ASSET_SELECTION['destination_tags']['hospital']),
    'town': DestinationProfile(label_en='Town', label_ne='नगर',
        keywords=['town', 'towns', 'city', 'cities', 'नगर', 'सहर', 'शहर'], tags=LEGACY_ASSET_SELECTION['destination_tags']['town'])}


def asset_selection_covers(recorded, requested):
    def selectors(selection):
        pairs = {('place', value) for value in selection['settlement_places']}
        for tags in selection['destination_tags'].values():
            pairs.update((key, value) for key, values in tags.items() for value in values)
        return pairs
    return selectors(requested).issubset(selectors(recorded))


def satellite_provider(requested):
    """Resolve auto once per new analysis; never switch after a CDSE failure."""
    if requested!='auto':return requested
    client_id,secret=os.getenv('CDSE_CLIENT_ID'),os.getenv('CDSE_CLIENT_SECRET')
    if bool(client_id)!=bool(secret):raise ValueError('Configure both CDSE_CLIENT_ID and CDSE_CLIENT_SECRET, or explicitly choose public access')
    return 'copernicus' if client_id and secret else 'public'
