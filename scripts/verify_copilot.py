"""Check a stored run through the real local API; synthetic data requires --allow-demo."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys

import httpx
from pypdf import PdfReader


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--run-id')
    parser.add_argument('--allow-demo',action='store_true')
    parser.add_argument('--require-llm',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('output/qa/copilot-e2e.json'))
    args=parser.parse_args()
    with httpx.Client(base_url=args.base_url.rstrip('/'),timeout=70) as client:
        def get(path):
            response=client.get(path);response.raise_for_status();return response.json()
        def post(path,body):
            response=client.post(path,json=body);response.raise_for_status();return response.json()
        run=get('/api/analyses/'+args.run_id if args.run_id else '/api/analyses/latest-real')
        if run['mode']=='demo' and not args.allow_demo:
            parser.error('Synthetic run rejected. Use --allow-demo only for explicitly labeled software checks.')
        base='/api/analyses/'+run['id']
        checks=[];replies=[]
        for language,question in [('en','How large is the newly detected water area?'),('ne','सम्भावित नयाँ डुबानको क्षेत्रफल कति छ?')]:
            reply=post(base+'/copilot',{'question':question,'language':language})
            assert reply['run_id']==run['id'] and reply['language']==language
            assert all(f['run_id']==run['id'] for f in reply['facts'])
            fact=next(f for f in reply['facts'] if f['field_name']=='water_km2')
            assert fact['value']==run['stats'].get('water_km2')
            if args.require_llm:assert reply['generation']['mode']=='llm',reply['generation']
            replies.append({'question':question,**reply})
            checks.append(language+' answer uses authoritative water measurement')
        compound=post(base+'/copilot',{'question':'Which roads are exposed and which settlements are isolated?','language':'en'})
        assert {'roads_exposed','settlements_cut_off'}.issubset({f['field_name'] for f in compound['facts']})
        if args.require_llm:assert compound['generation']['mode']=='llm',compound['generation']
        replies.append(compound);checks.append('Compound infrastructure/connectivity question')
        unsupported=post(base+'/copilot',{'question':'How many people died?','language':'en'})
        assert unsupported['intent']=='unsupported' and not unsupported['facts']
        replies.append(unsupported);checks.append('Absent casualty statistic receives no estimate')
        reports=[]
        for language in ('en','ne'):
            report=post(base+'/report',{'language':language})
            assert report['run_id']==run['id'] and report['language']==language
            if args.require_llm:assert report['generation']['mode']=='llm',report['generation']
            for f in report['report']['facts']:
                if f['field_name'] in run['stats']:assert f['value']==run['stats'][f['field_name']]
            pdf=client.get(report['links']['pdf']);pdf.raise_for_status()
            assert len(PdfReader(BytesIO(pdf.content)).pages)==1
            reports.append({'language':language,'status':report['status'],'links':report['links'],'generation':report['generation']})
            checks.append(language+' one-page PDF and authoritative report measurements')
        result={'run_id':run['id'],'data_mode':run['mode'],'synthetic':run['mode']=='demo','live_llm_required':args.require_llm,
                'checks':checks,'replies':replies,'reports':reports,
                'limitation':'Software consistency check; no claim of field accuracy or native-speaker review.'}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'run_id':run['id'],'data_mode':run['mode'],'checks_passed':len(checks),'report':str(args.output)},indent=2))


if __name__=='__main__':
    try:main()
    except (httpx.HTTPError,AssertionError,KeyError,StopIteration):
        print('Copilot verification failed. Check server configuration, selected run and the documented evidence requirements.',file=sys.stderr)
        raise SystemExit(1) from None
