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
    clarification_reason: str | None = None
    followup: bool = False
    requested_fields: list[str] = field(default_factory=list)
    destination_type: str = "all"
    inventory_layer: str | None = None
    inventory_layers: list[str] = field(default_factory=list)
    listing: bool = False
    exposure: str = 'exposed'
    unsupported_parts: list[str] = field(default_factory=list)

    @property
    def scoped(self):
        return bool(self.references or self.road_id or self.followup or self.status != "all" or self.destination_type != "all" or self.exposure != 'exposed')

    def connectivity_arguments(self, offset=0, limit=5):
        return {'status': self.status, 'road_id': self.road_id,
                'destination_type': self.destination_type, 'offset': offset, 'limit': limit}

    def infrastructure_arguments(self, asset, offset=0, limit=5):
        arguments = {'asset_type': asset, 'only_exposed': self.exposure == 'exposed',
                     'offset': offset, 'limit': limit}
        if self.listing or self.exposure != 'exposed':
            arguments['exposure'] = self.exposure
        return arguments

    @property
    def inventory_targets(self):
        return self.inventory_layers or ([self.inventory_layer] if self.inventory_layer else [])

    def provider_context(self):
        return {"features": [{"layer": layer, "feature_id": identifier} for layer, identifier in self.references],
                "road_id": self.road_id, "settlement_status": self.status,
                "requested_fields": self.requested_fields, "destination_type": self.destination_type,
                "exposure": self.exposure, "listing": self.listing,
                "response_kind": "report" if self.intent == "summary" else "answer",
                "topics": self.topics, "inventory_layer": self.inventory_layer,
                "inventory_layers": self.inventory_targets,
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
    # Separate explicit asset and settlement clauses before interpreting their
    # predicates (e.g. unassessed roads AND connected villages).
    clauses = re.split(r';|\band\b|साथै', question, flags=re.I)
    asset_clauses = [part for part in clauses if any(re.search(LAYERS[layer], part, re.I) for layer in ('roads', 'bridges', 'buildings'))]
    access_clauses = [part for part in clauses if re.search(LAYERS['settlements'], part, re.I)]
    status_question = ' and '.join(access_clauses) if asset_clauses and access_clauses else question
    asset_question = ' and '.join(asset_clauses) if asset_clauses and access_clauses else question
    access_negative = r"\b(?:(?:not|never)\s+(?:still\s+)?connected|(?:cannot|can't|can not|unable to|do not|don't)\s+reach|without\s+(?:road\s+)?access)\b|पुग्न नसक्ने|सम्पर्क नभएका"
    disconnected = r"\b(?:cut.off|isolated|disconnected|lost|lose|loss)\b|सम्पर्कविहीन|सम्पर्क टुट"
    before_event = bool(re.search(r'\b(?:before (?:the )?(?:flood|event)|pre.event|baseline|already)\b|घटनाअघि|बाढीअघि', status_question, re.I))
    if re.search(r"\bnot\s+(?:disconnected|isolated|cut.off)\b", status_question, re.I):
        plan.status = 'baseline_connected' if before_event else 'connected'
    elif before_event and (re.search(disconnected, status_question, re.I) or re.search(access_negative, status_question, re.I)):
        plan.status = 'baseline_disconnected'
    elif re.search(access_negative, status_question, re.I):
        plan.status = 'not_connected'
    elif re.search(r"\b(?:uncertain|unobserved|no confirmed access|unconfirmed access)\b|अनिश्चित", status_question, re.I):
        plan.status = "uncertain_access"
    elif re.search(r"\b(?:unassessed|not assessed)\b|मूल्याङ्कन हुन नसके", status_question, re.I):
        plan.status = "unassessed"
    elif re.search(r"\b(?:connected|can reach|still reach|route available)\b|पुग्न सक्ने", status_question, re.I):
        plan.status = 'baseline_connected' if before_event else 'connected'
    elif re.search(disconnected, status_question, re.I):
        plan.status = "cut_off"
    elif "connectivity" in plan.topics and re.search(r"\b(?:all|status|statuses)\b", question, re.I):
        plan.status = "all"

    destinations = [kind for kind, profile in context.destination_profiles.items()
                    if any(re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)' if keyword.isascii() else re.escape(keyword),
                                     plan.question, re.I) for keyword in profile.keywords)]
    if len(destinations) > 1:
        plan.destination_type = 'all'
        # The combined network models reaching either target. Requiring both
        # targets, or different predicates for each, needs separate queries.
        if re.search(r'\bboth\b|दुवै|\bbut not\b', question, re.I) or set(destinations) != set(context.destination_profiles):
            plan.clarification = True
            plan.clarification_reason = 'destination_combination'
    elif destinations:
        plan.destination_type = destinations[0]
    if 'settlements' in wanted_layers and plan.status != 'all' and not plan.references:
        # Flood/event words qualify the graph epoch; they do not request area.
        temporal_only = re.sub(r'\b(?:before|after|since|during)\s+(?:the\s+)?(?:flood|event)\b', '', question, flags=re.I)
        plan.topics = ['connectivity'] + [TOPICS[t] for t in question_topics(temporal_only)
                                         if t not in ('access', 'summary') and (t != 'damage' or plan.assets)]
        plan.topics = list(dict.fromkeys(plan.topics))
        plan.intent = 'compound' if len(plan.topics) > 1 else 'access'
    if plan.assets and not plan.references:
        if re.search(r'\b(?:(?:not|never)\s+(?:potentially\s+)?(?:exposed|affected)|unexposed|unaffected|without (?:mapped )?(?:exposure|impacts?))\b|प्रभावित छैन|प्रभाव नपरे', asset_question, re.I):
            plan.exposure = 'not_exposed'
        elif re.search(r'\b(?:unassessed|not assessed|unobserved|unknown coverage)\b|मूल्याङ्कन हुन नसके', asset_question, re.I):
            plan.exposure = 'unassessed'
        if plan.exposure != 'exposed':
            plan.topics = list(dict.fromkeys(['infrastructure'] + ([topic for topic in plan.topics if topic != 'infrastructure'] if 'settlements' in wanted_layers else [])))
            plan.intent = 'compound' if len(plan.topics) > 1 else 'damage'
    inventory = re.search(r"\b(?:mapped|inventory|total|how many|number of|count)\b|कति.*(?:नक्सा|अस्पताल)|नक्सामा", question, re.I)
    impacts = re.search(r"\b(?:exposed|exposure|damaged?|affected|hit|impacts?|overlap|reach|access|connect\w*|route|cut.off|isolat\w*)\b|प्रभाव|क्षति|पहुँच|सम्पर्क", question, re.I)
    if inventory and not impacts and not plan.references and plan.exposure == 'exposed' and plan.status == 'all':
        plan.inventory_layers = list(wanted_layers)
        if destinations:
            plan.inventory_layers.append('destinations')
        if plan.inventory_layers:
            plan.inventory_layer = plan.inventory_layers[0]
        if plan.inventory_layer:
            plan.topics = ['inventory'] + [topic for topic in plan.topics if topic not in ('inventory', 'infrastructure', 'connectivity')]
            plan.intent = 'compound' if len(plan.topics) > 1 else 'inventory'
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
