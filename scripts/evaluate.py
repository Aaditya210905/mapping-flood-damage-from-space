"""Compare a completed run with evaluation-only reference geometries."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.evaluation import evaluate_run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("run_folder")
parser.add_argument("reference_geojson")
parser.add_argument("--coverage", required=True, help="WGS84 polygons describing the officially reviewed reference area")
parser.add_argument("--activation", default="EMSR927")
parser.add_argument("--allow-demo", action="store_true", help="Explicitly label the output as a synthetic sanity check")
parser.add_argument("--output", default="evaluation.json")
args = parser.parse_args()
try:
    result = evaluate_run(args.run_folder, args.reference_geojson, args.coverage, args.activation, args.allow_demo)
except ValueError as error:
    parser.exit(2, f"{error}\n")
Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
