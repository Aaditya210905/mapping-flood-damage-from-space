"""Typed, evidence-backed one-page HTML/PDF reports in English and Nepali."""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from uuid import uuid4
import re

from app.copilot_types import SituationReport, ReportSection, GenerationInfo, AnswerDraft
from app.evidence import EvidenceContext, FLOOD_FIELDS, INFRA_FIELDS, ACCESS_FIELDS
from app.grounding import format_value, EXPLANATIONS, validate_draft, render_draft
from app.models import ATTRIBUTIONS

FONTS = Path(__file__).resolve().parent / 'static' / 'fonts'


class ReportDataError(ValueError):
    pass


class ReportRenderingError(ValueError):
    pass


def build_report_data(run, language='en', response=None):
    if language not in ('en','ne'):raise ReportDataError('Use English or Nepali')
    context=EvidenceContext(run)
    run=context.run
    context.call('event_summary',{})
    context.call('sources_limitations',{})
    i=language=='ne'
    for key in ('analysis_name','event_date'):
        if context.global_fields[key].status!='available':
            raise ReportDataError('Cannot create a situation report: analysis area or event date is unavailable.')
    if response and (response.get('run_id')!=context.run_id or response.get('language')!=language):
        raise ReportDataError('Report response belongs to a different analysis or language.')
    authored = {}
    if response and response.get('draft'):
        try:
            draft = AnswerDraft.model_validate(response['draft'])
            for section in draft.sections:
                for claim in section.claims:
                    if claim.subject_type not in ('analysis', 'observation'):
                        context.call('feature_evidence', {'layer': claim.subject_type, 'feature_id': claim.subject_id})
            validate_draft(draft, context, language)
            for section in draft.sections:
                if section.narrative:
                    authored.setdefault(section.topic, []).append(section)
        except (ValueError, TypeError, KeyError) as error:
            raise ReportDataError('Report narrative does not match the saved analysis facts.') from error
    selected=[];sections=[]
    def add(heading, keys, extra=None, topic=None):
        facts=[context.global_fields[key] for key in keys]
        selected.extend(facts)
        lines=[]; used=set(); narrated=[]
        for section in authored.get(topic, []):
            part=AnswerDraft(run_id=context.run_id, language=language, kind='answer', sections=[section])
            # Report sections already carry mandatory uncertainty below. Avoid
            # repeating the area name and the same disclaimers for every value.
            rendered, part_facts=render_draft(part,context,compact=True)
            if run['mode']=='demo':rendered=rendered.split('\n\n',1)[1]
            # The frozen report retains fact IDs in its typed section/evidence;
            # long UUID citations inline would make the one-page PDF unreadable.
            rendered=re.sub(r'\s*\[[0-9a-f-]{36}:[0-9a-f]{20}\]', '', rendered)
            lines.extend(rendered.splitlines())
            used.update(f.field_name for f in part_facts)
            narrated.extend(part_facts)
        selected.extend(narrated)
        remaining=[f"{fact.label_ne if i else fact.label_en}: {format_value(fact,language)}" for fact in facts if fact.field_name not in used]
        if remaining:lines.append('; '.join(remaining)+'.')
        lines.extend(text for text in extra or [] if text not in lines)
        sections.append(ReportSection(heading=heading[i],lines=lines,fact_ids=list(dict.fromkeys(f.fact_id for f in [*facts,*narrated]))))
    add(('Satellite observations','उपग्रह अवलोकन'),('radar_before_acquired','radar_after_acquired','optical_scene_ids','optical_acquisition_dates'))
    add(('Flood and surface-change findings','डुबान र भूआवरण परिवर्तन'),FLOOD_FIELDS,
        [EXPLANATIONS['surface_change_uncertain'][i],EXPLANATIONS['coverage_limits'][i]], topic='flood')
    add(('Potential infrastructure exposure','पूर्वाधारमा सम्भावित प्रभाव'),INFRA_FIELDS,[EXPLANATIONS['potential_damage'][i]], topic='infrastructure')
    settlement_facts=[]
    for (layer,identifier),feature in context.index.items():
        if layer=='settlements' and feature['properties'].get('status')=='cut_off':
            settlement_facts.extend(f for f in context.feature_facts(layer,identifier) if f.field_name=='name')
            if len(settlement_facts)>=3:break
    selected.extend(settlement_facts)
    names=', '.join(f"{f.value} [{f.subject_id}]" for f in settlement_facts if f.status=='available')
    extra=[EXPLANATIONS['modeled_closure'][i]]
    if names:extra.append(('उदाहरण बस्ती: ' if i else 'Example settlements: ')+names+('. पूर्ण सूची नक्सा निर्यातमा छ।' if i else '. Complete list remains in map exports.'))
    add(('Modeled settlement connectivity','मोडेल अनुसार बस्तीको सडक सम्पर्क'),ACCESS_FIELDS,extra, topic='connectivity')
    add(('Sources and evidence','स्रोत र प्रमाण'),('radar_before_id','radar_after_id','osm_snapshot','mapping_method'),
        [('प्रमाण: चयन गरिएको विश्लेषणको result.json, extent.geojson र पूर्वाधार/सडक सम्पर्क अभिलेख।' if i else 'Evidence: result.json, extent.geojson and the selected analysis’s infrastructure/connectivity layers.')], topic='sources')
    # Always include these authored limitations, even when the LLM requests a shorter briefing.
    codes=('satellite_limits','routing_limits','coverage_limits')
    limits=[EXPLANATIONS[code][i] for code in codes]
    limits.append('जनसङ्ख्या, मृत्यु, घाइते वा बेपत्ताको अनुमान उपलब्ध छैन। स्थल निरीक्षण र नेपाली भाषा समीक्षा आवश्यक छ।' if i else 'Population and casualty estimates are unavailable. Field verification and native-speaker Nepali review remain necessary.')
    add(('Important limitations','सीमाहरू'),(),limits,topic='limitations')
    unique=list({fact.fact_id:fact for fact in selected}.values())
    missing=[fact.field_name for fact in unique if fact.status!='available']
    recorded_credits=run.get('attributions')
    attributions=list(dict.fromkeys(ATTRIBUTIONS+[text for text in (recorded_credits if isinstance(recorded_credits,list) else []) if isinstance(text,str)]))
    generation=GenerationInfo.model_validate(response['generation']) if response else GenerationInfo(mode='deterministic_fallback',provider=None,model=None,reason='deterministic_report',validation='deterministic_facts')
    return SituationReport(report_id=str(uuid4()),run_id=context.run_id,language=language,
        title='बाढी स्थिति प्रतिवेदन' if i else 'Flood situation report',
        generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        analysis_name=str(context.global_fields['analysis_name'].value),event_date=str(context.global_fields['event_date'].value),
        data_mode=run['mode'],status='partial' if missing or run.get('analysis_status')=='partial' else 'complete',sections=sections,facts=unique,
        attributions=attributions,generation=generation,missing_fields=missing)


def report_html(run, language='en', data=None, *, font_size=9.5):
    data=data or build_report_data(run,language)
    if data.run_id!=str(run['id']) or data.language!=language:
        raise ReportDataError('Report does not belong to the selected analysis/language.')
    i=language=='ne'
    demo=('<p class="notice">'+('कृत्रिम प्रदर्शन — वास्तविक घटनाको अवलोकन वा नतिजा होइन।' if i else 'SYNTHETIC DEMO — not observations or results of the real event.')+'</p>') if data.data_mode=='demo' else ''
    missing_labels=[f.label_ne if i else f.label_en for f in data.facts if f.field_name in data.missing_fields]
    partial=('<p class="notice">'+('अपूर्ण प्रतिवेदन — आंशिक अवलोकन। उपलब्ध नभएका मापन शून्य होइनन्।' if i else 'INCOMPLETE REPORT — partial observations. Unavailable measurements are not zero.')+' '+escape(', '.join(missing_labels))+'</p>') if data.status=='partial' else ''
    sections=''.join('<section><h2>'+escape(section.heading)+'</h2>'+''.join('<p>'+escape(line)+'</p>' for line in section.lines)+'</section>' for section in data.sections)
    credits='<br>'.join(escape(text) for text in data.attributions)
    data_mode_label='CACHED REAL INPUTS' if run.get('acquisition_mode')=='cached_reprocessing' else data.data_mode.upper()
    mode_label=('Groq-assisted; values verified from map facts' if data.generation.mode=='llm' else 'Deterministic evidence-backed report') if not i else ('Groq सहयोग; मापन नक्साको तथ्यबाट जाँचिएको' if data.generation.mode=='llm' else 'नक्साको प्रमाणमा आधारित निश्चित प्रतिवेदन')
    return f'''<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{escape(data.title)}</title>
<style>
@font-face{{font-family:NotoDevanagari;src:url('/static/fonts/NotoSansDevanagari-Regular.ttf')}}
@font-face{{font-family:NotoDevanagari;font-weight:700;src:url('/static/fonts/NotoSansDevanagari-Bold.ttf')}}
@page{{size:A4;margin:12mm}}
*{{box-sizing:border-box}}body{{font:{font_size}pt/1.32 Arial,NotoDevanagari,sans-serif;color:#193d35;max-width:760px;margin:24px auto;padding:0 14px}}
body:lang(ne){{font-family:NotoDevanagari,Arial,sans-serif;line-height:1.38}}
h1{{font-size:21pt;margin:4px 0}}h2{{font-size:10pt;margin:9px 0 3px;color:#286e56}}p{{margin:2px 0;overflow-wrap:anywhere}}section{{break-inside:avoid}}
header{{border-bottom:2px solid #286e56;padding-bottom:8px}}small{{font-size:7.2pt}}.notice{{color:#8b551c;font-weight:700;margin:4px 0}}
footer{{border-top:1px solid #cbd8c9;margin-top:10px;padding-top:6px;font-size:6.8pt;line-height:1.25}}button{{padding:9px 14px;background:#286e56;color:white;border:0;margin-bottom:12px;cursor:pointer}}
@media print{{body{{margin:0;padding:0;max-width:none}}button{{display:none}}}}
</style></head><body><button onclick="window.print()">Print / Save PDF</button>
<header><small>FLOODLENS AI · {escape(data_mode_label)} · {escape(mode_label)}</small><h1>{escape(data.title)}</h1>
<p>{escape(data.analysis_name)} · {escape(data.event_date)}</p><small>{'तयार भएको समय' if i else 'Generated'}: {escape(data.generated_at)}<br>Run {escape(data.run_id)} · Report {escape(data.report_id)}</small>{demo}{partial}</header>
{sections}<footer>{credits}<br>{'शैक्षिक नमुना; क्षेत्रीय सत्यापन आवश्यक।' if i else 'Educational prototype; independent field verification required.'}</footer></body></html>'''


def report_pdf(run, language='en', data=None):
    data=data or build_report_data(run,language)
    try:
        from weasyprint import HTML
        from weasyprint.urls import URLFetcherResponse
        from weasyprint.text.fonts import FontConfiguration
    except (ImportError,OSError):
        raise ReportRenderingError('PDF rendering is unavailable. Install the documented Pango dependencies; printable HTML remains available.') from None
    allowed={(FONTS/name).resolve().as_uri(): FONTS/name for name in ('NotoSansDevanagari-Regular.ttf','NotoSansDevanagari-Bold.ttf')}
    if not all((FONTS/name).is_file() for name in ('NotoSansDevanagari-Regular.ttf','NotoSansDevanagari-Bold.ttf')):
        raise ReportRenderingError('Bundled Nepali fonts are unavailable; restore app/static/fonts.')
    def fetch(url,**kwargs):
        if url not in allowed:raise ValueError('Report resource access is not allowed')
        return URLFetcherResponse(url, body=allowed[url].read_bytes(), headers={"Content-Type": "font/ttf"})
    for size in (9.5,9,8.5):
        html=report_html(run,language,data,font_size=size)
        for name in ('NotoSansDevanagari-Regular.ttf','NotoSansDevanagari-Bold.ttf'):
            html=html.replace('/static/fonts/'+name,(FONTS/name).resolve().as_uri())
        try:
            document=HTML(string=html,url_fetcher=fetch).render(font_config=FontConfiguration())
            if len(document.pages)==1:return document.write_pdf()
        except Exception:
            raise ReportRenderingError('The report could not be rendered. Use the printable HTML report and check the documented PDF dependencies.') from None
    raise ReportRenderingError('This analysis contains too much text for a readable one-page PDF. Use the HTML report or the full JSON evidence export.')
