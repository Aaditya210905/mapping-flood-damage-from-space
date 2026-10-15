import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models import AnalysisRequest
from app.pipeline import run_analysis
from app.report import report_html, report_pdf
from app.presets import TRISHULI
from app.settings import MAPPING

parser = argparse.ArgumentParser(description="Run the same analysis pipeline without the dashboard")
parser.add_argument("--bbox", default=','.join(map(str, TRISHULI['bbox'])))
parser.add_argument("--date", default=TRISHULI['flood_date'])
parser.add_argument("--name")
parser.add_argument("--case-id", choices=["trishuli", "custom"])
parser.add_argument("--mode", choices=["demo", "live"], default="live")
parser.add_argument("--provider", choices=["public", "copernicus"], default="public")
parser.add_argument("--resolution", type=int, default=MAPPING.resolution_m)
parser.add_argument("--search-days", type=int, default=MAPPING.search_days)
parser.add_argument("--water-threshold", type=float, default=MAPPING.water_threshold_db)
parser.add_argument("--change-threshold", type=float, default=MAPPING.change_threshold_db)
parser.add_argument("--max-slope", type=float, default=MAPPING.max_slope_deg)
parser.add_argument("--output", default=None)
args = parser.parse_args()
run_id = str(uuid.uuid4())
folder = Path(args.output or f"data/runs/{run_id}")
try:
    bbox = list(map(float, args.bbox.split(",")))
    preset = bbox == TRISHULI['bbox'] and args.date == TRISHULI['flood_date']
    request = AnalysisRequest(bbox=bbox, flood_date=args.date, name=args.name or (TRISHULI['name'] if preset else 'Custom flood analysis'),
                              case_id=args.case_id or ('trishuli' if preset else 'custom'), mode=args.mode, resolution_m=args.resolution,
                              satellite_provider=args.provider, search_days=args.search_days, water_threshold_db=args.water_threshold,
                              change_threshold_db=args.change_threshold, max_slope_deg=args.max_slope)
    run = run_analysis(request, run_id, folder, lambda message, percent: print(f"{percent:3}% {message}", flush=True))
except ValueError as error:
    parser.exit(2, f"Analysis could not start or complete: {error}\n")
for language in ("en", "ne"):
    (folder / f"report-{language}.html").write_text(report_html(run, language), encoding="utf-8")
(folder / "report-en.pdf").write_bytes(report_pdf(run))
print(json.dumps({"folder": str(folder.resolve()), "stats": run["stats"]}, indent=2))
