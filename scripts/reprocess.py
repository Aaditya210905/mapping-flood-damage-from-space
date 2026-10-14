"""Reprocess a saved real CDSE acquisition into a new immutable run directory."""
import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.reprocess import reprocess_saved_cdse
from app.storage import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_run_folder")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "runs",
                        help="Parent folder for a newly generated run UUID; existing runs are never changed")
    args = parser.parse_args()
    identifier = str(uuid.uuid4())
    destination = args.output_root / identifier
    try:
        result = reprocess_saved_cdse(args.source_run_folder, destination, identifier,
                                     lambda label, percent: print(f"{percent}% {label}", flush=True))
    except (OSError, ValueError) as error:
        parser.exit(2, f"Cannot reprocess cached CDSE inputs: {error}\n")
    print(json.dumps({"id": result["id"], "folder": str(destination), "mode": result["mode"],
                      "acquisition_mode": result["acquisition_mode"], "analysis_status": result["analysis_status"],
                      "class_availability": result["class_availability"], "stats": result["stats"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
