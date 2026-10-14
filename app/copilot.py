"""Bilingual fallback routing; the configured LLM selects approved evidence tools.

No local intent model is trained. Neither the router nor the language model has
authority to supply counts: values and citations are resolved from saved facts.
"""
import re


COPILOT_INFO = {
    "component": "situation_report_copilot",
    "languages": ["en", "ne"],
    "algorithm": "Groq tool calling with validated facts; bilingual topic and contextual retrieval fallback",
    "external_model_weights": False,
    "external_training_datasets": False,
    "model_training": False,
    "fallback": "Deterministic bilingual topic routing and typed evidence rendering",
    "grounding": "Counts, areas, source dates and settlement names come from the saved analysis. Unsupported questions receive no estimate.",
}

# These rules select fallback topics, never values. They do not restrict which
# approved tools the configured generative model can select for other questions.
TOPIC_PATTERNS = {
    "summary": r"\b(?:summar\w*|briefing|overview|situation report)\b|सारांश|प्रतिवेदन|स्थिति विवरण|बाढीको अवस्था",
    "extent": r"\b(?:flood(?:ed|ing)?|inundat\w*|water|debris|surface.change|change mask|coverage|observed|observable|observation coverage)\b|डुबान|डुबेको|पानी|भग्नावशेष|अवलोकन क्षेत्र",
    "damage": r"\b(?:infrastructure|buildings?|structures?|homes?|bridges?|roads?|assets?|damage[ds]?)\b|पूर्वाधार|भवन|सडक|पुल|क्षति",
    "access": r"\b(?:settlements?|villages?|isolat\w*|cut.off|disconnect\w*|connectivity|road access|hospital|healthcare|routes?)\b|बस्ती|गाउँ|सम्पर्क|अस्पताल",
    "sources": r"\b(?:sources?|provenance|acquisitions?|snapshot|orbit|tracks?|satellite scenes?|data.*(?:use|from)|observations.*same|dates?.*(?:radar|satellite|maps?))\b|स्रोत|तथ्याङ्क प्रयोग|कक्षा|तस्बिर.*(?:कहिले|मिति)|नक्सा.*मिति|ओएसएम.*मिति",
    "limitations": r"\b(?:limitations?|reliab\w*|uncertain\w*|accura\w*|trust|confirmed?|destroyed|predict\w*|clouds?|shadow|layover)\b|सीमा|भरपर्दो|अनिश्चित|पुष्टि|पूर्वानुमान",
}
UNAVAILABLE_QUESTION = re.compile(
    r"\b(?:deaths?|dead|died|fatalit\w*|killed|casualt\w*|injur\w*|population|"
    r"missing (?:people|persons?|villagers?|residents?)|how many (?:people|residents?))\b|"
    r"मृत|घाइते|बेपत्ता|जनसङ्ख्या|जनसंख्या", re.I)
UNRELATED_QUESTION = re.compile(
    r"\b(?:weather|forecast|joke|election|restaurant|invent|fabricate|blame|culprit|responsible for|evacuat\w*|rescue priorit\w*)\b|मौसम|कथा|काल्पनिक|जिम्मेवार|दोषी|भोलि", re.I)


def unsupported_question(question):
    return bool(UNAVAILABLE_QUESTION.search(question) or UNRELATED_QUESTION.search(question))


def question_topics(question):
    if unsupported_question(question):
        return []
    return [intent for intent, pattern in TOPIC_PATTERNS.items() if re.search(pattern, question, re.I)]

def classify_question(question):
    if unsupported_question(question):
        return "unsupported", None
    topics = question_topics(question)
    if topics:
        # Uncertainty and provenance questions should not become exposure
        # assertions just because they mention a road or a satellite.
        intent = next((t for t in ("summary", "limitations", "sources", "access", "damage", "extent") if t in topics))
        return intent, None
    return "unsupported", None


def answer(run, question, language="en", history=None):
    from app.copilot_service import respond
    return respond(run, question, language, history)
