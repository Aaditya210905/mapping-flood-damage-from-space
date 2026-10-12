"""Reproduce training and an uncached actual run in an isolated Git checkout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.datasets import ROOT,save_json
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--checkout',type=Path,required=True,help='A fresh checkout without a data directory')
args=parser.parse_args()
checkout=args.checkout.resolve()
if (checkout/'data').exists():raise ValueError('Use a fresh checkout; no project data/cache may be inherited.')
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=checkout,text=True).strip()
remote=subprocess.check_output(['git','ls-remote','origin','refs/heads/main'],cwd=checkout,text=True).split()[0]
if remote!=commit:raise ValueError('The isolated checkout is not the current published commit.')
original_environment=os.environ.copy()
for key in ('CDSE_CLIENT_ID','CDSE_CLIENT_SECRET','OHSOME_API_KEY','FLOOD_LENS_DATA','FLOOD_LENS_MODEL'):
    original_environment.pop(key,None)
output=ROOT/'docs/qa';output.mkdir(exist_ok=True)
steps=[['uv','sync','--frozen','--python','3.12'],['.venv/bin/python','scripts/download_datasets.py'],
       ['.venv/bin/python','scripts/train_water_model.py'],
       ['.venv/bin/python','scripts/analyze.py','--name','Fresh checkout Dadu August 2022','--bbox','67.70,26.65,67.80,26.75','--date','2022-08-28'],
       ['.venv/bin/python','-m','pytest','-q']]
record={'commit':commit,'published_commit_verified':True,'initial_project_data_present':False,'credentials_removed':True,'steps':[]}
with (output/'fresh-checkout.log').open('w') as log:
    for command in steps:
        started=time.monotonic();print('Fresh checkout step:', ' '.join(command),flush=True)
        result=subprocess.run(command,cwd=checkout,env=original_environment,stdout=log,stderr=subprocess.STDOUT)
        record['steps'].append({'command':command,'exit_code':result.returncode,'elapsed_seconds':round(time.monotonic()-started,2)})
        log.flush()
        if result.returncode:
            save_json(ROOT/'docs/fresh-checkout-verification.json',record)
            raise SystemExit(result.returncode)
runs=[json.loads(p.read_text()) for p in (checkout/'data/runs').glob('*/result.json')]
run=max(runs,key=lambda r:r['created_at'])
card=json.loads((checkout/'data/models/water-baseline/model-card.json').read_text())
record.update(status='passed',run_id=run['id'],stats=run['stats'],radar_scenes=run['provenance']['radar_scenes'],
    optical_scenes=run['provenance']['optical_scenes'],osm_snapshot=run['provenance']['osm_snapshot'],
    training_chips=card['training_chip_count'],training_pixels=card['training_pixels'],model_threshold=card['threshold'],
    model_artifact_sha256=card['artifact_sha256'],limitations='Isolated source/data reproduction proves this actual case; it does not establish universal judge-date coverage or field accuracy.')
save_json(ROOT/'docs/fresh-checkout-verification.json',record)
print(json.dumps({'status':record['status'],'run_id':run['id'],'steps':record['steps']},indent=2),flush=True)
