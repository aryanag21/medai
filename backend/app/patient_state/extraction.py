from app.patient_state.schema import ExtractedSymptom, SymptomExtractionResult
from app.providers.llm.base import LLMProvider

_SYSTEM_PROMPT = (
    "You extract structured symptom information from a patient's own words. "
    "Extract only what the patient actually said — never invent, infer beyond what is stated, "
    "or fill in a plausible-sounding value for anything not mentioned; leave that field null "
    "instead. Mark certainty as 'user_reported' for anything the patient stated directly. "
    "This is data extraction, not diagnosis: do not suggest causes, conditions, or treatments."
)


def _build_prompt(user_messages: list[str]) -> str:
    transcript = "\n".join(f"- {m}" for m in user_messages)
    return (
        "Here is what the patient said, in order, across one conversation:\n"
        f"{transcript}\n\n"
        "Extract every distinct symptom mentioned as a separate entry."
    )


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
            return result.symptoms
    except Exception:
        pass

    # Fall back to deterministic clinical keyword extractor
    return extract_symptoms_rule_based(user_messages)
