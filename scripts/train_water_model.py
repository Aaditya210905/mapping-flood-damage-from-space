"""Train dual-polarization water inference; select thresholds on validation only."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from threadpoolctl import threadpool_limits
from app.datasets import DATASETS, TRAINING_CITATIONS, load_radar_chip, save_json, sha256, validate_training_splits
from app.evaluation import binary_metrics
from app.segmentation import FEATURES, MODEL_FOLDER, pixel_features

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data', type=Path, default=DATASETS)
parser.add_argument('--output', type=Path, default=MODEL_FOLDER)
parser.add_argument('--pixels-per-chip', type=int, default=2048)
parser.add_argument('--trees', type=int, default=96)
args = parser.parse_args()
if args.pixels_per_chip < 100 or args.trees < 8:
    parser.error('Use at least 100 pixels per chip and 8 trees.')
records, manifest_hashes = [], {}
for dataset in ('kuro_siwo', 'sen1floods11'):
    folder = args.data / 'training' / dataset
    path = folder / 'manifest.json'
    manifest_hashes[dataset] = sha256(path)
    records.extend((record, folder) for record in json.loads(path.read_text())['records'])
validate_training_splits([r for r, _ in records])
# These choices are fixed before looking at the new held-out test scores.
# India is excluded from every training/calibration split for regional testing.
# A publisher-training Kuro event supplies separate validation, never test data.
validation_event = '1111009'
if not any(r['dataset'] == 'kuro_siwo' and r['event'] == validation_event and r['split'] == 'train' for r, _ in records):
    raise ValueError('The prescribed Kuro Siwo validation event is missing.')
def role(record):
    if record['dataset'] == 'sen1floods11' and record['event'] == 'India':
        return 'regional_test'
    if record['dataset'] == 'kuro_siwo' and record['split'] == 'train' and record['event'] == validation_event:
        return 'valid'
    return record['split']

def features_for(record, folder):
    for key, filename in record['files'].items():
        if sha256(folder / filename) != record['sha256'][key]:
            raise ValueError('Input checksum changed: ' + record['id'])
    chip = load_radar_chip(record, folder)
    features, observed = pixel_features(chip['vv'], chip['observed'], chip['vh'])
    return features, chip['water'], observed & chip['label_valid']

rng = np.random.default_rng(927)
x, y, provenance = [], [], []
for index, (record, folder) in enumerate(records):
    if role(record) != 'train':
        continue
    features, water, usable = features_for(record, folder)
    chosen = []
    for is_water in (False, True):
        positions = np.flatnonzero(usable & (water == is_water))
        if len(positions):
            chosen.extend(rng.choice(positions, min(len(positions), args.pixels_per_chip // 2), replace=False))
    chosen = np.asarray(chosen, dtype=int)
    if not len(chosen):
        continue
    x.append(features.reshape(-1, len(FEATURES))[chosen])
    y.append(water.ravel()[chosen].astype('uint8'))
    provenance.append({'dataset': record['dataset'], 'id': record['id'], 'event': record['event'], 'pixels': len(chosen), 'water_pixels': int(y[-1].sum())})
    if len(provenance) % 50 == 0:
        print(f'Prepared {len(provenance)} training chips', flush=True)
if {p['dataset'] for p in provenance} != {'kuro_siwo', 'sen1floods11'}:
    raise ValueError('Both listed datasets must supply actual training pixels.')
x, y = np.concatenate(x), np.concatenate(y)
if len(np.unique(y)) != 2:
    raise ValueError('Observed water and land labels are required.')
print(f'Training {args.trees} trees on {len(x):,} balanced-sampled pixels / {len(provenance)} chips', flush=True)
model = ExtraTreesClassifier(n_estimators=args.trees, max_depth=20, min_samples_leaf=8,
    class_weight='balanced', n_jobs=4, random_state=927)
with threadpool_limits(limits=4):
    model.fit(x, y)
del x, y

def probabilities(features, usable):
    output = np.zeros(usable.shape, dtype='float32')
    positions = np.flatnonzero(usable)
    flat = features.reshape(-1, len(FEATURES))
    for start in range(0, len(positions), 65536):
        selection = positions[start:start+65536]
        output.ravel()[selection] = model.predict_proba(flat[selection])[:, 1]
    return output

histograms = {}
validation_records = []
for record, folder in records:
    if role(record) != 'valid':
        continue
    features, water, usable = features_for(record, folder)
    probability = probabilities(features, usable)
    bins = np.clip(np.floor(probability[usable]*1000).astype(int), 0, 1000)
    hist = histograms.setdefault(record['dataset'], np.zeros((2, 1001), dtype='int64'))
    for label in (0, 1):
        hist[label] += np.bincount(bins[water[usable] == bool(label)], minlength=1001)
    validation_records.append({'dataset':record['dataset'], 'id':record['id'], 'event':record['event']})
    if len(validation_records) % 25 == 0:
        print(f'Calibration evaluated {len(validation_records)} validation chips', flush=True)
if set(histograms) != {'kuro_siwo', 'sen1floods11'}:
    raise ValueError('Threshold selection needs validation observations from both datasets.')
def histogram_score(hist, index):
    tp, fp = [int(hist[label, index:].sum()) for label in (1, 0)]
    fn, tn = [int(hist[label, :index].sum()) for label in (1, 0)]
    return {'true_positive_pixels':tp, 'false_positive_pixels':fp, 'false_negative_pixels':fn, 'true_negative_pixels':tn,
        'iou':tp/(tp+fp+fn) if tp+fp+fn else 1., 'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.,
        'precision':tp/(tp+fp) if tp+fp else None, 'recall':tp/(tp+fn) if tp+fn else None}
curve = []
for index in range(200, 851, 10):
    scores = {name:histogram_score(hist, index) for name, hist in histograms.items()}
    curve.append({'threshold':index/1000, 'macro_iou':float(np.mean([s['iou'] for s in scores.values()])), 'scores':scores})
selected = max(curve, key=lambda row:(row['macro_iou'], row['threshold']))
threshold = selected['threshold']
print(f'Validation-selected threshold: {threshold}; macro IoU {selected["macro_iou"]:.3f}', flush=True)
scores = {f'{name}/valid':value for name, value in selected['scores'].items()}
counts, evaluated = {}, []
for record, folder in records:
    if role(record) not in ('test', 'regional_test'):
        continue
    features, water, usable = features_for(record, folder)
    probability = probabilities(features, usable)
    metrics = binary_metrics(probability >= threshold, water, usable)
    key = f'{record["dataset"]}/{role(record)}'
    counts.setdefault(key, Counter()).update({k:v for k,v in metrics.items() if k.endswith('_pixels')})
    evaluated.append({'dataset':record['dataset'], 'split':record['split'], 'role':role(record), 'id':record['id'], 'event':record['event'], 'metrics':metrics})
    if len(evaluated) % 25 == 0:
        print(f'Held-out evaluation: {len(evaluated)} chips', flush=True)
for key, count in counts.items():
    tp, fp, fn = [count[k] for k in ('true_positive_pixels','false_positive_pixels','false_negative_pixels')]
    scores[key] = {**dict(count), 'iou':tp/(tp+fp+fn) if tp+fp+fn else 1., 'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.,
        'precision':tp/(tp+fp) if tp+fp else None, 'recall':tp/(tp+fn) if tp+fn else None}
args.output.mkdir(parents=True, exist_ok=True)
artifact = args.output / 'model.joblib'
joblib.dump(model, artifact.with_suffix('.tmp'), compress=3)
artifact.with_suffix('.tmp').replace(artifact)
card = {'status':'trained', 'version':2, 'created_at':datetime.now(timezone.utc).isoformat(),
    'algorithm':'Dual-polarization balanced ExtraTrees water segmentation', 'training_datasets':['kuro_siwo','sen1floods11'],
    'features':FEATURES, 'threshold':threshold, 'threshold_selection':'Highest equal-dataset macro water IoU on validation only; test labels never select parameters',
    'training_pixels':sum(p['pixels'] for p in provenance), 'training_chip_count':len(provenance), 'training_inputs':provenance,
    'validation_chip_count':len(validation_records), 'evaluation_chip_count':len(evaluated), 'publisher_manifest_sha256':manifest_hashes,
    'protocol':{'kuro_validation_event':validation_event, 'sen_regional_holdout_event':'India', 'test_used_for_selection':False},
    'artifact_sha256':sha256(artifact), 'scores':scores, 'citations':TRAINING_CITATIONS,
    'label_mapping':{'kuro_siwo':'0 land; 1 permanent and 2 flood water; 3 ignored', 'sen1floods11':'0 land; 1 water; -1 ignored'},
    'limitations':['Expanded Kuro Siwo shard-prefix subset, not the complete Kuro Siwo benchmark.',
       'All publisher Sen1Floods11 split chips downloaded; India excluded from training and calibration for regional testing.',
       'Binary water model. Debris remains a separate optical change candidate, not trained landslide damage classification.',
       'Training sigma0 and live gamma0, resolution and terrain differ; independent Himalayan generalization is not established by chip scores.',
       'EMSR927 and all published damage maps are excluded from training, calibration and inference.']}
save_json(args.output/'model-card.json', card)
save_json(args.output/'threshold-selection.json', {'selected':selected,'curve':curve,'validation_inputs':validation_records})
save_json(args.output/'held-out-results.json', evaluated)
print(json.dumps(scores,indent=2),flush=True)
