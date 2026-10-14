"""Resolve question scope using saved identifiers and bounded same-run history."""
from dataclasses import dataclass, field
import re
import unicodedata

from app.copilot import classify_question, question_topics, unsupported_question


TOPICS = {"extent": "flood", "damage": "infrastructure", "access": "connectivity",
          "sources": "sources", "limitations": "limitations", "summary": "summary"}
LAYERS = {
    "roads": r"\broads?\b|सडक",
    "bridges": r"\bbridges?\b|पुल",
    "buildings": r"\b(?:buildings?|structures?|homes?)\b|भवन",
    "settlements": r"\b(?:villages?|settlements?)\b|गाउँ|बस्ती",
}
REFERENCE = re.compile(r"\b(?:that|this|those|these|it|its|them|their|same)\b|त्यो|त्यस|उक्त|तिन|यस|यी", re.I)
GENERIC_FOLLOWUP = re.compile(r"^(?:and |also |what about |how much\??$|how many\??$|tell me more|more detail|translate|explain (?:that|it)|in (?:nepali|english))|^(?:त्यस|उक्त|थप|नेपालीमा|अंग्रेजीमा)", re.I)


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def mentions(context, text, layers=None):
    """Match complete names/IDs; a name shared by features stays ambiguous."""
    text = normalized(text)
    matches = []
    for (layer, identifier), feature in context.index.items():
        if layers and layer not in layers:
            continue
        names = (identifier, feature["properties"].get("name"))
        if any(isinstance(name, str) and name.strip() and re.search(
                r"(?<![\w/-])" + re.escape(normalized(name)) + r"(?![\w/-]|\.\w)", text)
               for name in names):
            matches.append((layer, identifier))
    return matches


@dataclass
class QueryPlan:
    intent: str
    confidence: float | None
    topics: list[str]
    question: str
    references: list[tuple[str, str]] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    road_id: str | None = None
    status: str = "cut_off"
    clarification: bool = False
    followup: bool = False
    requested_fields: list[str] = field(default_factory=list)

    @property
    def scoped(self):
        return bool(self.references or self.road_id or self.followup or self.status != "cut_off")

    def provider_context(self):
        return {"features": [{"layer": layer, "feature_id": identifier} for layer, identifier in self.references],
                "road_id": self.road_id, "settlement_status": self.status,
                "requested_fields": self.requested_fields}


def plan_question(context, question, history):
    if unsupported_question(question):
        return QueryPlan('unsupported', None, [], question)
    if re.search(r"^(?:translate|in (?:nepali|english)|नेपालीमा|अंग्रेजीमा)", question.strip(), re.I):
        for position in range(len(history) - 1, -1, -1):
            if history[position]['role'] == 'user':
                plan = plan_question(context, history[position]['content'], history[:position])
                plan.followup = True
                return plan
    intent, confidence = classify_question(question)
    matched_topics = question_topics(question)
    plan = QueryPlan(intent, confidence, [TOPICS[t] for t in matched_topics if t != "summary"], question)
    if unsupported_question(question):
        return plan
    plan.references = mentions(context, question)
    wanted_layers = [layer for layer, pattern in LAYERS.items() if re.search(pattern, question, re.I)]
    plan.assets = [layer for layer in wanted_layers if layer != "settlements"]
    refers_back = bool(REFERENCE.search(question))
    if not plan.references and refers_back:
        # Resolve the most recent relevant mention, without treating remembered
        # counts or prose as current evidence. Singular references require one ID.
        reference_layers = ["roads"] if "roads" in wanted_layers else wanted_layers
        for message in reversed(history):
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
                r"\b(?:that|this|those|these|same)\s+(?:road|bridge|building|village|settlement)s?\b(?!\s+(?:connectivity|assessment|exposure))|(?:त्यो|त्यस|उक्त).*?(?:सडक|पुल|भवन|गाउँ|बस्ती)", question, re.I):
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
    if roads and "settlements" in wanted_layers:
        if len(roads) == 1:
            plan.road_id = roads[0]
            plan.topics = ["connectivity"]
            plan.status = "all"
        else:
            plan.clarification = True
    elif plan.references:
        plan.topics = ["feature"]
    if re.search(r"\b(?:uncertain|unobserved)\b|अनिश्चित", question, re.I):
        plan.status = "uncertain_access"
    elif re.search(r"\b(?:unassessed|not assessed)\b|मूल्याङ्कन हुन नसके", question, re.I):
        plan.status = "unassessed"
    elif re.search(r"\b(?:connected|can reach|still reach|route available)\b|पुग्न सक्ने", question, re.I):
        plan.status = "connected"
    elif re.search(r"\b(?:cut.off|isolated|disconnected|lost|lose|loss)\b|सम्पर्कविहीन|सम्पर्क टुट", question, re.I):
        plan.status = "cut_off"
    elif "connectivity" in plan.topics and re.search(r"\b(?:all|status|statuses)\b", question, re.I):
        plan.status = "all"

    # Required facts provide a relevance check as well as grounded values.
    field_patterns = {
        "valid_coverage_pct": r"coverage|observed|observable|अवलोकन क्षेत्र",
        "optical_clear_pct": r"optical.*(?:clear|coverage)|cloud.free|बादलरहित",
        "water_km2": r"inundat|new water|flooded|डुबान|डुबेको",
        "debris_km2": r"debris|surface.change|भग्नावशेष",
    }
    if "flood" in plan.topics:
        plan.requested_fields = [name for name, pattern in field_patterns.items() if re.search(pattern, plan.question, re.I)]
    return plan
