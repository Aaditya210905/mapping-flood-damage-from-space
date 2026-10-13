import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models import AnalysisRequest
from app.pipeline import run_analysis
from app.report import report_html, report_pdf

parser = argparse.ArgumentParser(description="Run the same analysis pipeline without the dashboard")
parser.add_argument("--bbox", default="85.19,27.90,85.41,28.30")
parser.add_argument("--date", default="2026-08-26")
parser.add_argument("--name", default="Trishuli river corridor")
parser.add_argument("--mode", choices=["demo", "live"], default="live")
parser.add_argument("--provider", choices=["public", "copernicus"], default="public")
parser.add_argument("--water-model", choices=["external", "threshold"], default="threshold")
parser.add_argument("--resolution", type=int, default=30)
parser.add_argument("--output", default=None)
args = parser.parse_args()
run_id = str(uuid.uuid4())
folder = Path(args.output or f"data/runs/{run_id}")
try:
    request = AnalysisRequest(bbox=list(map(float, args.bbox.split(","))), flood_date=args.date, name=args.name, mode=args.mode, resolution_m=args.resolution,
                              satellite_provider=args.provider, water_model=args.water_model)
    run = run_analysis(request, run_id, folder, lambda message, percent: print(f"{percent:3}% {message}", flush=True))
except ValueError as error:
    parser.exit(2, f"Analysis could not start or complete: {error}\n")
for language in ("en", "ne"):
    (folder / f"report-{language}.html").write_text(report_html(run, language), encoding="utf-8")
(folder / "report-en.pdf").write_bytes(report_pdf(run))
print(json.dumps({"folder": str(folder.resolve()), "stats": run["stats"]}, indent=2))
