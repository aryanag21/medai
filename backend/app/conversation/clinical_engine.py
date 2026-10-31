"""Clinical Conversational Engine for MEDAI.

Overhauled according to the Comprehensive Medical Assistant Overhaul requirements:
- Part 2: Intelligent Context Management (immediate vs relevant memory vs irrelevant/resolved).
- Part 3: Response Prioritisation (latest message determines direction).
- Part 4: Elimination of Repetition (no repetitive openers, no regurgitation of history).
- Part 5: Natural, Fluent, Approachable Communication Style (adaptive response length).
- Part 6: Intelligent, Purposeful Follow-Up Questions (at most 1 focused question, only when needed).
- Part 7 & 8: Contextual Medical Reasoning & Safety (emergency detection, medication/allergy vigilance).
- Part 9: Restructured, Clean System Prompt with clear priority hierarchy.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.conversation.context_manager import (
    classify_message_intent,
    detect_emergency,
    detect_resolved_symptoms,
    filter_relevant_memory,
    format_context_prompt_blocks,
)
from app.patient_state.schema import PatientState
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def format_vitals_summary(vitals: dict[str, Any]) -> str:
    """Formats recorded vitals into a concise clinical string."""
    if not vitals:
        return ""

    parts = []
    if "heart_rate" in vitals:
        hr = vitals["heart_rate"]["value"]
        parts.append(f"HR: {int(hr)} bpm")
    if "oxygen_saturation" in vitals:
        spo2 = int(vitals["oxygen_saturation"]["value"])
        parts.append(f"SpO2: {spo2}%")
    if "body_temperature" in vitals or "temperature" in vitals:
        temp_obj = vitals.get("body_temperature") or vitals.get("temperature")
        temp = temp_obj["value"]
        unit = temp_obj.get("unit", "F")
        parts.append(f"Temp: {temp}°{unit}")
    if "blood_pressure_systolic" in vitals and "blood_pressure_diastolic" in vitals:
        sys = int(vitals["blood_pressure_systolic"]["value"])
        dia = int(vitals["blood_pressure_diastolic"]["value"])
        parts.append(f"BP: {sys}/{dia} mmHg")
    if "weight" in vitals:
        wt = vitals["weight"]["value"]
        unit = vitals["weight"].get("unit", "kg")
        parts.append(f"Weight: {wt} {unit}")

    return ", ".join(parts)


def format_detailed_vitals_report(vitals: dict[str, Any]) -> str:
    """Formats recorded vitals into a structured, patient-friendly report with clinical normal ranges."""
    if not vitals:
        return ""

    lines = []
    if "heart_rate" in vitals:
        hr = int(vitals["heart_rate"]["value"])
        status = "Normal" if 60 <= hr <= 100 else ("Elevated (Tachycardia)" if hr > 100 else "Low (Bradycardia)")
        lines.append(f"• **Heart Rate**: {hr} bpm (Normal resting: 60–100 bpm — {status})")

    if "blood_pressure_systolic" in vitals and "blood_pressure_diastolic" in vitals:
        sys = int(vitals["blood_pressure_systolic"]["value"])
        dia = int(vitals["blood_pressure_diastolic"]["value"])
        if sys < 120 and dia < 80:
            bp_status = "Normal"
        elif 120 <= sys <= 129 and dia < 80:
            bp_status = "Elevated"
        else:
            bp_status = "Stage 1/2 High"
        lines.append(f"• **Blood Pressure**: {sys}/{dia} mmHg (Normal: <120/80 mmHg — {bp_status})")

    if "oxygen_saturation" in vitals:
        spo2 = int(vitals["oxygen_saturation"]["value"])
        spo2_status = "Normal" if spo2 >= 95 else "Low (Attention Required)"
        lines.append(f"• **Oxygen Saturation (SpO2)**: {spo2}% (Normal: 95–100% — {spo2_status})")

    if "body_temperature" in vitals or "temperature" in vitals:
        temp_obj = vitals.get("body_temperature") or vitals.get("temperature")
        temp = temp_obj["value"]
        unit = temp_obj.get("unit", "F")
        temp_status = "Fever" if temp >= 100.4 else "Normal"
        lines.append(f"• **Body Temperature**: {temp}°{unit} (Normal: 97.8–99.1°F — {temp_status})")

    if "respiratory_rate" in vitals:
        rr = int(vitals["respiratory_rate"]["value"])
        lines.append(f"• **Respiratory Rate**: {rr} breaths/min (Normal: 12–20 breaths/min)")

    if "weight" in vitals:
        wt = vitals["weight"]["value"]
        unit = vitals["weight"].get("unit", "kg")
        lines.append(f"• **Weight**: {wt} {unit}")

    return "\n".join(lines)


def generate_emergency_response(user_message: str, reason: str | None = None) -> str:
    """Generates an immediate, clear emergency guidance response."""
    return (
        "⚠️ **Urgent Medical Evaluation Required**\n\n"
        "The symptoms you describe indicate a potentially serious emergency that requires immediate medical care.\n\n"
        "**Please take these steps immediately:**\n"
        "• Call emergency services right now (911, 112, 108, or your local emergency number).\n"
        "• If you are alone, contact a family member or neighbor to stay with you while help arrives.\n"
        "• Rest in a comfortable, upright position, and avoid physical exertion.\n\n"
        "Do not wait to see if symptoms improve on their own."
    )


def generate_intelligent_clinical_fallback(
    state: PatientState,
    latest_message: str,
    intent: dict[str, Any],
    memory: dict[str, Any],
    vitals_summary: str = "",
) -> str:
    text_lower = latest_message.lower().strip()
    vitals_note = f"\n\n*(Your recorded vitals: {vitals_summary})*" if vitals_summary else ""

    # 1. System / Connection query
    if any(k in text_lower for k in ["server", "app is", "app server", "backend"]) or (
        any(k in text_lower for k in ["slow", "lag", "running", "working"]) and any(k in text_lower for k in ["server", "app", "connection"])
    ):
        return (
            "The MEDAI system is online and active! "
            "How are you feeling right now, and what health symptoms or concerns can I help you with today?"
        )

    # 2. Simple Direct Question (e.g. medication with food)
    if intent.get("is_simple_question") or intent.get("intent") == "SIMPLE_QUESTION":
        if "paracetamol" in text_lower and ("food" in text_lower or "empty" in text_lower):
            return "Yes, Paracetamol (acetaminophen) can safely be taken with or without food. Just be sure to follow the recommended dosage and not exceed 3,000 mg in 24 hours."
        if "ibuprofen" in text_lower and ("food" in text_lower or "empty" in text_lower):
            return "Ibuprofen should ideally be taken with food or a glass of milk to protect your stomach lining from irritation."
        if "blood pressure" in text_lower or "bp" in text_lower:
            return "A normal adult resting blood pressure is generally under 120/80 mmHg. Readings between 120–129 systolic are considered elevated, while 130/80 mmHg or higher enters hypertension categories."

    # 3. Vitals Query
    if intent.get("intent") == "VITALS_QUERY" or intent.get("is_vitals_query") or (
        any(k in text_lower for k in ["vital", "heart rate", "bp", "spo2", "pulse", "oxygen"]) and "what" in text_lower
    ):
        vitals_detailed = format_detailed_vitals_report(state.vitals)
        if vitals_detailed:
            return (
                "Here are your latest recorded vital signs:\n\n"
                f"{vitals_detailed}\n\n"
                "All metrics are recorded and monitored against clinical reference ranges. "
                "How are you feeling right now?"
            )
        elif vitals_summary:
            return (
                "Here are your latest recorded vital signs:\n\n"
                f"• {vitals_summary}\n\n"
                "Your readings are logged and up to date. Is there a specific reading or symptom you'd like to discuss?"
            )
        else:
            return (
                "You currently have no vital signs on record. "
                "If you wear a connected smartwatch (via Health Connect), readings will sync automatically. "
                "You can also tell me your readings right here (e.g. 'My heart rate is 72 and blood pressure is 120/80') "
                "and I will update your chart immediately."
            )

    # 4. Vitals Update confirmation
    if intent.get("intent") == "VITALS_UPDATE" or intent.get("is_vitals_update"):
        vitals_detailed = format_detailed_vitals_report(state.vitals)
        return (
            "I've updated your chart with your vital signs:\n\n"
            f"{vitals_detailed if vitals_detailed else (vitals_summary or 'Readings have been recorded successfully.')}\n\n"
            "Thank you for keeping your health record updated. How can I assist you further?"
        )

    # 5. Symptom Resolution acknowledgment
    if intent.get("intent") == "RESOLUTION" or memory.get("resolved_symptoms"):
        resolved_names = ", ".join(memory.get("resolved_symptoms", []))
        if resolved_names:
            return f"That is great news that your {resolved_names} has resolved! Is there anything else you'd like to check or discuss today?"
        return "I'm glad to hear that you are feeling better and that has cleared up. Is there anything else you'd like to discuss?"

    # 6. Combined Throat Pain + Cough
    if any(k in text_lower for k in ["throat", "swallow"]) and any(k in text_lower for k in ["cough", "cold", "congestion"]):
        return (
            "For a combination of throat pain and cough:\n\n"
            "• **For throat pain & inflammation**: Over-the-counter anti-inflammatories like **Ibuprofen (400 mg with food)** or **Paracetamol/Acetaminophen (500–650 mg)** will significantly reduce pain when swallowing.\n"
            "• **For the cough**: Warm tea with honey (1–2 teaspoons) soothes the throat and calms coughing naturally. If it is a dry, hacking cough, a **Dextromethorphan** cough syrup can help suppress the reflex, especially before sleep.\n"
            "• **Home care**: Gargling with warm salt water (1/2 tsp salt in warm water, 3–4 times daily) helps reduce pharyngeal swelling.\n\n"
            "Are you also running a fever or noticing any difficulty swallowing liquids?"
            f"{vitals_note}"
        )

    # 7. Throat Pain / Sore Throat
    if any(k in text_lower for k in ["throat", "swallow", "tonsil", "strep"]):
        return (
            "For throat pain and soreness:\n\n"
            "• **Pain relief**: **Ibuprofen (400 mg every 6–8 hours with food)** or **Paracetamol (500–650 mg every 6 hours)** are the most effective over-the-counter options for easing throat discomfort and inflammation.\n"
            "• **Local soothing**: Anesthetic throat lozenges (containing benzocaine, menthol, or amylmetacresol) temporarily numb pain. Warm salt-water gargles (1/2 tsp salt in warm water) 3 to 4 times a day reduce local swelling.\n"
            "• **Hydration**: Warm broths and herbal teas with honey help coat and soothe the irritated mucous membranes.\n\n"
            "Do you have a fever, or do you notice any white patches or swollen glands in your neck?"
            f"{vitals_note}"
        )

    # 8. Cough & Congestion
    if any(k in text_lower for k in ["cough", "cold", "congestion", "phlegm", "mucus"]):
        return (
            "For cough relief, the best over-the-counter medicine depends on the type of cough:\n\n"
            "• **Dry, tickly cough**: A cough suppressant like **Dextromethorphan** (15–30 mg every 6–8 hours) calms the cough center in the brain. Honey (1–2 teaspoons) is also clinically shown to soothe night-time dry coughing.\n"
            "• **Productive / chesty cough (with phlegm)**: An expectorant like **Guaifenesin** (200–400 mg every 4 hours) thins mucus so your airways can clear it easily.\n"
            "• **Supportive relief**: Steam inhalation or using a cool-mist humidifier, along with generous hydration (warm water, broths), thins secretions and eases irritation.\n\n"
            "Is your cough mostly dry and hacking, or are you bringing up colored phlegm?"
            f"{vitals_note}"
        )

    # 9. Headache / Migraine
    if any(k in text_lower for k in ["headache", "migraine", "head hurts", "head ache"]):
        return (
            "For headache relief:\n\n"
            "• **Over-the-counter options**: **Paracetamol/Acetaminophen (500–1000 mg)** or **Ibuprofen (400 mg with food)** are first-line options for tension headaches and mild migraines.\n"
            "• **Practical care**: Drink a large glass of water right away (dehydration is one of the most common triggers), rest in a quiet, dark room, and apply a cool damp cloth or ice pack to your forehead or temples.\n\n"
            "Did this headache start suddenly, or has it built up gradually over the day?"
            f"{vitals_note}"
        )

    # 10. Fever / Body Aches
    if any(k in text_lower for k in ["fever", "chills", "body ache", "body pain", "aches"]):
        return (
            "For fever and body aches:\n\n"
            "• **Antipyretics & Pain Relief**: **Paracetamol (500–650 mg every 6 hours)** or **Ibuprofen (400 mg every 8 hours with food)** help bring down elevated body temperature and relieve muscle aches.\n"
            "• **Supportive measures**: Rest, light clothing, and plenty of fluids (water, electrolyte solutions) are vital to prevent dehydration.\n\n"
            "Have you measured your body temperature with a thermometer?"
            f"{vitals_note}"
        )

    # 11. General Medicine / Remedy Query
    if any(w in text_lower for w in ["medicine", "medicines", "medication", "pill", "tablet", "remedy", "syrup"]):
        return (
            f"Regarding medicines for your symptoms ({latest_message}):\n\n"
            "Common over-the-counter options include **Paracetamol (500–650 mg)** for pain and fever, **Ibuprofen (400 mg with food)** for inflammation and pain, and targeted syrups like **Dextromethorphan** for dry cough or **Guaifenesin** for chest congestion.\n\n"
            "Could you specify which symptom is bothering you the most right now so I can recommend the most appropriate option?"
            f"{vitals_note}"
        )

    # 12. Conversational clarification or update
    if intent.get("intent") == "SYMPTOM_UPDATE" or intent.get("intent") == "CONVERSATIONAL":
        return (
            f"Understood regarding that detail ({latest_message}). Knowing this helps clarify the pattern. "
            "Are you noticing any other symptoms accompanying this, or does anything specifically aggravate or relieve it?"
            f"{vitals_note}"
        )

    # General adaptive guidance
    return (
        f"I hear you regarding '{latest_message}'. To provide the best guidance, "
        "could you describe how severe it feels and whether you have any other accompanying symptoms?"
        f"{vitals_note}"
    )

# Backward compatibility alias
generate_fallback_clinical_response = lambda state, user_message, prior_messages=None: generate_intelligent_clinical_fallback(
    state=state,
    latest_message=user_message,
    intent=classify_message_intent(user_message, prior_messages),
    memory=filter_relevant_memory(user_message, classify_message_intent(user_message, prior_messages), state),
    vitals_summary=format_vitals_summary(state.vitals),
)


SYSTEM_CLINICAL_POLICY = """You are MEDAI, an intelligent, empathetic, and context-aware clinical medical AI assistant.
You speak naturally, clearly, and conversationally, like an experienced and attentive clinician in a direct consultation with a patient.

CORE OPERATIONAL PRINCIPLES (Strictly Enforced):

1. HIERARCHY OF PRIORITIES:
   - Primary: Medical safety and immediate emergency identification.
   - Second: Directly addressing the patient's latest message and their immediate question or concern.
   - Third: Immediate conversation context and continuity from recent turns.
   - Fourth: Historical medical memory (retrieve and mention ONLY if materially relevant to the current issue or safety).
   - Fifth: Natural conversational tone and concise, adaptive response length.

2. ADAPTIVE RESPONSE LENGTH & STYLE:
   - Simple informational question -> 1 to 3 direct, clear sentences. Never turn a simple question into a lengthy clinical report.
   - Direct follow-up / clarification -> Seamlessly incorporate the answer into your ongoing assessment in 1 concise paragraph.
   - Symptom discussion -> 2 short paragraphs focusing on likely mechanisms, practical relief, and what to monitor.
   - Emergency -> Immediate, clear instructions to seek urgent care. No casual chatter.

3. ELIMINATE REPETITION & ROBOTIC SPEECH:
   - NEVER use robotic openers such as "I understand your concern", "Thank you for sharing that", "I'm sorry to hear that you are experiencing...", or "It's important to remember". Start directly with the response.
   - NEVER repeat the patient's entire medical history back to them.
   - NEVER re-explain facts or precautions you already explained earlier in the conversation.
   - Do NOT dump over-the-counter medicine dosages unless specifically asked or clinically pertinent.
   - Do NOT end every response with a canned invitation or generic disclaimer.
   - Only ask a follow-up question if the answer is essential for clinical decision making (at most ONE focused question). Never ask for information the patient already provided.

4. MEDICAL MEMORY & RELEVANCE DISCIPLINE:
   - Never bring up past symptoms or medical history if they have no clinical bearing on the current complaint.
   - If a symptom is marked RESOLVED, do NOT treat it as an active issue or ask about it unless the patient brings it up.
   - When the patient reports a symptom that could be an adverse effect or interaction with their recorded medications, address that connection cautiously and responsibly.
   - If user statements conflict with previous records (discrepancy), politely clarify rather than silently assuming one is true.
   - Do not invent diagnoses or make definitive medical pronouncements. Provide balanced, grounded clinical explanations.
"""


async def generate_conversational_reply(
    llm: LLMProvider,
    state: PatientState,
    latest_message: str,
    conversation_history: list[dict[str, str]],
    db: Any | None = None,
    conversation_id: Any | None = None,
) -> tuple[str, bool, str | None, str | None]:
    """Generates an intelligent, context-aware, non-repetitive clinical conversational reply.

    Returns:
        tuple of (reply_text, is_assessment, assessment_status, escalation)
    """

    # 1. Emergency Safety Triage (Part 8)
    is_emergency, emergency_reason = detect_emergency(latest_message)
    if is_emergency:
        logger.warning("Emergency triage triggered for message: %s (reason=%s)", latest_message, emergency_reason)
        reply = generate_emergency_response(latest_message, emergency_reason)
        return reply, True, "emergency", emergency_reason

    # 2. Classify Message Intent & Conversational State (Part 3 & 5)
    intent = classify_message_intent(latest_message, conversation_history)

    # 3. Detect and Update Resolved Symptoms dynamically (Part 2 C)
    all_known_symptoms = [s.symptom for s in getattr(state, "symptoms", [])]
    resolved_detected = detect_resolved_symptoms(latest_message, all_known_symptoms)

    if resolved_detected and db is not None and getattr(state.patient, "id", None):
        try:
            from app.diagnostics.service import update_symptom_status_by_name
            for sym in resolved_detected:
                await update_symptom_status_by_name(db, state.patient.id, sym, "resolved")
        except Exception as e:
            logger.warning("Could not persist resolved symptoms to DB: %s", e)

    # 4. Filter Medical Memory based on Clinical Relevance (Part 2 B & C)
    memory = filter_relevant_memory(
        latest_message=latest_message,
        intent=intent,
        state=state,
        resolved_symptoms=resolved_detected,
    )

    # 5. Format Vitals & Wearable Trends
    vitals_text = format_vitals_summary(state.vitals)
    trends_text = ""
    if db is not None and getattr(state.patient, "id", None):
        try:
            from app.vitals.trends import calculate_vital_trends, format_trends_summary
            trends_data = await calculate_vital_trends(db, state.patient.id, window_hours=24)
            trends_text = format_trends_summary(trends_data) or ""
        except Exception as e:
            logger.debug("Could not calculate vital trends: %s", e)

    # 6. Build Context Blocks for Gemini (Part 2 & Part 9)
    context_blocks = format_context_prompt_blocks(
        latest_message=latest_message,
        intent=intent,
        memory=memory,
        vitals_summary=vitals_text,
        trends_summary=trends_text,
    )

    # 7. Assemble Conversation History (keep last 8-10 messages for full dialogue continuity)
    history_lines = []
    # Take up to the last 10 messages
    recent_history = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
    for msg in recent_history:
        role = "Patient" if msg.get("role") == "user" else "Assistant"
        content = msg.get("content", "").strip()
        history_lines.append(f"{role}: {content}")

    # Patient's latest message is explicitly appended
    history_lines.append(f"Patient: {latest_message.strip()}")

    user_prompt = (
        f"{context_blocks}\n\n"
        "=== [CONVERSATION TRANSCRIPT] ===\n"
        + "\n".join(history_lines)
        + "\n\nAssistant:"
    )

    final_reply: str | None = None

    # 8. Call Gemini with Structured System Prompt & Temperature Tuning (Part 10)
    try:
        reply = await llm.generate(user_prompt, system=SYSTEM_CLINICAL_POLICY)
        reply = reply.strip()
        if reply:
            # Clean any artifact prefix like "Assistant:" if echoed
            if reply.lower().startswith("assistant:"):
                reply = reply[len("assistant:"):].strip()
            final_reply = reply
    except Exception as exc:
        logger.warning("LLM conversational generation failed (%s); using intelligent clinical fallback", exc)

    # 9. Fallback if LLM failed or not configured (Part 4)
    if not final_reply:
        final_reply = generate_intelligent_clinical_fallback(
            state=state,
            latest_message=latest_message,
            intent=intent,
            memory=memory,
            vitals_summary=vitals_text,
        )

    # 10. Auto-record consultation diagnostic record if health issue discussed
    if db is not None and getattr(state.patient, "id", None) and final_reply:
        try:
            from app.diagnostics.service import auto_record_consultation_diagnosis
            await auto_record_consultation_diagnosis(
                db=db,
                patient_id=state.patient.id,
                conversation_id=conversation_id,
                user_text=latest_message,
                reply_text=final_reply,
                state=state,
            )
        except Exception as e:
            logger.debug("Could not auto-record consultation diagnosis: %s", e)

    return final_reply, False, None, None
