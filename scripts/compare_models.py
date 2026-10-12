"""Compare frozen v1 and v2 on identical held-out chips and eligibility pixels."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import joblib
import numpy as np
from collections import Counter
from app.datasets import DATASETS, ROOT, load_radar_chip, save_json
from app.segmentation import pixel_features
from app.evaluation import binary_metrics
models=[]
for name in ('water-baseline-v1','water-v2'):
    p=ROOT/'data/models'/name
    models.append((name,joblib.load(p/'model.joblib'),json.loads((p/'model-card.json').read_text())))
counts={}
processed=0
for dataset in ('kuro_siwo','sen1floods11'):
    folder=DATASETS/'training'/dataset
    records=json.loads((folder/'manifest.json').read_text())['records']
    for record in records:
        if record['split']!='test':continue
        chip=load_radar_chip(record,folder)
        f1,u1=pixel_features(chip['vv'],chip['observed'])
        f2,u2=pixel_features(chip['vv'],chip['observed'],chip['vh'])
        valid=u1 & u2 & chip['label_valid']
        positions=np.flatnonzero(valid)
        for (name,model,card),features in zip(models,(f1,f2)):
            prediction=np.zeros(valid.shape,dtype=bool)
            flat=features.reshape(-1,features.shape[-1])
            for start in range(0,len(positions),65536):
                selection=positions[start:start+65536]
                prediction.ravel()[selection]=model.predict_proba(flat[selection])[:,1] >= card['threshold']
            metrics=binary_metrics(prediction,chip['water'],valid)
            key=name+'/'+dataset
            counts.setdefault(key,Counter()).update({k:v for k,v in metrics.items() if k.endswith('_pixels')})
        processed+=1
        if processed%25==0:print(f'Fair comparison: {processed} identical test chips',flush=True)
scores={}
for key,c in counts.items():
    tp,fp,fn=[c[k] for k in ('true_positive_pixels','false_positive_pixels','false_negative_pixels')]
    scores[key]={**dict(c),'iou':tp/(tp+fp+fn) if tp+fp+fn else 1.,'recall':tp/(tp+fn) if tp+fn else None,
                 'precision':tp/(tp+fp) if tp+fp else None,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.}
result={'protocol':'Identical publisher test chips and identical observed/label-valid pixels. Frozen models; no test-driven threshold tuning.',
        'evaluated_chips':processed,'scores':scores,'caveat':'V2 excludes India from training; v1 did not. Protocol changes are explicit, not a controlled architecture-only ablation.'}
save_json(ROOT/'docs/model-comparison.json',result)
print(json.dumps(result,indent=2),flush=True)
