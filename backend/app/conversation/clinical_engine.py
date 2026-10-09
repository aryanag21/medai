import logging
import re
from typing import Any

from app.patient_state.schema import PatientState
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Emergency red flags that require immediate emergency triage
_EMERGENCY_PATTERNS = [
    r"\b(sharp|severe|crushing|radiating)\s+pain\s+(in\s+)?(my\s+)?(left\s+)?arm\b",
    r"\b(chest\s+pain|pain\s+in\s+(my\s+)?chest|chest\s+pressure|chest\s+tightness)\b",
    r"\b(difficulty\s+breathing|trouble\s+breathing|cannot\s+breathe|can\'t\s+breathe|shortness\s+of\s+breath|breathless)\b",
    r"\b(coughing\s+up\s+blood|hemoptysis|blood\s+in\s+sputum)\b",
    r"\b(blue\s+lips|cyanosis|turning\s+blue)\b",
    r"\b(passed\s+out|lost\s+consciousness|fainted|unconscious)\b",
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
        return "No vitals recorded in the app yet."

    parts = []
    if "heart_rate" in vitals:
        hr = vitals["heart_rate"]["value"]
        parts.append(f"Heart Rate: {int(hr)} bpm")
    if "blood_pressure_systolic" in vitals and "blood_pressure_diastolic" in vitals:
        sys = int(vitals["blood_pressure_systolic"]["value"])
        dia = int(vitals["blood_pressure_diastolic"]["value"])
        parts.append(f"Blood Pressure: {sys}/{dia} mmHg")
    if "oxygen_saturation" in vitals:
        spo2 = int(vitals["oxygen_saturation"]["value"])
        parts.append(f"Oxygen Saturation (SpO2): {spo2}%")
    if "temperature" in vitals:
        temp = vitals["temperature"]["value"]
        unit = vitals["temperature"].get("unit", "F")
        parts.append(f"Temperature: {temp}°{unit}")
    if "respiratory_rate" in vitals:
        rr = int(vitals["respiratory_rate"]["value"])
        parts.append(f"Respiratory Rate: {rr} breaths/min")

    return ", ".join(parts) if parts else "No vitals recorded in the app yet."


def generate_emergency_response(user_message: str) -> str:
    return (
        "🚨 **URGENT MEDICAL EMERGENCY WARNING**\n\n"
        "The symptoms you described (such as acute chest pain, radiating arm pain, or difficulty breathing) "
        "can be signs of a life-threatening cardiovascular or respiratory emergency (such as an acute myocardial infarction "
        "or pulmonary embolism).\n\n"
        "**Please take immediate action:**\n"
        "• **Call emergency medical services immediately** (e.g., **911** in the US, **112** in Europe/India, or **108** for ambulance).\n"
        "• **Go to the nearest Emergency Room (ER)** right away. Do **NOT** attempt to drive yourself; have an ambulance or someone else transport you.\n"
        "• Sit down in a comfortable, upright resting position and loosen tight clothing around your neck and chest while awaiting emergency help.\n"
        "• If you have someone nearby, alert them to your condition immediately."
    )


def generate_fallback_clinical_response(state: PatientState, user_message: str) -> str:
    text_lower = user_message.lower()
    vitals_summary = format_vitals_summary(state.vitals)

    # Check for known allergies & medications to personalize warnings
    allergy_names = [a.substance for a in state.allergies]
    allergy_text = (
        f" (Safety note: cross-checked with your recorded allergies: {', '.join(allergy_names)})"
        if allergy_names
        else ""
    )

    # Vitals reassurance or notification
    vitals_note = ""
    if state.vitals:
        vitals_note = f"\n\n**App Vitals Check:** I reviewed your logged vitals from the app ({vitals_summary}). "
        if "oxygen_saturation" in state.vitals and state.vitals["oxygen_saturation"]["value"] >= 95:
            vitals_note += "Your oxygen saturation is currently within a reassuring normal range."
        elif "oxygen_saturation" in state.vitals and state.vitals["oxygen_saturation"]["value"] < 95:
            vitals_note += "⚠️ Note: Your logged SpO2 is below 95%, which warrants close monitoring and in-person medical evaluation if breathing feels difficult."

    # 1. Cough and Cold / Upper Respiratory
    if any(k in text_lower for k in ["cough", "cold", "congestion", "runny nose", "sneeze", "flu", "respiratory"]):
        return (
            "I'm very sorry you're dealing with an extreme cough and cold. Upper respiratory infections can be exhausting, "
            "but there are effective over-the-counter (OTC) medications and home care strategies to help you find relief and recover quickly.\n\n"
            "### Clinical Assessment\n"
            "Your presentation is consistent with an **Acute Viral Upper Respiratory Tract Infection** (such as the common cold, rhinovirus, or acute viral bronchitis)."
            f"{vitals_note}\n\n"
            "### Recommended Medications & Prescriptions (OTC)\n"
            f"Here are evidence-based, safe medications to relieve your specific symptoms{allergy_text}:\n\n"
            "1. **For Fever, Headache & Body Aches:**\n"
            "   • **Paracetamol (Acetaminophen)**: 500 mg to 650 mg orally every 4 to 6 hours as needed (do not exceed 3,000 mg in 24 hours). Avoid if you have significant liver impairment.\n"
            "   • *Alternative*: **Ibuprofen**: 400 mg orally every 6 to 8 hours taken with meals (avoid if you have a history of gastritis, stomach ulcers, or kidney disease).\n\n"
            "2. **For Cough Relief:**\n"
            "   • *If your cough is dry and hacking*: **Dextromethorphan (DXM)** (e.g., Robitussin DM / Benylin) 10–20 mg every 4 hours or 30 mg every 6–8 hours to calm the cough reflex.\n"
            "   • *If your cough has thick chest mucus/phlegm*: **Guaifenesin** (e.g., Mucinex) 200–400 mg every 4 hours with a large glass of water to thin and loosen mucus.\n"
            "   • *Natural soothing*: **Honey** (1–2 teaspoons in warm water or herbal tea) has proven clinical efficacy for coating and calming throat irritation.\n\n"
            "3. **For Nasal Congestion & Runny Nose:**\n"
            "   • **Saline Nasal Spray / Sinus Rinse**: Use 2–3 sprays per nostril 3 times daily. This safely rinses mucus and allergens without medication side effects.\n"
            "   • **Oxymetazoline 0.05% Nasal Spray** (e.g., Afrin): 1–2 sprays per nostril every 12 hours (use for a maximum of 3 consecutive days to prevent rebound congestion).\n"
            "   • **Cetirizine (10 mg)** or **Loratadine (10 mg)** once daily: If sneezing, watery eyes, or post-nasal drip are prominent.\n\n"
            "4. **For Throat Discomfort:**\n"
            "   • Warm saline gargle (1/2 teaspoon salt in 1 cup warm water) 3–4 times daily.\n"
            "   • Menthol or pectin throat lozenges to soothe localized irritation.\n\n"
            "### Supportive Home Care\n"
            "• **Hydration**: Drink 2.5 to 3 liters of warm fluids daily (water, warm broths, herbal teas) to keep respiratory secretions thin.\n"
            "• **Steam Inhalation / Humidifier**: Inhaling steam for 10–15 minutes or using a bedroom cool-mist humidifier eases airway tightness.\n"
            "• **Rest**: Elevate your head with an extra pillow while sleeping to reduce nighttime coughing.\n\n"
            "### ⚠️ Red Flags — When to See a Doctor Immediately\n"
            "Please seek prompt in-person medical evaluation if:\n"
            "• You develop shortness of breath, wheezing, or difficulty catching your breath.\n"
            "• Fever exceeds 103°F (39.4°C) or lasts more than 3 consecutive days.\n"
            "• You cough up blood or thick, rust-colored sputum.\n"
            "• Your symptoms worsen significantly after 7–10 days.\n\n"
            "How many days have you had this cough and cold, and are you running any fever right now?"
        )

    # 2. Fever & Chills
    if any(k in text_lower for k in ["fever", "chills", "feverish"]):
        return (
            "I'm sorry to hear you're experiencing a fever. Here is guidance to manage it safely:\n\n"
            "### Clinical Management & Medications\n"
            f"• **Paracetamol (Acetaminophen)**: 500 mg – 650 mg orally every 4 to 6 hours as needed (maximum 3,000 mg in 24 hours) to reduce fever and relieve discomfort{allergy_text}.\n"
            "• **Hydration**: Increased fluid losses occur with fever. Drink plenty of water, oral rehydration solutions, or clear broths.\n"
            "• **Cooling Measures**: Wear lightweight clothing and rest in a well-ventilated, comfortable room. Avoid cold baths, which cause shivering and raise core temperature.\n"
            f"{vitals_note}\n\n"
            "### ⚠️ Red Flag Warnings\n"
            "Seek immediate medical attention if you experience: a stiff neck, confusion, rash, persistent vomiting, or fever over 103°F (39.4°C).\n\n"
            "What temperature have you measured, and do you have any other symptoms like chills or body aches?"
        )

    # 3. Headache
    if any(k in text_lower for k in ["headache", "migraine", "head pain"]):
        return (
            "I understand you're suffering from a headache. Here are practical ways to manage it:\n\n"
            "### Recommended Medications & Relief\n"
            f"• **Paracetamol (Acetaminophen)**: 500 mg – 1,000 mg orally every 6 hours as needed (do not exceed 3,000 mg per day){allergy_text}.\n"
            "• **Ibuprofen**: 400 mg orally with food every 6–8 hours (if no history of gastric ulcers or kidney problems).\n"
            "• **Hydration & Rest**: Drink a large glass of water and rest in a quiet, darkened room.\n"
            "• **Cold or Warm Compress**: Apply a cold pack to your forehead or temples for 15 minutes.\n"
            f"{vitals_note}\n\n"
            "### ⚠️ Red Flags\n"
            "Seek emergency care if your headache began suddenly with severe intensity ('thunderclap' headache), or is accompanied by vision changes, weakness, or fever with neck stiffness.\n\n"
            "Is the pain throbbing or dull, and on one side or all over your head?"
        )

    # 4. General Medical Care Response
    symptom_list = [s.symptom for s in state.symptoms]
    sym_desc = f" ({', '.join(symptom_list)})" if symptom_list else ""
    return (
        f"Thank you for sharing your symptoms{sym_desc}. I'm here to help you evaluate your condition and provide medical guidance.\n\n"
        f"### Clinical Overview\n"
        "Based on what you've described, it's important to monitor your symptoms closely, ensure optimal rest, and stay well-hydrated."
        f"{vitals_note}\n\n"
        "### General Management & Safe Relief\n"
        f"• For generalized body discomfort or mild fever, **Paracetamol (Acetaminophen)** 500 mg every 4–6 hours as needed (max 3,000 mg/24h) is generally safe for short-term relief{allergy_text}.\n"
        "• Stay hydrated with 2 to 3 liters of fluids daily.\n"
        "• Ensure adequate sleep and rest to allow your immune system to recover.\n\n"
        "### ⚠️ When to Seek In-Person Medical Attention\n"
        "Please visit a clinic or hospital if symptoms worsen, or if you develop red flag signs such as difficulty breathing, high fever, chest pain, or severe weakness.\n\n"
        "Could you tell me a bit more about how long you've been feeling this way, or any other symptoms you've noticed?"
    )


async def generate_conversational_reply(
    llm: LLMProvider,
    state: PatientState,
    latest_message: str,
    conversation_history: list[dict[str, str]],
) -> tuple[str, bool, str | None, str | None]:
    """Generates an empathetic, clinically sound, conversational response.

    Returns: (reply_text, is_assessment, assessment_status, escalation)
    """

    # 1. Emergency Check
    is_emergency, emergency_reason = detect_emergency(latest_message)
    if is_emergency:
        logger.warning("Emergency triage triggered for message: %s", latest_message)
        reply = generate_emergency_response(latest_message)
        return reply, True, "emergency", emergency_reason

    # 2. Check vitals from patient state
    vitals_text = format_vitals_summary(state.vitals)

    # 3. Known patient context
    age_str = f"Age: {state.patient.age}" if state.patient.age else "Age: not provided"
    sex_str = f"Sex: {state.patient.sex}" if state.patient.sex else "Sex: not provided"
    allergies_str = ", ".join(a.substance for a in state.allergies) or "None reported"
    meds_str = ", ".join(m.name for m in state.medications) or "None reported"
    history_str = ", ".join(h.condition for h in state.medical_history) or "None reported"
    symptoms_str = ", ".join(s.symptom for s in state.symptoms) or "None reported"

    system_prompt = (
        "You are MEDAI, an empathetic, highly knowledgeable, and conversational AI clinical health assistant.\n"
        "You communicate warmly, clearly, and thoughtfully with patients, just like an experienced, attentive healthcare professional.\n\n"
        f"Patient Information:\n"
        f"• Demographics: {age_str}, {sex_str}\n"
        f"• Recorded Vitals from App: {vitals_text}\n"
        f"• Known Allergies: {allergies_str}\n"
        f"• Current Medications: {meds_str}\n"
        f"• Medical History / Conditions: {history_str}\n"
        f"• Current Symptoms: {symptoms_str}\n\n"
        "Guidelines for your response:\n"
        "1. Empathy & Active Listening: Directly acknowledge the patient's concerns (e.g. cough, cold, fever, pain) with compassion and care.\n"
        "2. Clinical Impression: Explain in plain, accessible language what might be causing their symptoms (e.g. viral upper respiratory infection, common cold, tension headache, etc.).\n"
        "3. Treatment & Prescription Guidance (Evidence-Based Safe OTC):\n"
        "   - Provide clear, safe over-the-counter medication recommendations suited for their specific symptoms (e.g., Paracetamol / Acetaminophen for fever/body aches, Dextromethorphan for dry cough, Guaifenesin for chesty/productive cough, saline nasal sprays, throat lozenges).\n"
        "   - Include standard adult dosage guidelines, frequency (e.g. 'every 4 to 6 hours as needed'), and max daily limits (e.g. 'do not exceed 3,000 mg in 24 hours').\n"
        "   - Cross-check against their known allergies and medications to ensure safety.\n"
        "4. Non-Pharmacological & Home Care:\n"
        "   - Practical steps to soothe symptoms: hydration (warm water, broths, herbal teas with honey), steam inhalation, saline gargles, rest, elevation while sleeping.\n"
        "5. Vitals Context:\n"
        "   - If vitals are recorded above, reference them reassuringly (e.g., 'Your logged heart rate and oxygen saturation look good...').\n"
        "   - NEVER ask the user to input vitals in chat — vitals are entered separately in the app.\n"
        "6. Red Flags / When to Seek Immediate Care:\n"
        "   - Clearly state warning signs that require emergency or urgent in-person medical attention (e.g. shortness of breath, fever > 103°F or persisting > 3 days, coughing up blood, chest pain).\n"
        "7. Conversational Follow-up:\n"
        "   - Ask 1-2 thoughtful, natural questions at the end if more context would help tailor recommendations (e.g. 'Are you running a fever or having any phlegm with that cough?')."
    )

    # 4. Try LLM generation
    try:
        # Build prompt from recent conversation history
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

    # 5. Fallback to comprehensive clinical engine
    fallback_reply = generate_fallback_clinical_response(state, latest_message)
    return fallback_reply, False, None, None
