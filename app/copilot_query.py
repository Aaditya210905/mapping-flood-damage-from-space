"""Resolve question scope using saved identifiers and bounded same-run history."""
from dataclasses import dataclass, field
import re
import unicodedata

from app.copilot import classify_question, question_topics, unsupported_question
from app.feature_lookup import lookup


TOPICS = {"extent": "flood", "damage": "infrastructure", "access": "connectivity",
          "sources": "sources", "limitations": "limitations", "summary": "summary"}
LAYERS = {
    "roads": r"\broads?\b|सडक",
    "bridges": r"\bbridges?\b|पुल",
    "buildings": r"\b(?:buildings?|structures?|homes?)\b|भवन",
    "settlements": r"\b(?:villages?|settlements?)\b|गाउँ|बस्ती",
}
REFERENCE = re.compile(r"\b(?:that|this|those|these|it|its|them|their|same)\b|\bthe (?:road|bridge|building|village|settlement)\b|त्यो|त्यस|उक्त|तिन|यस|यी", re.I)
GENERIC_FOLLOWUP = re.compile(r"^(?:and |also |what about |how much\??$|how many\??$|tell me more|more detail|translate|explain (?:that|it)|in (?:nepali|english))|^(?:त्यस|उक्त|थप|नेपालीमा|अंग्रेजीमा)", re.I)


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def mentions(context, text, layers=None):
    return lookup(context, text, layers)


@dataclass
class QueryPlan:
    intent: str
    confidence: float | None
    topics: list[str]
    question: str
    references: list[tuple[str, str]] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    road_id: str | None = None
    status: str = "all"
    clarification: bool = False
    followup: bool = False
    requested_fields: list[str] = field(default_factory=list)
    destination_type: str = "all"
    inventory_layer: str | None = None
    listing: bool = False
    unsupported_parts: list[str] = field(default_factory=list)

    @property
    def scoped(self):
        return bool(self.references or self.road_id or self.followup or self.status != "all" or self.destination_type != "all")

    def provider_context(self):
        return {"features": [{"layer": layer, "feature_id": identifier} for layer, identifier in self.references],
                "road_id": self.road_id, "settlement_status": self.status,
                "requested_fields": self.requested_fields, "destination_type": self.destination_type,
                "response_kind": "report" if self.intent == "summary" else "answer",
                "topics": self.topics, "inventory_layer": self.inventory_layer,
                "unsupported_parts": self.unsupported_parts}


def plan_question(context, question, history):
    rejected = []
    if unsupported_question(question):
        clauses = re.split(r"\s*(?:;|\?|\band\b|\balso\b|\bas well as\b|साथै)\s*", question, flags=re.I)
        supported = [part for part in clauses if part.strip() and not unsupported_question(part) and question_topics(part)]
        rejected = [part for part in clauses if part.strip() and unsupported_question(part)]
        if not supported:
            return QueryPlan('unsupported', None, [], question, unsupported_parts=rejected or [question])
        question = " and ".join(supported)
    if re.search(r"^(?:translate|in (?:nepali|english)|नेपालीमा|अंग्रेजीमा)", question.strip(), re.I):
        for position in range(len(history) - 1, -1, -1):
            if history[position]['role'] == 'user':
                plan = plan_question(context, history[position]['content'], history[:position])
                plan.followup = True
                return plan
    intent, confidence = classify_question(question)
    matched_topics = question_topics(question)
    plan = QueryPlan(intent, confidence, [TOPICS[t] for t in matched_topics if t != "summary"], question)
    plan.unsupported_parts = rejected
    plan.listing = bool(re.search(r"\b(?:which|list|show|name)\b|कुन|सूची", question, re.I)) and plan.intent != "summary"
    if unsupported_question(question):
        return plan
    plan.references = mentions(context, question)
    if len(plan.references) > 1:
        names = [normalized(context.index[subject]['properties'].get('name', '')) for subject in plan.references]
        if len(names) != len(set(names)):
            plan.clarification = True
    wanted_layers = [layer for layer, pattern in LAYERS.items() if re.search(pattern, question, re.I)]
    plan.assets = [layer for layer in wanted_layers if layer != "settlements"]
    refers_back = bool(REFERENCE.search(question))
    if not plan.references and refers_back:
        # Resolve the most recent relevant mention, without treating remembered
        # counts or prose as current evidence. Singular references require one ID.
        reference_layers = ["roads"] if "roads" in wanted_layers else wanted_layers
        for message in reversed(history):
            if message["role"] != "user":
                continue
            candidates = mentions(context, message["content"], reference_layers)
            if candidates:
                plural = bool(re.search(r"\b(?:those|these|them|their)\b|तिन|यी", question, re.I))
                if len(candidates) == 1 or plural:
                    plan.references = candidates
                else:
                    plan.clarification = True
                plan.followup = True
                break
        if not plan.references and wanted_layers and re.search(
                r"\b(?:that|this|those|these|same|the)\s+(?:road|bridge|building|village|settlement)s?\b(?!\s+(?:connectivity|assessment|exposure))|(?:त्यो|त्यस|उक्त).*?(?:सडक|पुल|भवन|गाउँ|बस्ती)", question, re.I):
            plan.clarification = True
    if not matched_topics and (refers_back or GENERIC_FOLLOWUP.search(question)):
        for message in reversed(history):
            if message["role"] != "user" or unsupported_question(message["content"]):
                continue
            previous_topics = question_topics(message["content"])
            if previous_topics:
                plan.intent, plan.confidence = classify_question(message["content"])
                plan.topics = [TOPICS[t] for t in previous_topics if t != "summary"]
                plan.question = message["content"] + "\n" + question
                plan.followup = True
                break
        if not plan.topics and not plan.references and re.fullmatch(
                r"(?:how much|how many|what about (?:it|them)|explain (?:that|it))[?.!]*", question.strip(), re.I):
            plan.clarification = True
    if plan.intent == "summary":
        plan.topics = ["flood", "infrastructure", "connectivity", "sources", "limitations"]
    elif not plan.topics and plan.intent in TOPICS:
        plan.topics = [TOPICS[plan.intent]]

    roads = [identifier for layer, identifier in plan.references if layer == "roads"]
    if not roads and 'roads' in wanted_layers and 'settlements' in wanted_layers and re.search(
            r'\b(?:on|along|via|using|use|rely|depend\w*)\b|उक्त|त्यस', question, re.I):
        plan.clarification = True
    if roads and "settlements" in wanted_layers:
        if len(roads) == 1:
            plan.road_id = roads[0]
            plan.topics = ["connectivity"]
            plan.status = "all"
        else:
            plan.clarification = True
    elif plan.references:
        plan.topics = ["feature"]
    if re.search(r"\b(?:uncertain|unobserved|no confirmed access|unconfirmed access)\b|अनिश्चित", question, re.I):
        plan.status = "uncertain_access"
    elif re.search(r"\b(?:unassessed|not assessed)\b|मूल्याङ्कन हुन नसके", question, re.I):
        plan.status = "unassessed"
    elif re.search(r"\b(?:connected|can reach|still reach|route available)\b|पुग्न सक्ने", question, re.I):
        plan.status = "connected"
    elif re.search(r"\b(?:cut.off|isolated|disconnected|lost|lose|loss)\b|सम्पर्कविहीन|सम्पर्क टुट", question, re.I):
        plan.status = "cut_off"
    elif "connectivity" in plan.topics and re.search(r"\b(?:all|status|statuses)\b", question, re.I):
        plan.status = "all"

    if re.search(r"\b(?:hospitals?|healthcare)\b|अस्पताल", plan.question, re.I):
        plan.destination_type = "hospital"
    elif re.search(r"\b(?:towns?|cities|city)\b|नगर|सहर|शहर", plan.question, re.I):
        plan.destination_type = "town"
    if plan.destination_type != "all" and "connectivity" in plan.topics:
        plan.assets = []
    inventory = re.search(r"\b(?:mapped|inventory|total|how many|number of|count)\b|कति.*(?:नक्सा|अस्पताल)|नक्सामा", question, re.I)
    impacts = re.search(r"\b(?:exposed|exposure|damaged?|affected|hit|impacts?|overlap|reach|access|connect\w*|route|cut.off|isolat\w*)\b|प्रभाव|क्षति|पहुँच|सम्पर्क", question, re.I)
    if inventory and not impacts and not plan.references:
        if plan.destination_type != "all":
            plan.inventory_layer = "destinations"
        elif wanted_layers:
            plan.inventory_layer = wanted_layers[0]
        if plan.inventory_layer:
            plan.topics = ["inventory"]
            plan.intent = "inventory"
    if not plan.references and not plan.road_id and re.search(
            r"\b(?:is|about|status of|for|from)\s+[A-Z][\w .'-]*?\s+(?:village|settlement|road|bridge|building)\b|"
            r"\b(?:road|bridge|settlement|building)\s+(?:[rsb]-\d+|(?:way|node)/\d+)\b", question):
        plan.clarification = True

    # Required facts provide a relevance check as well as grounded values.
    field_patterns = {
        "water_km2": r"inundat|new.water|flooded|water.*(?:area|extent|large|much)|डुबान|डुबेको",
        "debris_km2": r"debris|surface.change|भग्नावशेष",
        "water_threshold_db": r"water.*threshold|backscatter.*threshold",
        "change_threshold_db": r"(?:change|drop).*threshold",
        "max_slope_deg": r"slope.*(?:limit|threshold)|maximum slope",
        "resolution_m": r"resolution|pixel size|grid size",
    }
    if "flood" in plan.topics:
        plan.requested_fields = [name for name, pattern in field_patterns.items() if re.search(pattern, plan.question, re.I)]
        coverage_marker=r"coverage|observed|observable|eligible|eligibility|assessable|cloud.free|अवलोकन क्षेत्र|बादलरहित|योग्य.*क्षेत्र"
        kinds={
            'radar_valid_coverage_pct':r'radar|sentinel.1|\bS1\b|राडार',
            'optical_clear_pct':r'optical|cloud.free|sentinel.2|बादलरहित',
            'water_eligible_coverage_pct':r'water|डुबान|पानी',
            'debris_eligible_coverage_pct':r'debris|भग्नावशेष',
        }
        pending=[]
        for clause in re.split(r"[;?!]|\band\b|\balso\b|साथै| र ",plan.question,flags=re.I):
            named=[name for name,pattern in kinds.items() if re.search(pattern,clause,re.I)]
            if not re.search(coverage_marker,clause,re.I):
                if not any(re.search(pattern,clause,re.I) for pattern in field_patterns.values()):pending.extend(named)
                continue
            selected=pending+named
            pending=[]
            if not selected and re.search(r'eligible|eligibility|assessable|योग्य',clause,re.I):
                # A compound water-area question can qualify eligibility in
                # its following clause without repeating the word water.
                selected=[name for name in ('water_eligible_coverage_pct','debris_eligible_coverage_pct') if re.search(kinds[name],plan.question,re.I)]
            plan.requested_fields.extend(selected or ['valid_coverage_pct'])
        plan.requested_fields=list(dict.fromkeys(plan.requested_fields))
        if ('debris_eligible_coverage_pct' in plan.requested_fields
                and not re.search(r'debris.*(?:area|extent)|(?:area|extent|how much).*debris|भग्नावशेष.*क्षेत्र',plan.question,re.I)):
            plan.requested_fields=[field for field in plan.requested_fields if field!='debris_km2']
        if not plan.requested_fields:
            plan.requested_fields = ["water_km2"] if re.search(r"extent|flood|बाढी", plan.question, re.I) else []
    return plan
