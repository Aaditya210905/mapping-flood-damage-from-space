"""Independent asset comparison with explicit, recorded matching policy."""
from collections import Counter
import json
from pathlib import Path

import pyogrio
import rasterio
from shapely.geometry import box,shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from app.evaluation import joint_hazard_observations
from app.geo import Grid,observation_fraction
from app.settings import EVALUATION,EvaluationSettings
from app.storage import sha256


def match_asset(geometry,category,geometries,tree,policy):
    if category=='roads' and geometry.geom_type in ('LineString','MultiLineString') and geometry.length:
        candidates=tree.query(geometry.buffer(policy.road_match_buffer_m))
        ranked=[(geometry.intersection(geometries[int(i)].buffer(policy.road_match_buffer_m)).length/geometry.length,int(i)) for i in candidates]
        overlap,index=max(ranked,default=(0,-1))
        return index if overlap>=policy.road_match_min_overlap else None
    index=tree.nearest(geometry)
    if index is None:return None
    index=int(index)
    return index if geometry.distance(geometries[index])<=policy.asset_match_distance_m else None


def evaluate_assets(run_folder,reference_folder,policy=EVALUATION):
    policy=EvaluationSettings.model_validate(policy)
    folder,reference=Path(run_folder),Path(reference_folder).resolve()
    before={name:sha256(folder/name) for name in ('result.json','change-mask.tif','extent.geojson')}
    run=json.loads((folder/'result.json').read_text())
    if run['mode']!='live':raise ValueError('Real official grading requires an actual-data run')
    with rasterio.open(folder/'change-mask.tif') as source:
        grid=Grid(str(source.crs),source.transform,source.width,source.height,abs(source.transform.a))
        observed=source.read(1)!=255
    observed=joint_hazard_observations(folder,grid,observed)
    hazard=unary_union([grid.project(shape(f['geometry'])) for f in run['layers']['extent']['features']])
    aoi=grid.project(box(*run['bbox']))
    layers={key:run['layers'][key]['features'] for key in ('buildings','roads','bridges')}
    geometries={key:[grid.project(shape(f['geometry'])) for f in values] for key,values in layers.items()}
    trees={key:STRtree(values) for key,values in geometries.items() if values}
    counts,rows,seen={},[],set()
    manifest_path=reference/'manifest.json'
    manifest=json.loads(manifest_path.read_text())
    reference_hashes={'manifest.json':sha256(manifest_path)}
    for product in manifest['products']:
        if product['monitoring']:continue
        for layer in product['layers']:
            name=Path(layer['file']).name.lower()
            category='buildings' if 'builtupp' in name else 'roads' if 'transportationl' in name else 'bridges' if 'transportationp' in name else None
            if not category:continue
            source_path=(reference/layer['file']).resolve()
            if not source_path.is_relative_to(reference):raise ValueError('Reference vector path escapes the selected reference directory')
            # Include shapefile sidecars, which carry grades, CRS and indexing.
            for path in source_path.parent.glob(source_path.stem+'.*'):
                if path.is_file():reference_hashes[str(path.relative_to(reference))]=sha256(path)
            frame=pyogrio.read_dataframe(source_path).to_crs(4326)
            for _,row in frame.iterrows():
                if category=='bridges' and not any('bridge' in str(row.get(key,'')).lower() for key in ('info','obj_type','simplified')):continue
                if row.geometry is None or row.geometry.is_empty:
                    counts.setdefault(category,Counter())['missing_reference_geometry']+=1
                    continue
                grade=str(row.get('damage_gra','Unknown'))
                geometry=grid.project(row.geometry)
                key=(category,round(geometry.centroid.x,1),round(geometry.centroid.y,1),grade)
                if key in seen:continue
                seen.add(key)
                if not geometry.intersects(aoi):continue
                fraction=observation_fraction(geometry,observed,grid)
                record={'category':category,'aoi':product['aoi'],'product_id':product['product_id'],
                    'official_grade':grade,'observed_fraction':fraction}
                tally=counts.setdefault(category,Counter())
                tally['reference_assets_inside_aoi']+=1
                if fraction<policy.observation_threshold:
                    record['status']='insufficient_observation';tally['insufficient_observation']+=1
                else:
                    index=match_asset(geometry,category,geometries[category],trees[category],policy) if category in trees else None
                    if index is None:
                        record['status']='no_historical_osm_match';tally['unmatched']+=1
                    else:
                        predicted=bool(geometry.intersection(hazard).length>.001) if category=='roads' else bool(layers[category][index]['properties']['exposed'])
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
    for category,counts_for_category in counts.items():
        tp,fp,fn,tn=[counts_for_category[key] for key in ('tp','fp','fn','tn')]
        scores[category]={**dict(counts_for_category),'compared_assets':tp+fp+fn+tn,'status':'available' if tp+fp+fn else 'unavailable',
            'reason':None if tp+fp+fn else 'No positive, jointly observed, matched reference/prediction asset comparisons.',
            'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
            'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
    result={'run_id':run['id'],'activation':manifest['activation'],'role':'evaluation only',
        'status':'available' if any(score['status']=='available' for score in scores.values()) else 'unavailable',
        'configuration':policy.model_dump(),'input_sha256':before,'reference_sha256':reference_hashes,
        'method':f'Initial products only. Reference assets inside AOI; >={policy.observation_threshold:.1%} footprint area or length-weighted samples jointly eligible for water AND debris. Missing class observations cannot establish negative exposure. Pre-event OSM matching: {policy.asset_match_distance_m:g}m point distance / >={policy.road_match_min_overlap:.1%} road length within {policy.road_match_buffer_m:g}m. Uncertain grades excluded from binary agreement.',
        'scores':scores,'assets':rows,'attribution':manifest.get('attribution'),
        'limitations':'Exposure-versus-grading agreement is indirect: intersection does not establish structural damage, impassability or actual settlement cutoff. Matching and observation selection affect coverage; excluded assets are reported explicitly.',
        'prediction_unchanged':all(sha256(folder/name)==value for name,value in before.items())}
    if not result['prediction_unchanged']:raise ValueError('Prediction changed during evaluation')
    return result
