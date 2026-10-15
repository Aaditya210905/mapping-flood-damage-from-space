"""Export a saved analysis into bilingual reports; demo data needs explicit permission."""
import argparse
import base64
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.copilot import answer
from app.evidence import EvidenceContext
from app.report import FONTS, build_report_data, report_html, report_pdf
from app.storage import sha256, save_json
from app.copilot_types import SituationReport


def verified_snapshot(verification, run_folder, context, language):
    if not verification.get('checks_complete') or verification.get('run_id') != context.run_id:
        raise ValueError('API verification is incomplete or belongs to another analysis')
    record=next(item for item in verification['reports'] if item['language']==language)
    path=urlsplit(record['links']['json']).path
    identifier=str(UUID(path.rsplit('/',1)[-1]))
    if path != f'/api/analyses/{context.run_id}/reports/{identifier}':
        raise ValueError('Report snapshot URL does not belong to this analysis')
    source=run_folder.resolve().parent/'.reports'/context.run_id/f'{identifier}.json'
    report=SituationReport.model_validate_json(source.read_text())
    if report.run_id!=context.run_id or report.language!=language or report.report_id!=identifier:
        raise ValueError('Report snapshot identity mismatch')
    context.call('event_summary',{})
    context.call('sources_limitations',{})
    for fact in report.facts:
        if fact.fact_id not in context.facts and fact.subject_type not in ('analysis','observation'):
            context.call('feature_evidence',{'layer':fact.subject_type,'feature_id':fact.subject_id})
        if context.facts.get(fact.fact_id)!=fact:
            raise ValueError('Report snapshot facts differ from the selected saved analysis')
    return report, {'name':source.name,'sha256':sha256(source)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_folder', type=Path)
    parser.add_argument('--output', type=Path, default=Path('output/pdf/copilot-reports'))
    parser.add_argument('--allow-demo', action='store_true')
    parser.add_argument('--use-llm', action='store_true', help='Use configured Groq for the accompanying conversational briefing.')
    parser.add_argument('--verified-api-record',type=Path,
                        help='Export previously verified API report snapshots without new provider calls')
    args = parser.parse_args()
    if args.use_llm and args.verified_api_record:
        parser.error('--use-llm and --verified-api-record are alternatives')
    source = args.run_folder.resolve() / 'result.json'
    try:
        run = json.loads(source.read_text(encoding='utf-8'))
        context = EvidenceContext(run)
        verification=json.loads(args.verified_api_record.read_text()) if args.verified_api_record else None
        if run['mode'] == 'demo' and not args.allow_demo:
            parser.error('Synthetic analysis rejected. Use --allow-demo only for explicitly labeled preview reports.')
        args.output.mkdir(parents=True, exist_ok=True)
        records = []
        prefix = 'preview' if run['mode'] == 'demo' else 'situation-report'
        for language in ('en', 'ne'):
            response = answer(run, 'Write a situation report', language) if args.use_llm else None
            snapshot=None
            if verification:
                report,snapshot=verified_snapshot(verification,args.run_folder,context,language)
            else:
                report = build_report_data(run, language, response)
            base = args.output / f'{prefix}-{language}'
            save_json(base.with_suffix('.json'), report.model_dump(mode='json'))
            html = report_html(run, language, report)
            for font in ('NotoSansDevanagari-Regular.ttf', 'NotoSansDevanagari-Bold.ttf'):
                encoded = base64.b64encode((FONTS / font).read_bytes()).decode('ascii')
                html = html.replace('/static/fonts/' + font, 'data:font/ttf;base64,' + encoded)
            base.with_suffix('.html').write_text(html, encoding='utf-8')
            base.with_suffix('.pdf').write_bytes(report_pdf(run, language, report))
            if response:
                save_json(args.output / f'{prefix}-briefing-{language}.json', response)
            records.append({'language': language, 'report_id': report.report_id, 'status': report.status,
                            'missing_fields': report.missing_fields, 'generation': report.generation.model_dump(),
                            'verified_snapshot':snapshot,
                            'files': {kind: {'name': base.with_suffix('.' + kind).name,
                                             'sha256': sha256(base.with_suffix('.' + kind))} for kind in ('pdf', 'html', 'json')}})
        manifest = {'run_id': context.run_id, 'data_mode': run['mode'], 'source_sha256': sha256(source),
                    'synthetic_preview': run['mode'] == 'demo', 'reports': records,
                    'acquisition_mode':run.get('acquisition_mode'),
                    'api_verification_sha256':sha256(args.verified_api_record) if args.verified_api_record else None,
                    'attributions': records and report.attributions,
                    'limitations': ('Synthetic software previews, not real event observations. ' if run['mode']=='demo' else 'Saved real inputs retain their original observation dates and partial coverage. ')+'Missing evidence remains unavailable. Field verification and native-speaker review are pending.'}
        save_json(args.output / 'manifest.json', manifest)
        print(json.dumps({'output': str(args.output.resolve()), 'run_id': context.run_id,
                          'mode': run['mode'], 'reports': records}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError):
        parser.exit(1, 'Report export could not finish. Check the saved run, output permissions and documented PDF dependencies.\n')


if __name__ == '__main__':
    main()
