"""Source links and attribution only. There are no training-data loaders here."""
from app.storage import ROOT

REFERENCE_FOLDER = ROOT / "data" / "reference_only" / "EMSR927"
SOURCES = [
    {"id": "sentinel1", "name": "Sentinel-1 radar", "role": "before/after water detection", "category": "input",
     "description": "Same-track VV/VH radar observations, calibrated and terrain-corrected locally.",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus free, full and open data policy"},
    {"id": "sentinel2", "name": "Sentinel-2 optical", "role": "clear-sky water and debris support", "category": "input",
     "description": "L2A green, red, near-infrared, SWIR and scene classifications; used when a suitable pair is available.",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus free, full and open data policy"},
    {"id": "osm", "name": "Historical OpenStreetMap", "role": "infrastructure and road access", "category": "input",
     "description": "Buildings, roads, bridges, settlements and hospitals from a verified pre-event snapshot.",
     "url": "https://api.ohsome.org/v1", "license": "ODbL"},
    {"id": "copernicus_dem", "name": "Copernicus DEM GLO-30", "role": "terrain correction and downhill trace", "category": "input",
     "description": "Elevation for radar geometry, slope screening and the optional downhill path.",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus DEM license and WorldDEM attribution"},
    {"id": "kuro_siwo", "name": "Kuro Siwo", "role": "optional training outside this application", "category": "training_reference",
     "url": "https://github.com/Orion-AI-Lab/KuroSiwo", "license": "Author dataset CC BY 4.0; repository code MIT",
     "citation": "Bountos et al. (2024). Kuro Siwo: 33 billion m^2 under the water. NeurIPS 37, 38105-38121."},
    {"id": "sen1floods11", "name": "Sen1Floods11", "role": "optional training outside this application", "category": "training_reference",
     "url": "https://github.com/cloudtostreet/Sen1Floods11", "license": "Retain original attribution; original repository has no dataset license file",
     "citation": "Bonafilia, Tellman, Anderson and Issenberg (2020). Sen1Floods11. CVPR Workshops, 210-211."},
    {"id": "emsr927", "name": "Copernicus EMSR927", "role": "case-study evaluation only", "category": "evaluation_only",
     "url": "https://mapping.emergency.copernicus.eu/activations/EMSR927/",
     "license": "European Union, Copernicus Emergency Management Service data"},
]
