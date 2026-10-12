import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models import AnalysisRequest
from app.providers import search_catalog, select_pair

parser = argparse.ArgumentParser(description="Find a compatible Sentinel-1 pair without processing credentials")
parser.add_argument("--bbox", default="85.19,27.90,85.41,28.30")
parser.add_argument("--date", default="2026-08-26")
parser.add_argument("--output")
args = parser.parse_args()
params = AnalysisRequest(bbox=list(map(float,args.bbox.split(","))), flood_date=args.date, mode="live")
start = (params.flood_date - timedelta(days=params.search_days)).isoformat() + "T00:00:00Z"
end = (params.flood_date + timedelta(days=params.search_days)).isoformat() + "T23:59:59Z"
items = search_catalog(params.bbox,start,end)
pair = select_pair(items,params)
result = {"bbox":params.bbox,"flood_date":str(params.flood_date),"catalog_features":len(items),"radar_pair":[s.model_dump(mode="json") for s in pair],"status":"Catalog discovery only; authenticated image processing has not occurred."}
if args.output:
    Path(args.output).write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result,indent=2))

