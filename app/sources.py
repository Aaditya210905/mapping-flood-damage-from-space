"""Analysis inputs and independent references for the copilot route."""
from app.storage import ROOT

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
     "url": "https://api.ohsome.org/v1", "license": "ODbL"},
    {"id": "copernicus_dem", "name": "Copernicus DEM GLO-30", "role": "terrain correction and downhill trace", "category": "input",
     "description": "Elevation for radar geometry, slope screening and the optional downhill path.",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus DEM license and WorldDEM attribution"},
    {"id": "emsr927", "name": "Copernicus EMSR927", "role": "case-study evaluation only", "category": "evaluation_only",
     "url": "https://mapping.emergency.copernicus.eu/activations/EMSR927/",
     "license": "European Union, Copernicus Emergency Management Service data"},
]
