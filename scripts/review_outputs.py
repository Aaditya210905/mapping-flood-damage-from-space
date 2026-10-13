"""Internal bilingual fact checks and independent geometry checks of exported routes."""
import argparse
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import numpy as np
import rasterio
from shapely.geometry import shape
from shapely.ops import unary_union
from app.copilot import answer,TRAINING
from app.storage import save_json
from app.geo import Grid

QUESTIONS=[('en','How large is the newly detected water area?','extent'),('ne','नयाँ डुबानको क्षेत्रफल कति छ?','extent'),
 ('en','Which bridges and roads overlap the mapped changes?','damage'),('ne','कति पुल र सडक प्रभावित भएका छन्?','damage'),
 ('en','Which villages cannot reach any hospital by road?','access'),('ne','सडक बन्द भएको मान्दा कुन बस्तीबाट अस्पताल पुग्न सकिँदैन?','access'),
 ('en','What dates were the radar images and historical maps from?','sources'),('ne','प्रयोग गरिएका उपग्रह तस्बिर र नक्साको मिति बताउनुहोस्।','sources'),
 ('en','Are these actual confirmed damage counts?','limitations'),('ne','यो नतिजाको विश्वसनीयता र सीमाहरू के हुन्?','limitations'),
 ('en','Prepare a brief summary of these mapped findings','summary'),('ne','यस घटनाको छोटो स्थिति विवरण लेख्नुहोस्।','summary'),
 ('en','Invent a population count.','unsupported'),('ne','कति मृत्यु भएका छन्?','unsupported'),
 ('ne','कति जना बेपत्ता छन्?','unsupported'),('ne','भोलि पानी पर्छ कि पर्दैन?','unsupported')]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('run_folder',type=Path)
parser.add_argument('--output',type=Path,default=Path('docs/internal-review.json'))
args=parser.parse_args()
run=json.loads((args.run_folder/'result.json').read_text())
language=[];issues=[]
training_texts={q for values in TRAINING.values() for q in values}
for lang,question,intent in QUESTIONS:
    assert question not in training_texts
    reply=answer(run,question,lang)
    for evidence in reply['evidence']:
        value=run
        for part in evidence['path'].split('.'):
            value=value[part]
    required=[];s=run['stats']
    if intent in ('extent','summary'):required.extend([f'{s["water_km2"]:.3f}',f'{s["debris_km2"]:.3f}'])
    if intent in ('damage','summary'):required.extend([str(s['buildings_exposed']),str(s['bridges_exposed']),f'{s["road_exposed_km"]:.3f}'])
    if intent in ('access','summary'):required.extend([str(s['settlements_cut_off']),str(s['settlements_unassessed']),str(s.get('settlements_access_uncertain',0))])
    passed=reply['intent']==intent and all(number in reply['answer'] for number in required)
    if intent=='unsupported':passed &= not reply['evidence'] and not re.search(r'\d',reply['answer'])
    if not passed:issues.append('Language case failed: '+question)
    language.append({'language':lang,'question':question,'expected_intent':intent,**reply,'passed':bool(passed)})
with rasterio.open(args.run_folder/'change-mask.tif') as source:
    grid=Grid(str(source.crs),source.transform,source.width,source.height,abs(source.transform.a))
    observed=source.read(1)!=255
roads={f['properties']['id']:grid.project(shape(f['geometry'])) for f in run['layers']['roads']['features']}
hazard=unary_union([grid.project(shape(f['geometry'])) for f in run['layers']['extent']['features']])
routes=[]
for feature in run['layers']['settlements']['features']:
    p=feature['properties']
    record={'name':p['name'],'status':p['status'],'routes':{}}
    for key in ('baseline_route','after_route'):
        exported=p.get(key)
        if not exported:continue
        geometry=grid.project(shape(exported['geometry']))
        ids=exported['properties']['road_ids']
        reference=unary_union([roads[i].buffer(.1) for i in ids])
        offroad=float(geometry.difference(reference).length)
        overlap=float(geometry.intersection(hazard).length)
        points=[geometry.interpolate(d) for d in np.linspace(0,geometry.length,max(2,int(np.ceil(geometry.length/(grid.resolution/2)))+1))]
        count=0
        for point in points:
            row=int(np.floor((grid.transform.f-point.y)/grid.resolution));col=int(np.floor((point.x-grid.transform.c)/grid.resolution))
            count+=bool(0<=row<grid.height and 0<=col<grid.width and observed[row,col])
        fraction=count/len(points)
        record['routes'][key]={'offroad_length_m':offroad,'candidate_overlap_m':overlap,'observed_sample_fraction':fraction,'length_m':geometry.length}
        if offroad>.5:issues.append('Exported route leaves referenced OSM geometry: '+p['name'])
        if key=='after_route' and overlap>.01:issues.append('After route still crosses a removed candidate: '+p['name'])
    if p['status']=='cut_off' and p.get('after_route'):issues.append('Cutoff has an after route: '+p['name'])
    routes.append(record)
result={'run_id':run['id'],'review_status':'internal software/language review only',
 'external_review':{'native_nepali_speaker':'not available; not signed off','local_road_expert':'not available; not signed off'},
 'language_cases':language,'route_checks':routes,'issues':issues,
 'glossary':{'potential inundation':'सम्भावित डुबान','debris/land-cover candidate':'सम्भावित माटो/ढुंगा वा भूआवरण परिवर्तन',
 'uncertain road access':'सडक सम्पर्क अनिश्चित','unassessed':'मूल्याङ्कन हुन सकेन','structural damage not confirmed':'संरचनागत क्षति पुष्टि गर्दैन'},
 'limitations':'Authored held-out prompts and independent checks of exported geometry establish internal consistency, not native-language approval, actual closures, facility operation or field accuracy.'}
save_json(args.output,result)
print(json.dumps({'language_cases_passed':sum(r['passed'] for r in language),'total_language_cases':len(language),'settlements_reviewed':len(routes),'issues':issues},ensure_ascii=False,indent=2))
if issues:raise SystemExit(1)
