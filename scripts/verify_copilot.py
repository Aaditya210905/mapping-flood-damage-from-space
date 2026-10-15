"""Check a stored run through the real local API; synthetic data requires --allow-demo."""
import argparse
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import time

import httpx
from pypdf import PdfReader


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--run-id')
    parser.add_argument('--allow-demo',action='store_true')
    parser.add_argument('--require-llm',action='store_true')
    parser.add_argument('--checks',choices=('all','reports'),default='all')
    parser.add_argument('--request-gap-seconds',type=float,default=0,
                        help='Pace provider-backed checks between 0 and 60 seconds for account token quotas')
    parser.add_argument('--output',type=Path,default=Path('output/qa/copilot-e2e.json'))
    parser.add_argument('--export-reports',type=Path,
                        help='Save the verified bilingual PDF/HTML/JSON snapshots and source manifest')
    args=parser.parse_args()
    if not 0 <= args.request_gap_seconds <= 60:
        parser.error('--request-gap-seconds must be between 0 and 60')
    with httpx.Client(base_url=args.base_url.rstrip('/'),timeout=70) as client:
        def get(path):
            response=client.get(path);response.raise_for_status();return response.json()
        previous_request=[None]
        observed=[]
        def post(path,body):
            if previous_request[0] is not None:
                time.sleep(max(0,args.request_gap_seconds-(time.monotonic()-previous_request[0])))
            previous_request[0]=time.monotonic()
            response=client.post(path,json=body);response.raise_for_status();data=response.json()
            observed.append({'endpoint':path,'request':body,'response':data})
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({'checks_complete':False,'observed':observed},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'endpoint':path,'generation':data.get('generation')},ensure_ascii=False),flush=True)
            return data
        run=get(('/api/analyses/'+args.run_id if args.run_id else '/api/analyses/latest-real')+'?view=dashboard')
        if run['mode']=='demo' and not args.allow_demo:
            parser.error('Synthetic run rejected. Use --allow-demo only for explicitly labeled software checks.')
        base='/api/analyses/'+run['id']
        checks=[];replies=[]
        for language,question in ([('en','How large is the newly detected water area?'),('ne','सम्भावित नयाँ डुबानको क्षेत्रफल कति छ?')] if args.checks=='all' else []):
            reply=post(base+'/copilot',{'question':question,'language':language})
            assert reply['run_id']==run['id'] and reply['language']==language
            assert all(f['run_id']==run['id'] for f in reply['facts'])
            fact=next(f for f in reply['facts'] if f['field_name']=='water_km2')
            assert fact['value']==run['stats'].get('water_km2')
            if args.require_llm:assert reply['generation']['mode']=='llm',reply['generation']
            replies.append({'question':question,**reply})
            checks.append(language+' answer uses authoritative water measurement')
        if args.checks=='all':
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
            record={'language':language,'report_id':report['report']['report_id'],'status':report['status'],
                    'links':report['links'],'generation':report['generation']}
            if args.export_reports:
                folder=args.export_reports
                folder.mkdir(parents=True,exist_ok=True)
                html=client.get(report['links']['html']);html.raise_for_status()
                offline=html.text
                fonts=Path(__file__).resolve().parent.parent/'app/static/fonts'
                for font in ('NotoSansDevanagari-Regular.ttf','NotoSansDevanagari-Bold.ttf'):
                    encoded=base64.b64encode((fonts/font).read_bytes()).decode('ascii')
                    offline=offline.replace('/static/fonts/'+font,'data:font/ttf;base64,'+encoded)
                outputs={'pdf':pdf.content,'html':offline.encode('utf-8'),
                         'json':json.dumps(report['report'],ensure_ascii=False,indent=2).encode('utf-8')}
                record['files']={}
                for kind,content in outputs.items():
                    target=folder/f'situation-report-{language}.{kind}'
                    target.write_bytes(content)
                    record['files'][kind]={'name':target.name,'sha256':hashlib.sha256(content).hexdigest()}
            reports.append(record)
            checks.append(language+' one-page PDF and authoritative report measurements')
        result={'checks_complete':True,'check_scope':args.checks,'run_id':run['id'],'data_mode':run['mode'],'synthetic':run['mode']=='demo','live_llm_required':args.require_llm,
                'checks':checks,'replies':replies,'reports':reports,
                'limitation':'Software consistency check; no claim of field accuracy or native-speaker review.'}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if args.export_reports:
            source=Path(__file__).resolve().parent.parent/'data/runs'/run['id']/'result.json'
            manifest={'run_id':run['id'],'data_mode':run['mode'],'acquisition_mode':run.get('acquisition_mode'),
                      'synthetic_preview':run['mode']=='demo','checks_complete':True,
                      'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None,
                      'reports':reports,'attributions':report['report']['attributions'],
                      'limitations':'Software consistency and rendered PDF checks; partial observations remain unknown. Field accuracy and native-speaker Nepali review are pending.'}
            (args.export_reports/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'run_id':run['id'],'data_mode':run['mode'],'checks_passed':len(checks),'report':str(args.output)},indent=2))


if __name__=='__main__':
    try:main()
    except (httpx.HTTPError,AssertionError,KeyError,StopIteration):
        print('Copilot verification failed. Check server configuration, selected run and the documented evidence requirements.',file=sys.stderr)
        raise SystemExit(1) from None
