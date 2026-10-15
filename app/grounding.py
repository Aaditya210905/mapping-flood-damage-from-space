"""Semantic fact binding and deterministic bilingual claim rendering.

The model selects claims/narrative codes. It cannot supply values, names, dates,
units, labels or unrestricted factual prose. Evidence accuracy still depends on
input provenance and the geospatial engine; this is not a field-truth guarantee.
"""
from decimal import Decimal, ROUND_HALF_UP
import re

from app.copilot_types import AnswerDraft, Claim
from app.evidence import FLOOD_FIELDS, INFRA_FIELDS, INFRA_CONTEXT_FIELDS, INFRA_QUERY_FIELDS, ACCESS_FIELDS, CONNECTIVITY_QUERY_FIELDS


class GroundingError(ValueError):
    pass


EXPLANATIONS = {
    "potential_damage": ("Mapped intersection indicates potential exposure, not confirmed damage or destruction.", "नक्साको परिवर्तनसँग जुध्नु सम्भावित प्रभाव हो; क्षति वा विनाश पुष्टि भएको होइन।"),
    "modeled_closure": ("Connectivity uses a thresholded candidate-overlap closure scenario. Water beneath a bridge does not prove failure. Actual closures remain unverified.", "परिवर्तनसँग जुधेको लम्बाइको सीमा अनुसार सडक बन्द मानेर सम्पर्क गणना गरिएको हो। पुलमुनिको पानीले पुल बिग्रेको पुष्टि गर्दैन। वास्तविक सडक बन्द जाँचिएको छैन।"),
    "coverage_limits": ("Unobserved areas remain unknown. No mapped change does not prove absence of damage.", "अवलोकन नभएको क्षेत्र अज्ञात छ। परिवर्तन नदेखिनु क्षति नभएको प्रमाण होइन।"),
    "surface_change_uncertain": ("Surface-change/debris candidates can include other land-cover changes; they are not confirmed debris flows.", "माटो/ढुंगा वा भूआवरण परिवर्तनका सम्भावित क्षेत्र अन्य परिवर्तन पनि हुन सक्छन्; भग्नावशेषको बहाव पुष्टि भएको होइन।"),
    "satellite_limits": ("Satellite revisit gaps, terrain shadow, clouds and vegetation limit interpretation; the system cannot provide minute-ahead warnings.", "उपग्रहको पुनरागमन, भूभागको छाया, बादल र वनस्पतिले नतिजा सीमित गर्छन्; यसले केही मिनेटअघि चेतावनी दिन सक्दैन।"),
    "routing_limits": ("Incomplete OSM roads, facility operation and routes outside the analysis bounds remain unverified.", "अपूर्ण OSM सडक, गन्तव्य सञ्चालन र विश्लेषण क्षेत्रबाहिरका मार्ग जाँचिएका छैनन्।"),
    "missing_data": ("Unavailable measurements remain unavailable and must not be interpreted as zero.", "उपलब्ध नभएका मापनलाई शून्य मान्न मिल्दैन।"),
    "destination_limits": ("Access is specific to the requested mapped destination type. Combined destination results cannot establish type-specific access; facility operation is unverified.", "पहुँच अनुरोध गरिएको नक्साको गन्तव्यको प्रकारका लागि मात्र हो। संयुक्त गन्तव्यको नतिजाले विशेष प्रकारको गन्तव्यसम्मको पहुँच पुष्टि गर्दैन; गन्तव्य सञ्चालन जाँचिएको छैन।"),
}
HEADINGS = {
    "summary": ("Analysis summary", "विश्लेषणको सारांश"),
    "flood": ("Flood and surface change", "डुबान र भूआवरण परिवर्तन"),
    "infrastructure": ("Potential infrastructure exposure", "पूर्वाधारमा सम्भावित प्रभाव"),
    "inventory": ("Mapped inventory", "नक्सामा रहेका अभिलेख"),
    "connectivity": ("Modeled settlement connectivity", "मोडेल अनुसार बस्तीको सडक सम्पर्क"),
    "sources": ("Recorded observations and sources", "अभिलेखित अवलोकन र स्रोत"),
    "limitations": ("Limitations", "सीमाहरू"),
    "feature": ("Feature evidence", "विशेष नक्सा अभिलेखको प्रमाण"),
}
STATUS_TEXT = {
    'not_connected': ('No modeled connection: baseline disconnected or access lost; uncertain and unassessed access excluded', 'मोडेल अनुसार सम्पर्क छैन: घटनाअघि सम्पर्क नभएको वा पछि गुमेको; अनिश्चित र अमूल्याङ्कित अवस्था समावेश छैन'),
    'baseline_connected': ('Baseline connection existed in the mapped network', 'घटनाअघिको नक्साको सञ्जालमा सम्पर्क थियो'),
    'exposed': ('Potential exposure to mapped change', 'नक्साको परिवर्तनबाट सम्भावित प्रभाव'),
    'not_exposed': ('No mapped exposure within complete available hazard observations; not a safety determination', 'उपलब्ध विपद् वर्गको पूर्ण अवलोकनमा प्रभाव भेटिएन; सुरक्षित भएको निष्कर्ष होइन'),
    "cut_off": ("Modeled loss of previous road connection", "मोडेल अनुसार पहिलेदेखिको सडक सम्पर्क गुमेको"),
    "connected": ("Modeled route available in the assessed network", "मूल्याङ्कन गरिएको सञ्जालमा मोडेल अनुसार मार्ग उपलब्ध"),
    "uncertain_access": ("Uncertain access; route, connectors or exterior alternatives are unverified", "सडक सम्पर्क अनिश्चित; मार्ग, जोड्ने बाटो वा क्षेत्रबाहिरका विकल्प जाँचिएका छैनन्"),
    "unassessed": ("Unassessed", "मूल्याङ्कन भएको छैन"),
    "baseline_disconnected": ("No baseline connection in the mapped network", "घटनाअघिको नक्साको सञ्जालमा सम्पर्क नभेटिएको"),
    "potential_exposure": ("Potential exposure to mapped change", "नक्साको परिवर्तनबाट सम्भावित प्रभाव"),
    "no_mapped_change_at_observed_samples": ("No mapped change at observed samples", "अवलोकित नमुनामा परिवर्तन भेटिएन"),
    "no_mapped_change_in_assessed_hazard_scope": ("No mapped change within the assessed hazard classes; other classes remain unknown", "मूल्याङ्कन गरिएका विपद्का वर्गमा परिवर्तन भेटिएन; अन्य वर्ग अज्ञात छन्"),
    "unassessed_or_partial": ("Unassessed or partially observed", "मूल्याङ्कन नभएको वा आंशिक अवलोकन"),
    "demo": ("Synthetic demonstration", "कृत्रिम प्रदर्शन"),
    "live": ("Actual observations", "वास्तविक अवलोकन"),
    "ascending": ("Ascending", "उत्तरतर्फ जाने कक्षा"),
    "descending": ("Descending", "दक्षिणतर्फ जाने कक्षा"),
    "partial": ("Partial observations", "आंशिक अवलोकन"),
    "complete": ("Complete observations", "पूर्ण अवलोकन"),
    "hospital": ("Hospital", "अस्पताल"),
    "town": ("Town", "नगर"),
}
UNSUPPORTED = ("The selected analysis cannot determine that information. Deaths, injuries, missing people and population estimates are unavailable. Ask about mapped changes, potential exposure, modeled connectivity, sources or limitations.",
               "चयन गरिएको विश्लेषणबाट यो जानकारी निर्धारण गर्न सकिँदैन। मृत्यु, घाइते, बेपत्ता वा जनसङ्ख्याको अनुमान उपलब्ध छैन। नक्साको परिवर्तन, सम्भावित प्रभाव, मोडेल अनुसार सडक सम्पर्क, स्रोत वा सीमाबारे सोध्नुहोस्।")
CLARIFICATION = ("Which mapped feature or measurement do you mean? Please give the road, bridge, building or settlement name/ID, or name the measurement. The previous context does not identify a unique subject.",
                 "कुन नक्सा अभिलेख वा मापनबारे सोध्नुभएको हो? सडक, पुल, भवन वा बस्तीको नाम/पहिचान वा मापनको नाम दिनुहोस्। अघिल्लो सन्दर्भबाट एउटा निश्चित विषय पहिचान हुन सकेन।")


def claim_for(fact, operation="exact"):
    return Claim(fact_id=fact.fact_id, run_id=fact.run_id, subject_type=fact.subject_type,
                 subject_id=fact.subject_id, field_name=fact.field_name,
                 interpretation=fact.interpretation, operation=operation)


def validate_draft(draft, context, language):
    if draft.run_id != context.run_id or draft.language != language:
        raise GroundingError("Wrong analysis or language")
    if sum(len(section.claims) for section in draft.sections) > 80:
        raise GroundingError("Too many claims")
    if draft.kind in ("unsupported", "clarification") and draft.sections:
        raise GroundingError("Unsupported or clarification response contains sections")
    if draft.kind not in ("unsupported", "clarification") and not any(section.claims for section in draft.sections):
        raise GroundingError("No supported claims")
    for section in draft.sections:
        for claim in section.claims:
            fact = context.facts.get(claim.fact_id)
            if fact is None or claim.fact_id not in context.returned_fact_ids:
                raise GroundingError("Fact was not returned by an approved tool")
            if (claim.run_id, claim.subject_type, claim.subject_id, claim.field_name, claim.interpretation) != (fact.run_id, fact.subject_type, fact.subject_id, fact.field_name, fact.interpretation):
                raise GroundingError("Claim subject, field or interpretation does not match its evidence")
            if claim.operation != "exact":
                if fact.status != "available" or type(fact.value) not in (int, float) or fact.unit not in ("km²", "km", "m", "%", "fraction", "dB", "degrees"):
                    raise GroundingError("Rounding is not permitted for this fact")
            if section.topic == "flood" and fact.field_name not in FLOOD_FIELDS + ("mapping_method", "water_threshold_db", "change_threshold_db", "max_slope_deg", "resolution_m"):
                raise GroundingError("Fact is associated with the wrong topic")
            if section.topic == "infrastructure" and fact.subject_type not in ("buildings", "roads", "bridges") and fact.field_name not in INFRA_FIELDS + INFRA_CONTEXT_FIELDS + INFRA_QUERY_FIELDS:
                raise GroundingError("Fact is associated with the wrong infrastructure topic")
            if section.topic == "connectivity" and fact.subject_type not in ("settlements", "destinations") and fact.field_name not in ACCESS_FIELDS + CONNECTIVITY_QUERY_FIELDS:
                raise GroundingError("Fact is associated with the wrong connectivity topic")
            if section.topic == "feature" and fact.subject_type in ("analysis", "observation"):
                raise GroundingError("Aggregate evidence is not feature evidence")
            if section.topic == "limitations" and fact.interpretation != "limitation":
                raise GroundingError("Measurement is not a limitation")
            if section.topic == "sources" and fact.interpretation not in ("recorded_metadata", "limitation", "unavailable"):
                raise GroundingError("Measurement is not a source")
            if section.topic == "inventory" and fact.field_name not in ("inventory_count", "name"):
                raise GroundingError("Exposure or access is not an inventory count")
        if section.narrative:
            selected = {claim.fact_id for claim in section.claims}
            narrated = set()
            for sentence in section.narrative:
                identifiers = re.findall(r'\{fact:([^{}]+)\}', sentence.text)
                if not identifiers or set(identifiers) != set(sentence.fact_ids) or not set(identifiers).issubset(selected):
                    raise GroundingError('Narrative must cite exactly its validated claims')
                prose = re.sub(r'\{fact:[^{}]+\}', '', sentence.text)
                if re.search(r'[\d{}<>]|\b(?:zero|one|two|three|four|five|hundred|thousand|million)\b', prose, re.I):
                    raise GroundingError('Narrative values, names and markup must come from fact placeholders')
                if re.search(r'\b(?:destroyed|dead|died|fatalit\w*|casualt\w*|population|safe|impassable|evacuat\w*|rescue|guarantee\w*)\b|confirmed (?:damage|closure|destruction)|no (?:damage|flood|water)|roads? (?:are )?(?:open|closed)|मृत|घाइते|जनसङ्ख्या|जनसंख्या|सुरक्षित|विनाश', prose, re.I):
                    raise GroundingError('Narrative contains an unsupported physical or operational claim')
                # A generated sentence cannot type a feature name beside a
                # different feature's facts, including plausible saved names.
                from app.feature_lookup import lookup
                if lookup(context, prose):
                    raise GroundingError('Narrative feature names must use fact placeholders')
                narrated.update(identifiers)
            if narrated != selected:
                raise GroundingError('Narrative omitted a selected claim')
    return draft


def format_value(fact, language="en", operation="exact"):
    i = language == "ne"
    if fact.status != "available":
        return "उपलब्ध छैन" if i else "Unavailable"
    value = fact.value
    if type(value) is bool:
        if fact.field_name != "exposed":
            return ("हो" if value else "होइन") if i else ("Yes" if value else "No")
        return ("सम्भावित प्रभाव; पुष्टि भएको क्षति होइन" if i else "Potential exposure; damage unconfirmed") if value else ("नक्सामा जुधेको भेटिएन; क्षति नभएको प्रमाण होइन" if i else "No mapped intersection; absence of damage is not established")
    if type(value) in (int, float):
        digits = int(operation[-1]) if operation != "exact" else {"km²": 3, "km": 3, "m": 2, "%": 1, "fraction": 3, "dB": 1, "degrees": 1}.get(fact.unit)
        number = str(value) if digits is None else f"{Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP):.{digits}f}"
        unit = {"km²": "वर्ग कि.मि.", "km": "कि.मि.", "m": "मिटर", "count": "", "degrees": "डिग्री"}.get(fact.unit, fact.unit or "") if i else ("" if fact.unit == "count" else fact.unit or "")
        return number + (" " + unit if unit else "")
    if isinstance(value, list):
        if fact.field_name in ("unavailable_hazards", "assessment_scope") and i:
            return "; ".join({"water": "डुबान", "debris": "भूआवरण परिवर्तन"}.get(v, v) for v in value) or "कुनै अभिलेख छैन"
        return "; ".join(str(v) for v in value) if value else ("कुनै अभिलेख छैन" if i else "No entries recorded")
    if value in STATUS_TEXT:
        return STATUS_TEXT[value][i]
    return str(value)


def fact_line(fact, language="en", operation="exact", *, compact=False):
    label = fact.label_ne if language == "ne" else fact.label_en
    subject = ""
    if fact.subject_type not in ("analysis", "observation"):
        subject = f" [{fact.subject_name or fact.subject_id} · {fact.subject_id}]"
    elif fact.subject_type == "analysis" and fact.subject_name and not compact:
        subject = f" [{fact.subject_name}]"
    line = f"{label}{subject}: {format_value(fact, language, operation)}."
    if not compact and fact.status != "available" and fact.missing_reason:
        line += " " + ("कारण: सम्बन्धित मापन वा गन्तव्य अनुसारको प्रमाण यस विश्लेषणमा उपलब्ध छैन।" if language == "ne" else fact.missing_reason)
    if not compact and fact.uncertainty and fact.uncertainty.startswith("The value covers only"):
        line += " " + ("यो मापन त्यस वर्गका लागि योग्य अवलोकन क्षेत्रमा मात्र लागू हुन्छ।" if language == "ne" else fact.uncertainty)
    return line + f" [{fact.fact_id}]"


def required_explanations(topic, facts):
    codes = set()
    if topic in ("flood", "summary"):
        codes.update(("surface_change_uncertain", "coverage_limits"))
    if topic in ("infrastructure", "summary") or any(f.subject_type in ("roads", "buildings", "bridges") for f in facts):
        codes.update(("potential_damage", "coverage_limits"))
    if topic in ("connectivity", "summary") or any(f.subject_type == "settlements" for f in facts):
        codes.update(("modeled_closure", "routing_limits", "coverage_limits"))
    if topic in ("limitations", "summary"):
        codes.add("satellite_limits")
    if any(f.status != "available" for f in facts):
        codes.add("missing_data")
    if any(f.field_name == "destination_type" or ".connectivity_by_destination_type." in f.path for f in facts):
        codes.add("destination_limits")
    return codes


def render_draft(draft, context, *, compact=False):
    i = draft.language == "ne"
    if draft.kind in ("unsupported", "clarification"):
        text, facts = (CLARIFICATION if draft.kind == "clarification" else UNSUPPORTED)[i], []
    else:
        paragraphs, facts = [], []
        for section in draft.sections:
            section_facts = [context.facts[claim.fact_id] for claim in section.claims]
            if section.narrative:
                operations = {claim.fact_id: claim.operation for claim in section.claims}
                lines = [re.sub(r'\{fact:([^{}]+)\}', lambda match: fact_line(context.facts[match.group(1)], draft.language, operations[match.group(1)], compact=compact).rstrip('.'), sentence.text)
                         for sentence in section.narrative]
            else:
                lines = [HEADINGS[section.topic][i]]
                lines.extend(fact_line(context.facts[claim.fact_id], draft.language, claim.operation) for claim in section.claims)
            if not compact:
                codes = required_explanations(section.topic, section_facts) | set(section.explanations)
                lines.extend(EXPLANATIONS[code][i] for code in EXPLANATIONS if code in codes)
            paragraphs.append("\n".join(lines))
            facts.extend(section_facts)
        text = "\n\n".join(paragraphs)
    if context.run["mode"] == "demo":
        text = ("कृत्रिम प्रदर्शन तथ्याङ्क — वास्तविक घटनाको नतिजा होइन।\n\n" if i else "SYNTHETIC DEMO — these are not observations of the real event.\n\n") + text
    if len(text) > 32000:
        raise GroundingError("Rendered answer exceeds the bounded output size")
    return text, list({f.fact_id: f for f in facts}.values())
