import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models import AnalysisRequest
from app.providers import search_catalog, select_pair
from app.presets import PRESETS
from app.settings import MAPPING

parser = argparse.ArgumentParser(description="Find a compatible Sentinel-1 pair without processing credentials")
parser.add_argument("--bbox")
parser.add_argument("--date")
parser.add_argument('--preset',choices=list(PRESETS))
parser.add_argument("--search-days", type=int, default=MAPPING.search_days)
parser.add_argument("--output")
args = parser.parse_args()
values={'case_id':args.preset or 'custom','search_days':args.search_days}
if args.bbox:values['bbox']=list(map(float,args.bbox.split(',')))
if args.date:values['flood_date']=args.date
if not args.preset and not (args.bbox and args.date):parser.error('Supply --bbox and --date or --preset')
params=AnalysisRequest.model_validate(values)
start = (params.flood_date - timedelta(days=params.search_days)).isoformat() + "T00:00:00Z"
end = (params.flood_date + timedelta(days=params.search_days)).isoformat() + "T23:59:59Z"
items = search_catalog(params.bbox,start,end)
pair = select_pair(items,params,allow_mosaics=True)
result = {"bbox":params.bbox,"flood_date":str(params.flood_date),"catalog_features":len(items),"radar_pair":[s.model_dump(mode="json") for s in pair],"status":"Catalog discovery only; authenticated image processing has not occurred."}
if args.output:
    Path(args.output).write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result,indent=2))
