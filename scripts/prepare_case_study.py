"""Package saved predictions and independent comparison metadata without acquisition."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.storage import save_json,sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_folder',type=Path)
    parser.add_argument('--fresh-run',type=Path)
    parser.add_argument('--output',type=Path,default=Path('output/case-study'))
    args=parser.parse_args()
    source=args.run_folder/'result.json'
    run=json.loads(source.read_text())
    if run.get('mode')!='live':parser.error('The submission case study requires actual acquired inputs')
    prediction_before=sha256(source)
    args.output.mkdir(parents=True,exist_ok=True)
    comparisons={}
    for kind,name in [('extent','evaluation.json'),('assets','asset-evaluation.json')]:
        path=args.run_folder/name
        value=json.loads(path.read_text())
        if value.get('run_id')!=run['id']:parser.error('A comparison belongs to a different analysis')
        target=args.output/f'emsr927-{kind}-evaluation.json'
        save_json(target,value)
        comparisons[kind]={'status':value.get('status'),'file':target.name,'sha256':sha256(target)}
    def summary(record,path):
        provenance=record['provenance']
        return {'run_id':record['id'],'source_sha256':sha256(path),'mode':record['mode'],
                'acquisition_mode':record.get('acquisition_mode'),'analysis_status':record['analysis_status'],
                'bbox':record['bbox'],'flood_date':record['flood_date'],'parameters':record['parameters'],
                'statistics':record['stats'],'class_availability':record['class_availability'],
                'radar_scenes':provenance['radar_scenes'],'osm_snapshot':provenance['osm_snapshot'],
                'mapping_configuration':provenance.get('mapping_configuration'),
                'acquisition_provider':provenance.get('acquisition',{}).get('provider'),
                'attributions':record['attributions']}
    package=summary(run,source)
    package.update(generated_at=datetime.now(timezone.utc).isoformat(),comparisons=comparisons,
                   limitations='Evaluation only; unavailable matching observations are not negative impact or accuracy. No field verification or native Nepali sign-off.')
    if args.fresh_run:
        fresh_source=args.fresh_run/'result.json'
        fresh=json.loads(fresh_source.read_text())
        if fresh.get('mode')!='live' or fresh.get('acquisition_mode')!='fresh_acquisition':
            parser.error('--fresh-run must be an actual new acquisition, not replay or synthetic input')
        save_json(args.output/'fresh-run-summary.json',summary(fresh,fresh_source))
        package['fresh_verification']={'file':'fresh-run-summary.json','sha256':sha256(args.output/'fresh-run-summary.json')}
    if sha256(source)!=prediction_before:raise ValueError('Packaging must not change prediction evidence')
    save_json(args.output/'trishuli-comparison.json',package)
    print(json.dumps({'output':str(args.output.resolve()),'run_id':run['id'],'comparisons':comparisons,'prediction_unchanged':True},indent=2))


if __name__=='__main__':main()
