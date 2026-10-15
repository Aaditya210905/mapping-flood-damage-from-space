"""Analysis inputs and independent references for the copilot route."""
from app.storage import ROOT
from app.settings import ACQUISITION
import re

REFERENCE_ROOT = ROOT / "data" / "reference_only"
MAPPING_METHOD = {
    "algorithm": "Rule-based radar and optical change detection",
    "type": "rule_based",
    "requires_model_weights": False,
    "requires_training_datasets": False,
}
SOURCES = [
    {"id": "sentinel1", "name": "Sentinel-1 radar", "role": "before/after water detection", "category": "input",
     "description": "Same-track VV/VH radar observations, calibrated and terrain-corrected using Copernicus elevation.",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus free, full and open data policy"},
    {"id": "sentinel2", "name": "Sentinel-2 optical", "role": "clear-sky water and debris support", "category": "input",
     "description": "L2A green, red, near-infrared, SWIR and scene classifications; used when a suitable pair is available.",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus free, full and open data policy"},
    {"id": "osm", "name": "Historical OpenStreetMap", "role": "infrastructure and road access", "category": "input",
     "description": "Buildings, roads, bridges, settlements and configured destinations from a verified pre-event snapshot.",
     "url": ACQUISITION.osm_url, "license": "ODbL"},
    {"id": "copernicus_dem", "name": "Copernicus DEM GLO-30", "role": "terrain correction and downhill trace", "category": "input",
     "description": "Elevation for radar geometry, slope screening and the optional downhill path.",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus DEM license and WorldDEM attribution"},
    {"id": "ems", "name": "Copernicus Emergency Management Service", "role": "independent evaluation only; explicit activation required", "category": "evaluation_only",
     "url": ACQUISITION.ems_public_base_url,
     "license": "European Union, Copernicus Emergency Management Service data"},
]


def evaluation_reference_url(activation):
    code = str(activation or '').upper()
    return f'{ACQUISITION.ems_public_base_url}/{code}/' if re.fullmatch(r'EMSR[0-9]{3,}', code) else None
