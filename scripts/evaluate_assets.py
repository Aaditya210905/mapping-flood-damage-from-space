"""Check exposure against explicitly supplied official asset grading."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.asset_evaluation import evaluate_assets
from app.settings import EVALUATION,EvaluationSettings
from app.storage import save_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_folder',type=Path)
    parser.add_argument('--reference',type=Path,required=True,help='Explicit directory containing the reviewed reference manifest/vectors')
    parser.add_argument('--observation-threshold',type=float)
    parser.add_argument('--road-match-buffer-m',type=float)
    parser.add_argument('--road-match-min-overlap',type=float)
    parser.add_argument('--asset-match-distance-m',type=float)
    args=parser.parse_args()
    overrides={name:getattr(args,name) for name in ('observation_threshold','road_match_buffer_m','road_match_min_overlap','asset_match_distance_m') if getattr(args,name) is not None}
    try:
        policy=EvaluationSettings.model_validate({**EVALUATION.model_dump(),**overrides})
        result=evaluate_assets(args.run_folder,args.reference,policy)
    except (ValueError,OSError,KeyError) as error:parser.exit(2,f'Asset evaluation failed: {error}\n')
    save_json(args.run_folder/'asset-evaluation.json',result)
    print(json.dumps({'run_id':result['run_id'],'configuration':result['configuration'],
        'scores':result['scores'],'prediction_unchanged':result['prediction_unchanged']},indent=2))


if __name__=='__main__':main()
