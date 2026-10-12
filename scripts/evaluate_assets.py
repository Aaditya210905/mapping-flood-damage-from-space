"""Check exposure against official asset grading, strictly after prediction."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import numpy as np
import pyogrio
import rasterio
from shapely.geometry import shape,box
from shapely.ops import unary_union
from shapely.strtree import STRtree
from app.datasets import DATASETS,save_json,sha256
from app.geo import Grid

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('run_folder',type=Path)
parser.add_argument('--reference',type=Path,default=DATASETS/'evaluation/EMSR927')
args=parser.parse_args()
folder=args.run_folder
before={p.name:sha256(p) for p in (folder/'result.json',folder/'change-mask.tif',folder/'extent.geojson')}
run=json.loads((folder/'result.json').read_text())
if run['mode']!='live':raise ValueError('Real official grading requires an actual-data run.')
with rasterio.open(folder/'change-mask.tif') as source:
    grid=Grid(str(source.crs),source.transform,source.width,source.height,abs(source.transform.a))
    observed=source.read(1)!=255
hazard=unary_union([grid.project(shape(f['geometry'])) for f in run['layers']['extent']['features']])
aoi=grid.project(box(*run['bbox']))
layers={k:run['layers'][k]['features'] for k in ('buildings','roads','bridges')}
geometries={k:[grid.project(shape(f['geometry'])) for f in fs] for k,fs in layers.items()}
trees={k:STRtree(gs) for k,gs in geometries.items() if gs}
def coverage(geometry):
    if geometry.geom_type=='LineString':
        points=[geometry.interpolate(x) for x in np.linspace(0,geometry.length,max(2,int(np.ceil(geometry.length/grid.resolution))+1))]
    else:points=[geometry.representative_point()]
    total=0
    for p in points:
        row=int(np.floor((grid.transform.f-p.y)/grid.resolution));col=int(np.floor((p.x-grid.transform.c)/grid.resolution))
        total+=bool(0<=row<grid.height and 0<=col<grid.width and observed[row,col])
    return total/len(points)
counts={};rows=[];seen=set()
manifest=json.loads((args.reference/'manifest.json').read_text())
for product in manifest['products']:
    if product['monitoring']:continue
    for layer in product['layers']:
        name=Path(layer['file']).name.lower()
        category='buildings' if 'builtupp' in name else 'roads' if 'transportationl' in name else 'bridges' if 'transportationp' in name else None
        if not category:continue
        frame=pyogrio.read_dataframe(args.reference/layer['file']).to_crs(4326)
        for _,row in frame.iterrows():
            if category=='bridges' and not any('bridge' in str(row.get(k,'')).lower() for k in ('info','obj_type','simplified')):
                continue
            if row.geometry is None or row.geometry.is_empty:
                counts.setdefault(category,Counter())["missing_reference_geometry"]+=1
                continue
            grade=str(row.get('damage_gra','Unknown'));g=grid.project(row.geometry)
            key=(category,round(g.centroid.x,1),round(g.centroid.y,1),grade)
            if key in seen:continue
            seen.add(key)
            if not g.intersects(aoi):continue
            record={'category':category,'aoi':product['aoi'],'product_id':product['product_id'],'official_grade':grade,'observed_fraction':round(coverage(g),3)}
            tally=counts.setdefault(category,Counter());tally['reference_assets_inside_aoi']+=1
            if record['observed_fraction'] < .8:
                record['status']='insufficient_observation';tally['insufficient_observation']+=1
            elif category not in trees:
                record['status']='no_historical_osm_match';tally['unmatched']+=1
            else:
                tree=trees[category]
                if category=='roads' and g.geom_type=='LineString' and g.length:
                    candidates=tree.query(g.buffer(30))
                    ranked=[(g.intersection(geometries[category][int(i)].buffer(30)).length/g.length,int(i)) for i in candidates]
                    overlap,index=max(ranked,default=(0,-1))
                    matched=overlap>=.5
                else:
                    index=int(tree.nearest(g));matched=g.distance(geometries[category][index])<=50
                if not matched:
                    record['status']='no_historical_osm_match';tally['unmatched']+=1
                else:
                    predicted=bool(g.intersection(hazard).length > .001) if category=='roads' else bool(layers[category][index]['properties']['exposed'])
                    record.update(osm_id=layers[category][index]['properties']['id'],predicted_exposure=predicted)
                    lower=grade.lower()
                    if lower=='no visible damage':truth=False
                    elif 'possibly' in lower or lower in ('unknown','not analysed','not applicable'):truth=None
                    elif 'destroyed' in lower or 'damaged' in lower:truth=True
                    else:truth=None
                    if truth is None:
                        record['status']='uncertain_official_grade';tally['uncertain_grade']+=1
                    else:
                        record['status']='compared';record['official_affected']=truth
                        tally['tp' if predicted and truth else 'fp' if predicted else 'fn' if truth else 'tn']+=1
            rows.append(record)
scores={}
for category,c in counts.items():
    tp,fp,fn,tn=[c[k] for k in ('tp','fp','fn','tn')]
    scores[category]={**dict(c),'compared_assets':tp+fp+fn+tn,'precision':tp/(tp+fp) if tp+fp else None,
        'recall':tp/(tp+fn) if tp+fn else None,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
result={'run_id':run['id'],'activation':manifest['activation'],'role':'evaluation only',
    'method':'Initial products only. Reference assets inside AOI; >=80% observed samples; pre-event OSM matching: 50m point distance / >=50% road length within 30m. Uncertain grades excluded from binary agreement.',
    'scores':scores,'assets':rows,'attribution':'European Union, Copernicus Emergency Management Service data',
    'limitations':'Exposure-versus-grading agreement is indirect: intersection does not establish structural damage, impassability or actual settlement cutoff. Matching and observation selection affect coverage; excluded assets are reported explicitly.',
    'prediction_unchanged':all(sha256(folder/name)==value for name,value in before.items())}
if not result['prediction_unchanged']:raise ValueError('Prediction changed during evaluation.')
save_json(folder/'asset-evaluation.json',result)
print(json.dumps({'run_id':run['id'],'scores':scores,'prediction_unchanged':result['prediction_unchanged']},indent=2))
