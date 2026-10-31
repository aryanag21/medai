import re

from app.patient_state.schema import ExtractedSymptom, SymptomExtractionResult
from app.providers.llm.base import LLMNotConfiguredError, LLMProvider

_SYSTEM_PROMPT = (
    "You extract structured symptom information from a patient's own words. "
    "Extract only what the patient actually said — never invent, infer beyond what is stated, "
    "or fill in a plausible-sounding value for anything not mentioned; leave that field null instead. "
    "Mark certainty as 'user_reported' for anything the patient stated directly. "
    "IMPORTANT: Only extract symptoms that the patient CURRENTLY experiences. "
    "Do NOT extract symptoms that the patient explicitly denies or states they do NOT have (e.g. 'no fever', 'no cough'). "
    "Do NOT extract symptoms that the patient states have resolved or ended (e.g. 'headache is gone', 'cough stopped'). "
    "This is data extraction, not diagnosis: do not suggest causes, conditions, or treatments."
)


def _build_prompt(user_messages: list[str]) -> str:
    transcript = "\n".join(f"- {m}" for m in user_messages)
    return (
        "Here is what the patient said, in order, across one conversation:\n"
        f"{transcript}\n\n"
        "Extract every distinct active, currently experienced symptom mentioned as a separate entry."
    )


def is_negated_or_resolved(text_lower: str, phrase: str) -> bool:
    """Checks whether a symptom phrase in the text is negated or reported as resolved."""
    # Check preceding negation: "no cough", "not having cough", "don't have cough", "without cough"
    neg_patterns = [
        rf"\b(no|not|without|denies|never|none|don\'t\s+have|dont\s+have|no\s+more|zero)\s+(?:any\s+)?{re.escape(phrase)}\b",
        rf"\b(no|not|without)\s+\w+\s+(?:or|nor)\s+{re.escape(phrase)}\b",
    ]
    for p in neg_patterns:
        if re.search(p, text_lower):
            return True

    # Check succeeding resolution: "cough is gone", "cough resolved", "cough stopped", "cough cleared up"
    res_patterns = [
        rf"\b{re.escape(phrase)}\s+(?:is|has)?\s*(?:gone|stopped|resolved|cleared\s+up|healed|ended|subsided|disappeared)\b",
        rf"\b{re.escape(phrase)}\s+went\s+away\b",
    ]
    for p in res_patterns:
        if re.search(p, text_lower):
            return True

    return False


def extract_symptoms_rule_based(user_messages: list[str]) -> list[ExtractedSymptom]:
    combined_text = " ".join(user_messages).lower()
    results: list[ExtractedSymptom] = []
    seen = set()

    keywords_map = [
        (["extreme cough", "severe cough", "dry cough", "wet cough", "coughing", "cough"], "cough"),
        (["extreme cold", "severe cold", "common cold", "cold"], "cold"),
        (["high fever", "fever", "feverish", "chills"], "fever"),
        (["headache", "migraine", "head ache"], "headache"),
        (["chest pain", "pain in chest", "chest tightness", "chest pressure"], "chest pain"),
        (["arm pain", "pain in left arm", "pain in my left arm", "sharp pain in left arm", "pain in arm"], "arm pain"),
        (["difficulty breathing", "shortness of breath", "trouble breathing", "cannot breathe", "breathless", "wheezing"], "shortness of breath"),
        (["sore throat", "throat pain", "pain swallowing", "scratchy throat"], "sore throat"),
        (["runny nose", "nasal congestion", "blocked nose", "stuffy nose", "congestion"], "nasal congestion"),
        (["body ache", "body pain", "muscle pain", "aches all over"], "body ache"),
        (["fatigue", "exhaustion", "feeling tired", "weakness"], "fatigue"),
        (["nausea", "feeling nauseous", "queasy"], "nausea"),
        (["vomiting", "throwing up", "threw up"], "vomiting"),
        (["dizziness", "dizzy", "lightheaded"], "dizziness"),
        (["abdominal pain", "stomach ache", "stomach pain", "cramps"], "abdominal pain"),
        (["diarrhea", "loose stools"], "diarrhea"),
    ]

    for phrases, sym_name in keywords_map:
        if sym_name in seen:
            continue
        for phrase in phrases:
            if phrase in combined_text:
                # Ensure the mention is not negated or reported resolved
                if is_negated_or_resolved(combined_text, phrase) or is_negated_or_resolved(combined_text, sym_name):
                    continue

                severity = None
                if any(w in combined_text for w in ["extreme", "severe", "unbearable", "very bad", "intense", "sharp"]):
                    severity = 8
                elif any(w in combined_text for w in ["moderate", "medium", "quite bad"]):
                    severity = 5
                elif any(w in combined_text for w in ["mild", "slight", "little"]):
                    severity = 2

                results.append(
                    ExtractedSymptom(
                        symptom=sym_name,
                        severity=severity,
                        certainty="user_reported",
                    )
                )
                seen.add(sym_name)
                break

    return results


async def extract_symptoms_from_conversation(
    llm: LLMProvider, user_messages: list[str]
) -> list[ExtractedSymptom]:
    """Phase 3: transcript -> structured symptom_extraction.fields. Per patient_state.rules,
    this is the ONLY place raw conversation text is handed to an LLM for interpretation in this
    phase — the result is structured data, not prose the rest of the system reasons over
    directly."""

    if not user_messages:
        return []

    try:
        result = await llm.structured_generate(
            _build_prompt(user_messages), schema=SymptomExtractionResult, system=_SYSTEM_PROMPT
        )
        if result.symptoms:
            # Filter out any hallucinated negated/resolved items from the LLM extraction
            filtered = []
            combined_text = " ".join(user_messages).lower()
            for s in result.symptoms:
                if not is_negated_or_resolved(combined_text, s.symptom.lower()):
                    filtered.append(s)
            return filtered
    except LLMNotConfiguredError:
        raise
    except Exception:
        pass

    # Fall back to deterministic clinical keyword extractor
    return extract_symptoms_rule_based(user_messages)
