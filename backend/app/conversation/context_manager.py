"""Intelligent Context Management & Clinical Relevance Engine for MEDAI.

Implements Part 2, Part 3, and Part 6 of the Medical Assistant AI architecture:
1. Distinguishes Immediate Conversation Context, Relevant Medical Memory, and Irrelevant/Resolved History.
2. Performs relevance-based retrieval: historical medical memory is incorporated ONLY when it
   materially improves response quality or affects patient safety.
3. Dynamically tracks active vs. resolved symptoms without hardcoded keyword silos.
4. Detects discrepancies and contradictory information rather than silently overwriting.
5. Classifies message intent (emergency, simple question, symptom clarification, greeting, resolution)
   to drive adaptive response formatting and eliminate repetition.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.patient_state.schema import PatientState

logger = logging.getLogger(__name__)

# Clinical domains for medical relevance mapping
CLINICAL_DOMAINS = {
    "respiratory": {
        "symptoms": {
            "cough", "cold", "congestion", "phlegm", "mucus", "runny nose", "sneeze", "sneezing",
            "sore throat", "throat pain", "shortness of breath", "difficulty breathing", "wheezing",
            "stridor", "chest congestion", "bronchitis", "sinus", "hoarseness",
        },
        "conditions": {"asthma", "copd", "bronchitis", "pneumonia", "allergies", "allergic rhinitis", "sinusitis"},
        "medications": {"albuterol", "fluticasone", "montelukast", "cetirizine", "loratadine", "dextromethorphan", "guaifenesin", "pseudoephedrine"},
    },
    "cardiovascular": {
        "symptoms": {
            "chest pain", "chest tightness", "chest pressure", "palpitations", "racing heart",
            "shortness of breath", "dizziness", "lightheadedness", "fainting", "syncope",
            "leg swelling", "ankle swelling", "edema",
        },
        "conditions": {"hypertension", "high blood pressure", "coronary artery disease", "heart failure", "arrhythmia", "atrial fibrillation"},
        "medications": {"lisinopril", "amlodipine", "metoprolol", "losartan", "atorvastatin", "aspirin", "furosemide"},
    },
    "neurological": {
        "symptoms": {
            "headache", "migraine", "head pain", "throbbing head", "dizziness", "vertigo",
            "lightheadedness", "numbness", "tingling", "weakness", "confusion", "vision changes",
            "blurred vision", "sensitivity to light", "photophobia", "neck stiffness",
        },
        "conditions": {"migraine", "tension headaches", "epilepsy", "neuropathy", "stroke", "tia"},
        "medications": {"sumatriptan", "topiramate", "gabapentin", "propranolol"},
    },
    "gastrointestinal": {
        "symptoms": {
            "stomach ache", "stomach pain", "abdominal pain", "belly pain", "cramps", "nausea",
            "vomiting", "throwing up", "diarrhea", "loose stools", "constipation", "heartburn",
            "acid reflux", "indigestion", "bloating", "loss of appetite",
        },
        "conditions": {"gerd", "acid reflux", "gastritis", "peptic ulcer", "ibs", "crohns", "celiac"},
        "medications": {"omeprazole", "pantoprazole", "famotidine", "ranitidine", "antacid", "loperamide", "ondansetron"},
    },
    "musculoskeletal_trauma": {
        "symptoms": {
            "back pain", "lower back pain", "neck pain", "joint pain", "knee pain", "knee", "shoulder pain", "shoulder", "ankle", "foot", "toe", "leg", "arm", "wrist", "hip", "stiff", "stiffness",
            "muscle ache", "muscle strain", "sprain", "bruise", "contusion", "swelling",
            "stubbed toe", "hit foot", "door slam", "fall", "injury", "fracture", "stiffness",
        },
        "conditions": {"arthritis", "osteoarthritis", "rheumatoid arthritis", "chronic back pain", "sciatica", "fibromyalgia"},
        "medications": {"ibuprofen", "naproxen", "paracetamol", "acetaminophen", "diclofenac", "cyclobenzaprine", "meloxicam"},
    },
    "dermatological": {
        "symptoms": {
            "rash", "itching", "itchy", "hives", "urticaria", "redness", "skin lesion",
            "bump", "blister", "dry skin", "eczema flare", "insect bite", "peeling",
        },
        "conditions": {"eczema", "psoriasis", "dermatitis", "urticaria"},
        "medications": {"hydrocortisone", "triamcinolone", "diphenhydramine", "cetirizine"},
    },
    "infectious_constitutional": {
        "symptoms": {
            "fever", "chills", "feverish", "high temperature", "fatigue", "exhaustion",
            "body aches", "night sweats", "malaise", "weakness",
        },
        "conditions": {"infection", "influenza", "covid", "mononucleosis"},
        "medications": {"paracetamol", "acetaminophen", "ibuprofen", "amoxicillin", "azithromycin"},
    },
}

# Known common drug adverse effects for clinical correlation
MEDICATION_SIDE_EFFECTS = {
    "lisinopril": ["cough", "dry cough", "dizziness", "lightheadedness", "headache"],
    "amlodipine": ["swelling", "edema", "ankle swelling", "dizziness", "headache", "flushing"],
    "metoprolol": ["fatigue", "dizziness", "lightheadedness", "slow heart rate", "cold hands"],
    "losartan": ["dizziness", "nasal congestion", "back pain", "fatigue"],
    "ibuprofen": ["stomach pain", "heartburn", "nausea", "indigestion", "dizziness"],
    "naproxen": ["stomach pain", "heartburn", "nausea", "dizziness"],
    "metformin": ["nausea", "diarrhea", "stomach pain", "bloating", "loss of appetite"],
    "atorvastatin": ["muscle pain", "muscle ache", "joint pain", "fatigue"],
}

# Emergency red-flag patterns
EMERGENCY_PATTERNS = [
    r"\b(crush|crushing|radiat|radiating|pressure|tight|tightness|heavy|heaviness)\b.*\b(chest|heart)\b",
    r"\b(chest|heart)\b.*\b(crush|crushed|tight|tightness|pressure|heavy|pain|hurts)\b",
    r"\b(difficulty\s+breathing|cannot\s+breathe|can\'t\s+breathe|unable\s+to\s+breathe|struggling\s+to\s+breathe|gasping)\b",
    r"\b(coughing\s+up\s+blood|hemoptysis)\b",
    r"\b(slurred\s+speech|facial\s+droop|face\s+drooping|stroke|paralysis|sudden\s+weakness|loss\s+of\s+vision)\b",
    r"\b(unconscious|passed\s+out|syncope|unresponsive)\b",
    r"\b(anaphylaxis|swelling\s+of\s+(my\s+)?(tongue|throat|lips)|airway\s+closing|cannot\s+swallow)\b",
]
_COMPILED_EMERGENCY = [re.compile(p, re.IGNORECASE) for p in EMERGENCY_PATTERNS]


def detect_emergency(message: str) -> tuple[bool, str | None]:
    """Scans message for acute life-threatening medical emergencies."""
    for pattern in _COMPILED_EMERGENCY:
        if pattern.search(message):
            return True, f"Emergency red flag detected: {pattern.pattern}"
    return False, None


def extract_domains_from_text(text: str) -> set[str]:
    """Identifies clinical domains touched by text."""
    text_lower = text.lower()
    matched_domains = set()
    for domain, data in CLINICAL_DOMAINS.items():
        if any(sym in text_lower for sym in data["symptoms"]):
            matched_domains.add(domain)
        elif any(cond in text_lower for cond in data["conditions"]):
            matched_domains.add(domain)
    return matched_domains


def detect_resolved_symptoms(message: str, active_symptoms: list[str]) -> list[str]:
    """Detects whether the patient is stating that a symptom has resolved or ended.

    Works dynamically for any active symptom name rather than using hardcoded string lists.
    """
    msg_l = message.lower()
    resolved: list[str] = []

    resolution_phrases = [
        "is gone", "has gone", "went away", "stopped", "cleared up", "healed",
        "better now", "resolved", "no more", "not having", "don't have", "dont have",
        "not bothering me anymore", "completely gone", "disappeared", "all good now",
    ]

    for sym in active_symptoms:
        sym_l = sym.lower()
        # Direct mention near resolution phrase
        if any(f"{sym_l} {p}" in msg_l for p in ["is gone", "has gone", "stopped", "cleared up", "healed", "went away", "resolved"]):
            resolved.append(sym)
            continue
        if any(f"{p} {sym_l}" in msg_l for p in ["no more", "no longer have", "don't have", "dont have", "stopped having"]):
            resolved.append(sym)
            continue
        # Also check general recovery if symptom is mentioned in sentence
        if sym_l in msg_l and any(p in msg_l for p in resolution_phrases):
            resolved.append(sym)

    # General recovery phrases where all recent symptoms might be resolved
    if any(p in msg_l for p in ["i feel completely better", "all my symptoms are gone", "everything cleared up", "i'm all healed"]):
        resolved.extend(active_symptoms)

    return list(dict.fromkeys(resolved))


def classify_message_intent(
    latest_message: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Classifies the patient's incoming message intent to drive response style and context priority.

    Returns:
        dict with keys:
            - intent: "EMERGENCY" | "GREETING_OR_CLOSER" | "SIMPLE_QUESTION" | "SYMPTOM_UPDATE" | "RESOLUTION" | "NEW_CONCERN" | "GENERAL"
            - is_emergency: bool
            - is_simple_question: bool
            - is_greeting: bool
            - is_closer: bool
            - is_medication_query: bool
            - is_update: bool
            - summary: str
    """
    text = latest_message.strip()
    text_l = text.lower()
    history = conversation_history or []

    # 1. Emergency
    is_emerg, reason = detect_emergency(text)
    if is_emerg:
        return {
            "intent": "EMERGENCY",
            "is_emergency": True,
            "is_simple_question": False,
            "is_greeting": False,
            "is_closer": False,
            "is_medication_query": False,
            "is_update": False,
            "emergency_reason": reason,
            "summary": "Acute medical emergency requiring immediate urgent guidance",
        }

    # 2. Greeting or Closing
    closers = {"thank you", "thanks", "thx", "ok", "okay", "got it", "understood", "alright", "great", "perfect", "bye", "goodbye"}
    greetings = {"hello", "hi", "hey", "good morning", "good afternoon", "good evening"}

    clean_words = re.sub(r"[^\w\s]", "", text_l).strip()
    if clean_words in closers or (any(c in text_l for c in ["thank", "thanks", "appreciate"]) and not any(w in text_l for w in ["pain", "hurt", "fever", "cough", "ache", "symptom"])):
        return {
            "intent": "GREETING_OR_CLOSER",
            "is_emergency": False,
            "is_simple_question": False,
            "is_greeting": False,
            "is_closer": True,
            "is_medication_query": False,
            "is_update": False,
            "summary": "Polite acknowledgment or closing",
        }

    if clean_words in greetings or (len(clean_words.split()) <= 2 and any(g in clean_words for g in greetings)):
        return {
            "intent": "GREETING_OR_CLOSER",
            "is_emergency": False,
            "is_simple_question": False,
            "is_greeting": True,
            "is_closer": False,
            "is_medication_query": False,
            "is_update": False,
            "summary": "Greeting or conversation opening",
        }

    # 3. Check for symptom resolution statement
    if any(p in text_l for p in ["is gone", "went away", "stopped hurting", "cleared up", "no more", "healed", "feel completely better"]):
        return {
            "intent": "RESOLUTION",
            "is_emergency": False,
            "is_simple_question": False,
            "is_greeting": False,
            "is_closer": False,
            "is_medication_query": False,
            "is_update": True,
            "summary": "Reporting resolution of a prior symptom",
        }

    # 4. Medication query check
    is_med_query = any(k in text_l for k in [
        "medicine", "medication", "pill", "tablet", "syrup", "can i take", "should i take",
        "dosage", "dose", "paracetamol", "ibuprofen", "aspirin", "antibiotic", "prescription",
        "side effect", "adverse effect", "interact",
    ])

    # 5. Check if it is a simple informational / direct question
    is_direct_q = text.endswith("?") or any(text_l.startswith(w) for w in ["what", "how", "can i", "is it safe", "why", "when", "does", "should i"]) or any(w in text_l for w in ["what is", "how to", "can i take", "is it safe to"])
    is_short = len(text.split()) <= 15
    has_no_symptom_complaint = not any(w in text_l for w in ["i have", "i feel", "i'm having", "hurts", "pain", "suffering", "started", "severe"])

    if is_direct_q and is_short and has_no_symptom_complaint:
        return {
            "intent": "SIMPLE_QUESTION",
            "is_emergency": False,
            "is_simple_question": True,
            "is_greeting": False,
            "is_closer": False,
            "is_medication_query": is_med_query,
            "is_update": False,
            "summary": "Direct, focused question requiring a concise answer",
        }

    # 6. Check if it is an update/clarification following an assistant question
    last_assistant_msg = ""
    for m in reversed(history):
        if m.get("role") == "assistant":
            last_assistant_msg = m.get("content", "").strip()
            break

    is_short_answer = len(text.split()) <= 10
    time_or_severity_pattern = bool(re.search(r"\b(\d+\s*(day|days|week|weeks|hour|hours|month|months)|mild|moderate|severe|sharp|dull|throbbing)\b", text_l))

    if last_assistant_msg and (is_short_answer or time_or_severity_pattern) and ("?" in last_assistant_msg or "how long" in last_assistant_msg.lower() or "where" in last_assistant_msg.lower()):
        return {
            "intent": "SYMPTOM_UPDATE",
            "is_emergency": False,
            "is_simple_question": False,
            "is_greeting": False,
            "is_closer": False,
            "is_medication_query": is_med_query,
            "is_update": True,
            "summary": "Providing specific clarification or details to previous question",
        }

    # 7. Check if user is reporting a new concern vs continuing
    current_domains = extract_domains_from_text(text)
    prior_user_texts = [m.get("content", "") for m in history if m.get("role") == "user"]
    prior_domains = set()
    for pt in prior_user_texts:
        prior_domains.update(extract_domains_from_text(pt))

    if current_domains and not (current_domains & prior_domains) and len(prior_user_texts) >= 2:
        return {
            "intent": "NEW_CONCERN",
            "is_emergency": False,
            "is_simple_question": False,
            "is_greeting": False,
            "is_closer": False,
            "is_medication_query": is_med_query,
            "is_update": False,
            "summary": "Introducing a new, distinct medical concern",
        }

    return {
        "intent": "GENERAL",
        "is_emergency": False,
        "is_simple_question": False,
        "is_greeting": False,
        "is_closer": False,
        "is_medication_query": is_med_query,
        "is_update": False,
        "summary": "Ongoing clinical discussion",
    }


def filter_relevant_memory(
    latest_message: str,
    intent: dict[str, Any],
    state: PatientState,
    resolved_symptoms: list[str] | None = None,
) -> dict[str, Any]:
    """Intelligently filters medical memory based on clinical relevance to the current message.

    Implements:
    - Part 2 B: Relevant medical memory (included ONLY when it materially affects the response).
    - Part 2 C: Irrelevant, resolved, or outdated information (suppressed from active focus).
    - Discrepancy detection between current user statement and records.
    """
    resolved_set = set(s.lower() for s in (resolved_symptoms or []))
    msg_l = latest_message.lower()
    current_domains = extract_domains_from_text(latest_message)
    is_med_query = intent.get("is_medication_query", False)

    # 1. Filter Symptoms: Active vs. Resolved vs. Clinically Relevant
    all_symptoms = [s.symptom for s in getattr(state, "symptoms", [])]
    # Deduplicate while preserving order
    all_symptoms = list(dict.fromkeys(all_symptoms))

    active_relevant_symptoms: list[str] = []
    active_unrelated_symptoms: list[str] = []
    historical_resolved: list[str] = []

    for sym in all_symptoms:
        sym_l = sym.lower()
        if sym_l in resolved_set:
            historical_resolved.append(sym)
            continue

        # Check if symptom is in the same clinical domain or mentioned in the latest message
        sym_domains = extract_domains_from_text(sym)
        shares_term = any(len(w) > 3 and w in msg_l for w in sym_l.split())
        if sym_l in msg_l or shares_term or (current_domains and (sym_domains & current_domains)):
            active_relevant_symptoms.append(sym)
        else:
            # Check if there is a known cross-domain syndrome
            # (e.g. fever + any infection; headache + nausea)
            if "infectious_constitutional" in current_domains or "infectious_constitutional" in sym_domains:
                active_relevant_symptoms.append(sym)
            elif "neurological" in current_domains and ("gastrointestinal" in sym_domains or "cardiovascular" in sym_domains):
                active_relevant_symptoms.append(sym)
            else:
                active_unrelated_symptoms.append(sym)

    # 2. Filter Medications: Relevant when user asks about medications OR when a medication
    # could explain the current symptoms as a potential adverse effect
    relevant_medications: list[str] = []
    for med in state.medications:
        med_name = med.name.strip()
        med_l = med_name.lower()

        # If user explicitly asks about medicines or names this medicine
        if is_med_query or med_l in msg_l:
            relevant_medications.append(med_name)
            continue

        # Check if any reported symptom is a recognized side effect of this medication
        known_effects = []
        for drug_key, effects in MEDICATION_SIDE_EFFECTS.items():
            if drug_key in med_l:
                known_effects.extend(effects)

        if any(eff in msg_l for eff in known_effects) or any(
            any(eff in sym.lower() for eff in known_effects) for sym in active_relevant_symptoms
        ):
            relevant_medications.append(f"{med_name} (potential correlation with reported symptoms)")

    # 3. Filter Allergies: Essential whenever medications are considered or allergic reactions reported
    relevant_allergies: list[str] = []
    if is_med_query or "dermatological" in current_domains or "respiratory" in current_domains or any(a.substance.lower() in msg_l for a in state.allergies):
        relevant_allergies = [a.substance for a in state.allergies]

    # 4. Filter Medical History / Conditions: Relevant when condition intersects current domain
    relevant_conditions: list[str] = []
    for h in state.medical_history:
        cond_name = h.condition.strip()
        cond_domains = extract_domains_from_text(cond_name)
        if cond_name.lower() in msg_l or (current_domains and (cond_domains & current_domains)):
            relevant_conditions.append(cond_name)

    # 5. Detect Discrepancies between current message and recorded memory
    discrepancies: list[str] = []
    # E.g. user claims no medications but has medications on record
    if any(neg in msg_l for neg in ["i take no meds", "no medications", "not taking any medicine", "i don't take anything"]) and state.medications:
        med_names = ", ".join(m.name for m in state.medications)
        discrepancies.append(f"Patient states not taking medications, but records list: {med_names}. Clarify if recently stopped.")

    # E.g. user claims no allergies but records show allergy
    if any(neg in msg_l for neg in ["i have no allergies", "no drug allergies", "no allergies"]) and state.allergies:
        allg_names = ", ".join(a.substance for a in state.allergies)
        discrepancies.append(f"Patient states no allergies, but records list: {allg_names}.")

    return {
        "active_relevant_symptoms": active_relevant_symptoms,
        "active_unrelated_symptoms": active_unrelated_symptoms,
        "resolved_symptoms": historical_resolved,
        "relevant_medications": relevant_medications,
        "relevant_allergies": relevant_allergies,
        "relevant_conditions": relevant_conditions,
        "discrepancies": discrepancies,
    }


def format_context_prompt_blocks(
    latest_message: str,
    intent: dict[str, Any],
    memory: dict[str, Any],
    vitals_summary: str = "",
    trends_summary: str = "",
) -> str:
    """Constructs clean, prioritized context blocks for Gemini without prompt pollution or redundant boilerplate."""
    blocks: list[str] = []

    # Block 1: Immediate Focus (Highest Priority)
    blocks.append("=== [1. IMMEDIATE CONSULTATION FOCUS - HIGHEST PRIORITY] ===")
    blocks.append(f"Patient's Latest Message: \"{latest_message}\"")
    blocks.append(f"Conversation State: {intent.get('summary', 'Ongoing consultation')}")
    if intent.get("is_simple_question"):
        blocks.append("Direct Instruction: Answer this question directly, concisely, and clearly. Do NOT turn it into a full clinical report or symptom interview.")
    elif intent.get("intent") == "SYMPTOM_UPDATE":
        blocks.append("Direct Instruction: Seamlessly incorporate this new detail into your ongoing assessment. Do NOT repeat previous explanations or restart the conversation.")
    elif intent.get("intent") == "NEW_CONCERN":
        blocks.append("Direct Instruction: Address this new concern fully on its own merits. Do NOT drag in unrelated prior issues unless there is a clear medical danger.")

    # Block 2: Relevant Medical Memory (Supplementary only)
    has_relevant_memory = any([
        memory.get("active_relevant_symptoms"),
        memory.get("relevant_medications"),
        memory.get("relevant_allergies"),
        memory.get("relevant_conditions"),
        memory.get("discrepancies"),
    ])

    if has_relevant_memory:
        blocks.append("\n=== [2. RELEVANT MEDICAL MEMORY (Use ONLY if materially relevant)] ===")
        if memory.get("active_relevant_symptoms"):
            blocks.append(f"• Active Clinically-Related Symptoms: {', '.join(memory['active_relevant_symptoms'])}")
        if memory.get("relevant_conditions"):
            blocks.append(f"• Relevant Medical Conditions: {', '.join(memory['relevant_conditions'])}")
        if memory.get("relevant_medications"):
            blocks.append(f"• Relevant Medications: {', '.join(memory['relevant_medications'])}")
        if memory.get("relevant_allergies"):
            blocks.append(f"• Known Allergies: {', '.join(memory['relevant_allergies'])}")
        if memory.get("discrepancies"):
            for d in memory["discrepancies"]:
                blocks.append(f"⚠️ Medical Discrepancy to Clarify: {d}")

    # Block 3: Recorded Vitals (if present)
    if vitals_summary or trends_summary:
        blocks.append("\n=== [3. RECORDED VITALS & WEARABLE DATA] ===")
        if vitals_summary:
            blocks.append(f"• Current Vitals: {vitals_summary}")
        if trends_summary:
            blocks.append(f"• 24h Trends & Alerts: {trends_summary}")
        blocks.append("Note: Vitals are automatically synced from connected sensors. Never ask the patient to measure vitals in chat.")

    # Block 4: Inactive / Resolved History (Passive reference ONLY)
    inactive_items = []
    if memory.get("resolved_symptoms"):
        inactive_items.append(f"Resolved Symptoms: {', '.join(memory['resolved_symptoms'])}")
    if memory.get("active_unrelated_symptoms"):
        inactive_items.append(f"Unrelated Past Mentions (Ignore unless medically necessary): {', '.join(memory['active_unrelated_symptoms'])}")

    if inactive_items:
        blocks.append("\n=== [4. INACTIVE / RESOLVED HISTORY (DO NOT BRING UP UNLESS PATIENT ASKS)] ===")
        for item in inactive_items:
            blocks.append(f"• {item}")

    return "\n".join(blocks)
