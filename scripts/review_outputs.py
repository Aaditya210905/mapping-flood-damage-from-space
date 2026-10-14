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
from shapely import line_merge
from app.copilot import answer
from app.availability import normalize_analysis_availability
from app.copilot_query import plan_question
from app.evidence import EvidenceContext
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
def review_language(run, questions=QUESTIONS):
    """Check typed evidence and scope, not substrings in numerical prose.

    These are authored software checks, not a language-quality or field-accuracy
    benchmark. Query-derived counts are independently recomputed from saved
    records by the approved evidence implementation; no model arithmetic.
    """
    run=normalize_analysis_availability(run)
    language=[];issues=[]
    for lang,question,intent in questions:
        context=EvidenceContext(run)
        plan=plan_question(context,question,[])
        reply=answer(run,question,lang)
        if 'connectivity' in plan.topics:
            context.call('settlement_connectivity',{'status':plan.status,'offset':0,'limit':5,
                         'road_id':plan.road_id,'destination_type':plan.destination_type})
        facts={fact['fact_id']:fact for fact in reply.get('facts',[])}
        canonical=True
        for fact in facts.values():
            if fact['subject_type'] not in ('analysis','observation'):
                context.feature_facts(fact['subject_type'],fact['subject_id'],plan.destination_type)
            expected=context.facts.get(fact['fact_id'])
            canonical &= expected is not None and expected.model_dump(mode='json')==fact
        fields={fact['field_name'] for fact in facts.values()}
        required={'extent':{'water_km2'},'damage':{'roads_exposed','bridges_exposed'},
                  'sources':{'radar_before_acquired','radar_after_acquired','osm_snapshot'},
                  'limitations':{'limitations','warnings'},
                  'summary':{'water_km2','debris_km2','buildings_exposed','settlements_cut_off'}}.get(intent,set())
        if intent=='access':
            required={'destination_type','matching_settlements_count','destination_assessment_unavailable_count'} if plan.destination_type!='all' else {'settlements_cut_off','settlements_unassessed'}
        cited=all('['+identifier+']' in reply['answer'] for identifier in facts)
        passed=reply['intent'] in (intent,'compound') and required.issubset(fields) and canonical and cited
        if plan.destination_type!='all' and intent=='access':
            passed &= 'settlements_cut_off' not in fields and any(f['field_name']=='destination_type' and f['value']==plan.destination_type for f in facts.values())
        if intent=='unsupported':
            passed=reply['intent']=='unsupported' and not facts and not reply['evidence'] and not re.search(r'\d',reply['answer'])
        if not passed:issues.append('Internal language/evidence case failed: '+question)
        language.append({'language':lang,'question':question,'expected_intent':intent,**reply,'passed':bool(passed)})
    return language,issues


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_folder',type=Path)
    parser.add_argument('--output',type=Path,default=Path('docs/internal-review.json'))
    args=parser.parse_args()
    run=normalize_analysis_availability(json.loads((args.run_folder/'result.json').read_text()))
    language,issues=review_language(run)
    with rasterio.open(args.run_folder/'change-mask.tif') as source:
        grid=Grid(str(source.crs),source.transform,source.width,source.height,abs(source.transform.a))
        observed=source.read(1)!=255
    roads={f['properties']['id']:grid.project(shape(f['geometry'])) for f in run['layers']['roads']['features']}
    road_properties={f['properties']['id']:f['properties'] for f in run['layers']['roads']['features']}
    hazard=unary_union([grid.project(shape(f['geometry'])) for f in run['layers']['extent']['features']])
    # A post-event route may legitimately cross a below-threshold candidate.
    # Compare against the saved closure scenario, not every hazard intersection.
    removed=[]
    for identifier,road in roads.items():
        props=road_properties[identifier]
        if props.get('closure_candidate') is False:
            continue
        minimum=props.get('closure_min_length_m',.001)
        if props.get('bridge') not in (None,'no') and 'closure_candidate' in props:
            # Bridge water exposure alone is explicitly not a closure. The
            # class-specific closure geometry is not exported in older runs.
            continue
        intersection=line_merge(road.intersection(hazard))
        pieces=list(intersection.geoms) if hasattr(intersection,'geoms') else [intersection]
        removed.extend(part for part in pieces if part.geom_type=='LineString' and part.length>=minimum-1e-6)
    removed=unary_union(removed)
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
            if key=='after_route' and geometry.intersection(removed).length>.01:issues.append('After route still crosses a scenario-removed candidate: '+p['name'])
        if p['status']=='cut_off' and p.get('after_route'):issues.append('Cutoff has an after route: '+p['name'])
        routes.append(record)
    result={'run_id':run['id'],'review_status':'internal software/language review only',
     'external_review':{'native_nepali_speaker':'not available; not signed off','local_road_expert':'not available; not signed off'},
     'language_cases':language,'route_checks':routes,'issues':issues,
     'glossary':{'potential inundation':'सम्भावित डुबान','debris/land-cover candidate':'सम्भावित माटो/ढुंगा वा भूआवरण परिवर्तन',
     'uncertain road access':'सडक सम्पर्क अनिश्चित','unassessed':'मूल्याङ्कन हुन सकेन','structural damage not confirmed':'संरचनागत क्षति पुष्टि गर्दैन'},
     'limitations':'Authored software prompts and checks of exported geometry establish internal consistency, not native-language approval, actual closures, facility operation or field accuracy. Bridge class-specific closure geometry is not independently checked by this utility.'}
    save_json(args.output,result)
    print(json.dumps({'language_cases_passed':sum(r['passed'] for r in language),'total_language_cases':len(language),'settlements_reviewed':len(routes),'issues':issues},ensure_ascii=False,indent=2))
    if issues:raise SystemExit(1)


if __name__ == "__main__":
    main()
