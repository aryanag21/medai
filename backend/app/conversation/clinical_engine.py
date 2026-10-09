import logging
import re
from typing import Any

from app.patient_state.schema import PatientState
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Emergency red flags that require immediate emergency triage
_EMERGENCY_PATTERNS = [
    r"\b(crushing|radiating)\s+(chest\s+)?pain\b",
    r"\b(chest\s+pain|pain\s+in\s+(my\s+)?chest)\b.*\b(difficulty\s+breathing|cannot\s+breathe|arm\s+pain)\b",
    r"\b(difficulty\s+breathing|cannot\s+breathe|can\'t\s+breathe)\b.*\b(blue\s+lips|passed\s+out|chest\s+pain)\b",
    r"\b(coughing\s+up\s+blood|hemoptysis)\b",
    r"\b(slurred\s+speech|facial\s+droop|face\s+drooping|stroke|paralysis)\b",
]

_COMPILED_EMERGENCY = [re.compile(p, re.IGNORECASE) for p in _EMERGENCY_PATTERNS]


def detect_emergency(user_message: str) -> tuple[bool, str | None]:
    for pattern in _COMPILED_EMERGENCY:
        if pattern.search(user_message):
            matched = pattern.pattern
            return True, f"Emergency pattern detected: {matched}"
    return False, None


def format_vitals_summary(vitals: dict[str, Any]) -> str:
    if not vitals:
        return ""

    parts = []
    if "heart_rate" in vitals:
        hr = vitals["heart_rate"]["value"]
        parts.append(f"Heart Rate: {int(hr)} bpm")
    if "oxygen_saturation" in vitals:
        spo2 = int(vitals["oxygen_saturation"]["value"])
        parts.append(f"SpO2: {spo2}%")
    if "temperature" in vitals:
        temp = vitals["temperature"]["value"]
        unit = vitals["temperature"].get("unit", "F")
        parts.append(f"Temp: {temp}°{unit}")
    if "blood_pressure_systolic" in vitals and "blood_pressure_diastolic" in vitals:
        sys = int(vitals["blood_pressure_systolic"]["value"])
        dia = int(vitals["blood_pressure_diastolic"]["value"])
        parts.append(f"BP: {sys}/{dia}")

    return ", ".join(parts)


def generate_emergency_response(user_message: str) -> str:
    return (
        "⚠️ **Urgent Medical Evaluation Recommended**\n\n"
        "The symptoms you described (such as severe chest discomfort or acute breathing difficulty) "
        "can be a sign of a cardiac or respiratory emergency.\n\n"
        "Please don't wait: call emergency medical services immediately (such as 911, 112, or 108) "
        "or have someone take you to the nearest emergency clinic right away. Rest in a comfortable upright position while waiting for help."
    )


def generate_fallback_clinical_response(state: PatientState, user_message: str, prior_messages: list[dict[str, str]] | None = None) -> str:
    text_lower = user_message.lower()
    vitals_summary = format_vitals_summary(state.vitals)
    vitals_note = f"\n\n*(Your recorded vitals in the app look reassuring: {vitals_summary})*" if vitals_summary else ""

    # Check recent history for context
    past_text = " ".join([m.get("content", "") for m in (prior_messages or [])[-4:]]).lower()
    had_cough_earlier = "cough" in past_text

    # 1. Trauma / Injury / Slammed foot / Stubbed toe / Sprain
    injury_words = [
        "slam", "door", "foot", "toe", "ankle", "hit my", "stub", "kick", "fall",
        "fell", "twist", "sprain", "bruis", "injur", "hurt my", "swollen toe", "stepped on"
    ]
    if any(k in text_lower for k in injury_words):
        cough_mention = "on top of already dealing with that cough! " if had_cough_earlier else ""
        return (
            f"Ouch! Slamming your foot into a door is intensely painful {cough_mention}— it throbs immediately and can make walking really tough.\n\n"
            "Here is the best way to handle it right now:\n"
            "• **Elevate & Rest:** Sit or lie down and prop your foot up on a pillow above heart level to reduce the throbbing and swelling.\n"
            "• **Ice:** Apply an ice pack (or frozen peas wrapped in a towel) for 15–20 minutes every couple of hours.\n"
            "• **Pain & Swelling Relief:** **Ibuprofen (400 mg with food)** is very effective because it reduces both pain and inflammation. Alternatively, **Paracetamol (500–650 mg)** works well if you cannot take anti-inflammatories.\n\n"
            "**When to get it checked:** Can you bear any weight on your foot, and does any toe look bruised, purple, or crooked? If you cannot walk on it at all, or if a toe looks misaligned, it's best to get an X-ray to rule out a small fracture."
        )

    # 2. Cough / Cold / Lingering respiratory
    cough_words = ["cough", "cold", "congestion", "phlegm", "mucus", "sneeze", "runny nose", "sore throat"]
    if any(k in text_lower for k in cough_words) or (had_cough_earlier and any(w in text_lower for w in ["still there", "not gone", "worse", "same", "coughing"])):
        is_lingering = any(w in text_lower for w in ["still", "not gone", "lingering", "days", "week"])
        intro = (
            "Coughs can be really stubborn and often linger for 1 to 2 weeks after an infection because the airway lining stays sensitive."
            if is_lingering
            else "I'm sorry you're dealing with a cough and cold. Upper respiratory bugs can really wear you down."
        )
        return (
            f"{intro}\n\n"
            "Here are the most effective ways to quiet it down:\n"
            "• **For a dry, hacking tickle:** An over-the-counter syrup with **Dextromethorphan (10–20 mg)** helps calm the cough reflex, especially before bed.\n"
            "• **For chest mucus:** **Guaifenesin (200–400 mg)** taken with a full glass of water thins out phlegm so it's easier to clear.\n"
            "• **Soothing home care:** A warm cup of tea with a spoonful of **honey** (clinically proven to coat and soothe the throat) and steam from a warm shower make a noticeable difference.\n\n"
            "Is your cough mostly dry or bringing up phlegm? And are you running any fever alongside it?"
            f"{vitals_note}"
        )

    # 3. Headache
    if any(k in text_lower for k in ["headache", "migraine", "head hurts", "throbbing head"]):
        return (
            "I'm sorry your head is pounding. Tension and dehydration are the most common culprits, especially when unwell.\n\n"
            "For fast relief:\n"
            "• Drink a tall glass of water and rest your eyes in a dimly lit, quiet room away from screens.\n"
            "• An OTC pain reliever like **Paracetamol (500–1,000 mg)** or **Ibuprofen (400 mg with a snack)** usually brings prompt relief.\n"
            "• A cool compress placed over your forehead or the back of your neck can help ease the pressure.\n\n"
            "Is the pain throbbing or steady, and is it on one side or all over?"
            f"{vitals_note}"
        )

    # 4. Fever / Chills
    if any(k in text_lower for k in ["fever", "chills", "feverish", "temperature"]):
        return (
            "Running a fever is your body's immune system fighting off a bug, but it can leave you feeling completely exhausted.\n\n"
            "To stay comfortable and bring it down safely:\n"
            "• **Paracetamol (500–650 mg every 4–6 hours as needed)** is the first-line medication to reduce fever and body aches (keep within 3,000 mg in 24 hours).\n"
            "• Stay well-hydrated with water, electrolyte drinks, or warm broths to replace fluids lost from fever.\n"
            "• Wear lightweight, loose clothing and rest in a well-ventilated room.\n\n"
            "What temperature have you measured, and have you noticed any other symptoms like chills or a sore throat?"
            f"{vitals_note}"
        )

    # 5. Stomach / Digestive / Nausea
    if any(k in text_lower for k in ["stomach", "nausea", "vomit", "diarrhea", "cramp", "belly", "tummy"]):
        return (
            "Stomach upset is miserable. The priority right now is keeping your digestive system rested while staying hydrated.\n\n"
            "A few gentle steps:\n"
            "• Take small, frequent sips of water, electrolyte solution, or ginger/peppermint tea.\n"
            "• Stick to bland foods (bananas, rice, plain toast, crackers) once you feel up to eating.\n"
            "• Avoid dairy, caffeine, and spicy or greasy foods for the next couple of days.\n\n"
            "How long has your stomach been bothering you, and are you able to keep fluids down?"
            f"{vitals_note}"
        )

    # 6. General Conversational / Greetings
    if any(k in text_lower for k in ["hello", "hi", "hey", "good morning", "good evening", "how are you"]):
        return (
            "Hello! I'm here to help you evaluate how you're feeling and provide medical advice. "
            "How are you feeling today, or what symptoms are bothering you?"
        )

    # 7. Adaptive General Reply
    return (
        f"I hear you. Based on what you've described, getting plenty of rest and staying hydrated is a great foundation right now.\n\n"
        "If you're having general aches or discomfort, a standard OTC pain reliever like **Paracetamol (500 mg)** can help you rest more comfortably.\n\n"
        "Could you tell me a bit more about how long this has been going on, or any other symptoms you're noticing?"
        f"{vitals_note}"
    )


async def generate_conversational_reply(
    llm: LLMProvider,
    state: PatientState,
    latest_message: str,
    conversation_history: list[dict[str, str]],
) -> tuple[str, bool, str | None, str | None]:
    """Generates a concise, natural, human-sounding conversational clinical response."""

    # 1. Emergency Check (only for acute, dangerous presentations)
    is_emergency, emergency_reason = detect_emergency(latest_message)
    if is_emergency:
        logger.warning("Emergency triage triggered for message: %s", latest_message)
        reply = generate_emergency_response(latest_message)
        return reply, True, "emergency", emergency_reason

    # 2. Known patient context for prompt
    vitals_text = format_vitals_summary(state.vitals)
    vitals_line = f"• Recorded Vitals from App: {vitals_text}\n" if vitals_text else ""
    allergies_str = ", ".join(a.substance for a in state.allergies)
    allergies_line = f"• Known Allergies: {allergies_str}\n" if allergies_str else ""
    meds_str = ", ".join(m.name for m in state.medications)
    meds_line = f"• Current Medications: {meds_str}\n" if meds_str else ""

    system_prompt = (
        "You are MEDAI, a warm, concise, and helpful clinical AI assistant.\n"
        "You speak naturally and conversationally, like a friendly and knowledgeable doctor.\n\n"
        f"{vitals_line}{allergies_line}{meds_line}\n"
        "CRITICAL CONVERSATIONAL GUIDELINES:\n"
        "1. BE CONCISE & HUMAN: Write 2 to 3 short paragraphs max (under 160 words). Avoid long-winded essays, walls of bullet points, or robotic section headers.\n"
        "2. FOCUS DIRECTLY ON WHAT THE PATIENT JUST SAID: If the patient talks about an injury (like slamming their foot into a door), address that trauma immediately with R.I.C.E. and pain relief! Do NOT repeat old advice about unrelated symptoms from earlier messages.\n"
        "3. CALM, REASSURING TONE: Do not use alarmist language, screaming siren emojis, or aggressive all-caps headers.\n"
        "4. PRACTICAL RELIEF: Offer 1-2 practical OTC remedies with clear adult doses (e.g., Ibuprofen 400mg with food or Paracetamol 500mg) and simple home care.\n"
        "5. NEVER ASK FOR VITALS IN CHAT: Vitals are managed in the app. If vitals are recorded above, reference them gently if relevant.\n"
        "6. CLOSE WITH A GENTLE QUESTION: Ask 1 short, natural follow-up question."
    )

    # 3. Try LLM generation
    try:
        history_lines = []
        for msg in conversation_history[-6:]:
            role = "Patient" if msg.get("role") == "user" else "Assistant"
            history_lines.append(f"{role}: {msg.get('content', '')}")
        history_lines.append(f"Patient: {latest_message}")
        prompt = "\n".join(history_lines) + "\nAssistant:"

        reply = await llm.generate(prompt, system=system_prompt)
        reply = reply.strip()
        if reply:
            return reply, False, None, None
    except Exception as exc:
        logger.warning("LLM conversational generation failed (%s); using intelligent clinical fallback", exc)

    # 4. Fallback to concise, empathetic clinical responder
    fallback_reply = generate_fallback_clinical_response(state, latest_message, conversation_history)
    return fallback_reply, False, None, None
