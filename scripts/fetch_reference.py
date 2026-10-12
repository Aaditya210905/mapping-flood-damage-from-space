"""Download official EMS vectors to an evaluation-only directory."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
import pyogrio
from shapely import from_wkt, make_valid
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from app.datasets import DATASETS, download, save_json, sha256

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--activation", default="EMSR927")
parser.add_argument("--output", type=Path, default=DATASETS / "evaluation" / "EMSR927")
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
activation_url = f"https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?code={args.activation}"
with httpx.Client(timeout=90, follow_redirects=True) as client:
    response = client.get(activation_url); response.raise_for_status()
    payload = response.json()
save_json(args.output / "activation.json", payload)
if payload.get("count") != 1 or payload["results"][0]["code"] != args.activation:
    raise ValueError("The official API did not return the requested activation.")
activation = payload["results"][0]
records, references, coverage = [], [], []
for aoi in activation["aois"]:
    products = [p for p in aoi["products"] if p.get("downloadPath") and p["version"]["statusCode"] == "F"]
    for product in products:
        url = product["downloadPath"] + "?type=vectors"
        zip_path = args.output / f"{product['id']}-vectors.zip"
        print(f"Downloading {args.activation} AOI {aoi['number']} product {product['id']} vectors (evaluation only)", flush=True)
        download(url, zip_path, limit=256 * 1024 * 1024)
        folder = args.output / str(product["id"])
        folder.mkdir(exist_ok=True)
        with ZipFile(zip_path) as archive:
            for member in archive.infolist():
                target = folder / member.filename
                if not target.resolve().is_relative_to(folder.resolve()) or member.file_size > 128 * 1024 * 1024:
                    raise ValueError("Unsafe or oversized member in the official vector archive.")
                if not member.is_dir():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
        layers = []
        not_analysed = []
        product_reference = []
        for path in sorted(folder.rglob("*.shp")):
            frame = pyogrio.read_dataframe(path).to_crs("EPSG:4326")
            layers.append({"file": str(path.relative_to(args.output)), "features": len(frame), "columns": list(frame.columns)})
            if "notanalysed" in path.stem.lower():
                not_analysed.extend(make_valid(g) for g in frame.geometry if g is not None and not g.is_empty)
            if "observedeventa" in path.stem.lower():
                for feature in json.loads(frame.to_json())["features"]:
                    feature["properties"].update({"activation": args.activation, "aoi": aoi["number"],
                                                   "product_id": product["id"], "source_layer": path.name})
                    product_reference.append(feature)
        records.append({"product_id": product["id"], "aoi": aoi["number"], "name": aoi["name"], "type": product["type"],
                        "monitoring": product["monitoring"], "download_url": url, "sha256": sha256(zip_path),
                        "version": product["version"], "images": product["images"], "layers": layers})
        # Keep the initial post-event product for the comparison, rather than
        # combining monitoring dates into an enlarged reference extent.
        if not product["monitoring"] and product_reference:
            references.extend(product_reference)
            reviewed = make_valid(from_wkt(product["extent"])).difference(unary_union(not_analysed))
            coverage.append({"type": "Feature", "geometry": mapping(reviewed), "properties": {
                "activation": args.activation, "aoi": aoi["number"], "product_id": product["id"],
                "method": "Official product extent minus official not-analysed polygons"}})
save_json(args.output / "reference-extent.geojson", {"type": "FeatureCollection", "features": references})
save_json(args.output / "reviewed-coverage.geojson", {"type": "FeatureCollection", "features": coverage})
save_json(args.output / "manifest.json", {"activation": args.activation, "source_url": activation_url,
    "role": "evaluation only; this directory is never read by acquisition or prediction", "products": records,
    "reference_classes": "All polygons in observedEventA; inspect class attributes before interpreting combined water/debris scores",
    "attribution": "European Union, Copernicus Emergency Management Service data"})
print(f"Saved {len(records)} official products; {len(references)} reference polygons. Source class/date review is recorded in the manifest.")
