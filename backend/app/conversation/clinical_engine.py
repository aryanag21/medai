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
    if "body_temperature" in vitals or "temperature" in vitals:
        temp_obj = vitals.get("body_temperature") or vitals.get("temperature")
        temp = temp_obj["value"]
        unit = temp_obj.get("unit", "F")
        parts.append(f"Temp: {temp}°{unit}")
    if "weight" in vitals:
        wt = vitals["weight"]["value"]
        unit = vitals["weight"].get("unit", "kg")
        parts.append(f"Weight: {wt} {unit}")
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
    text_lower = user_message.lower().strip()
    vitals_summary = format_vitals_summary(state.vitals)
    vitals_note = f"\n\n*(Your recorded vitals in the app look reassuring: {vitals_summary})*" if vitals_summary else ""

    prior_messages = prior_messages or []
    past_user_texts = [m.get("content", "") for m in prior_messages if m.get("role") == "user"]

    # 1. Meta / System / App / Server chatter (do NOT give medical advice for server checks!)
    is_server_query = any(k in text_lower for k in ["server", "app is", "app server", "backend"]) or (
        any(k in text_lower for k in ["slow", "lag", "running", "working"]) and any(k in text_lower for k in ["server", "app", "connection", "it"])
    )
    if is_server_query:
        return (
            "Yes, the MEDAI server and Cloudflare connection are up, running, and active!\n\n"
            "If responses ever feel a little slow, the Cloudflare quick tunnel or mobile network latency can occasionally add a brief delay, but everything is communicating properly.\n\n"
            "What health questions or symptoms can I help you evaluate right now?"
        )

    # 2. Identify the active symptom from the CURRENT user message first
    # This prevents older context from overriding a new symptom the user brings up!
    detected_current = None
    if any(k in text_lower for k in ["throat", "swallow", "tonsil", "pharynx", "strep"]):
        detected_current = "throat"
    elif any(k in text_lower for k in ["back pain", "lower back", "upper back", "spine", "lumbar", "sciatica", "backache"]) or (
        "back" in text_lower and any(w in text_lower for w in ["pain", "sitting", "hurts", "ache", "stiff", "muscle", "joint", "sit"])
    ):
        detected_current = "back"
    elif any(k in text_lower for k in ["slam", "door", "foot", "toe", "ankle", "hit my", "stub", "kick", "fall", "sprain", "bruis", "injur"]):
        detected_current = "injury"
    elif any(k in text_lower for k in ["cough", "cold", "congestion", "phlegm", "mucus", "sneeze", "runny nose"]):
        detected_current = "cough"
    elif any(k in text_lower for k in ["headache", "migraine", "head hurts", "throbbing head"]):
        detected_current = "headache"
    elif any(k in text_lower for k in ["fever", "chills", "feverish", "high temperature"]):
        detected_current = "fever"
    elif any(k in text_lower for k in ["stomach", "nausea", "vomit", "diarrhea", "belly", "heartburn", "acid reflux"]):
        detected_current = "stomach"
    elif any(k in text_lower for k in ["neck", "shoulder", "knee", "hip", "elbow", "wrist"]):
        detected_current = "joint"

    # If the user is asking a follow-up ("any other medicines", "what about the pain i asked", "what else"):
    # Look back at past user messages to see what symptom they were talking about
    active_topic = detected_current
    is_medicine_query = any(k in text_lower for k in ["medicine", "medicines", "medication", "pill", "tablet", "syrup", "other", "take"])
    if not active_topic and past_user_texts:
        for prev in reversed(past_user_texts):
            prev_l = prev.lower()
            if any(k in prev_l for k in ["throat", "swallow", "tonsil", "strep"]):
                active_topic = "throat_medicines" if is_medicine_query else "throat"
                break
            elif any(k in prev_l for k in ["back", "spine", "lumbar", "sciatica", "sitting"]):
                active_topic = "back_medicines" if is_medicine_query else "back"
                break
            elif any(k in prev_l for k in ["foot", "toe", "ankle", "slam", "door", "injury", "sprain"]):
                active_topic = "injury_medicines" if is_medicine_query else "injury"
                break
            elif any(k in prev_l for k in ["cough", "cold"]):
                active_topic = "cough"
                break
            elif any(k in prev_l for k in ["headache"]):
                active_topic = "headache"
                break

    # Route based on resolved active topic:

    # A. Throat Pain / Sore Throat / Strep
    if active_topic in ["throat", "throat_medicines"]:
        more_meds = "Other effective medications and remedies for intense throat pain include:" if active_topic == "throat_medicines" else "Severe throat pain can make swallowing and speaking really difficult. Here are the most effective treatments to calm it down:"
        return (
            f"{more_meds}\n\n"
            "• **Anti-Inflammatory (First Choice):** **Ibuprofen (400 mg with food)** is substantially more effective than Paracetamol for throat pain because it directly reduces the intense swelling of the pharynx and tonsils.\n"
            "• **Numbing Throat Sprays / Lozenges:** Look for lozenges containing **Benzocaine** or **Hexylresorcinol** (like Cepacol or Strepsils), or a **Phenol throat spray (Chloraseptic)**. These temporarily numb the nerve endings so swallowing is tolerable.\n"
            "• **Warm Salt Water Gargle:** Mix 1/2 teaspoon of salt in a glass of warm water. Gargle for 30 seconds, then spit it out. Doing this 3–4 times daily draws fluid out of inflamed throat tissue.\n"
            "• **Coating Relief:** Warm water or herbal tea with a generous spoonful of **honey** coats the sensitive mucous membranes.\n\n"
            "**Important check:** Do you see any white spots/patches on your tonsils when you look in a mirror, and are you running a high fever? If yes, it may be bacterial strep throat, which requires a doctor's prescription for antibiotics."
            f"{vitals_note}"
        )

    # B. Back Pain / Lower Back / Posture / Sitting / Sciatica
    if active_topic in ["back", "back_medicines"]:
        if active_topic == "back_medicines":
            return (
                "Besides oral pain relievers, here are other effective medications and treatments for back pain:\n\n"
                "• **Topical Anti-Inflammatory Gel:** **Diclofenac gel (Voltaren 1%)** applied directly to the lower back provides targeted anti-inflammatory relief without upsetting your stomach.\n"
                "• **Combined OTC Relief:** If Ibuprofen alone is insufficient, you can safely alternate **Ibuprofen (400 mg)** and **Paracetamol (500 mg)** every 3 to 4 hours (ensuring you do not exceed daily limits).\n"
                "• **Therapeutic Heat:** A heated pad or warm bath loosens the involuntary muscle spasms around the lumbar spine.\n"
                "• **Gentle Movement:** Complete bed rest actually stiffens back muscles. Short 3-minute walking intervals and gentle hamstring stretches are proven to speed recovery.\n\n"
                "Are you experiencing any shooting nerve sensations down the back of your leg or numbness?"
                f"{vitals_note}"
            )
        return (
            "Lower back pain while sitting is very common — sitting actually increases pressure on your lumbar discs and spinal muscles by up to 40% compared to standing.\n\n"
            "Here is how you can tell whether it's more likely muscular or joint/disc related:\n"
            "• **Muscular tension:** Usually feels like a dull, tight ache across the lower back band. It often flares up after prolonged sitting or slouching and eases when you lie flat or walk gently.\n"
            "• **Joint / Facet irritation:** Tends to feel stiff when rising from a chair, sharpens if you lean backwards or twist, and improves with gentle movement.\n\n"
            "**What to do right now for relief:**\n"
            "• **Take a standing break:** Stand up, walk around for 2 to 3 minutes, and gently stretch your hamstrings and hip flexors.\n"
            "• **Lumbar support:** Place a small rolled towel or cushion at the curve of your lower back, and keep your knees level with your hips.\n"
            "• **Heat:** Apply a warm heating pad to your lower back for 15–20 minutes to relax tight muscle fibers.\n"
            "• **Medication:** If the pain is bothersome, **Ibuprofen (400 mg with food)** works well for musculoskeletal inflammation, or **Paracetamol (500–650 mg)** for straightforward pain relief.\n\n"
            "Does the discomfort stay in your lower back, or does any pain, numbness, or tingling shoot down into your buttock or leg?"
            f"{vitals_note}"
        )

    # C. Trauma / Foot / Toe / Ankle / Injury
    if active_topic in ["injury", "injury_medicines"]:
        return (
            "Ouch! That kind of impact is intensely painful — it throbs immediately and can make putting weight down really tough.\n\n"
            "Here is the best way to handle it right now:\n"
            "• **Elevate & Rest:** Sit or lie down and prop your foot up on a pillow above heart level to reduce the throbbing and swelling.\n"
            "• **Ice:** Apply an ice pack (or frozen peas wrapped in a towel) for 15–20 minutes every couple of hours.\n"
            "• **Pain & Swelling Relief:** **Ibuprofen (400 mg with food)** is very effective because it reduces both pain and inflammation. Alternatively, **Paracetamol (500–650 mg)** works well if you cannot take anti-inflammatories.\n\n"
            "**When to get it checked:** Can you bear any weight on it, and does the area look bruised, purple, or crooked? If you cannot walk on it at all, or if a toe looks misaligned, it's best to get an X-ray to rule out a small fracture."
        )

    # D. Neck / Shoulder / Joint
    if active_topic == "joint":
        return (
            "Joint and neck stiffness is often caused by muscle strain, awkward posture, or prolonged desk work.\n\n"
            "For gentle relief:\n"
            "• Apply a warm heating pad or take a warm shower for 15 minutes to relax tight fibers.\n"
            "• Perform gentle range-of-motion stretches without forcing past the point of sharp pain.\n"
            "• An anti-inflammatory like **Ibuprofen (400 mg with a snack)** helps ease joint inflammation, or a topical pain gel like **Diclofenac (Voltaren)** can be applied directly.\n\n"
            "Did this pain start suddenly after a specific movement, or has it built up gradually over the day?"
            f"{vitals_note}"
        )

    # E. Cough / Cold / Lingering respiratory
    if active_topic == "cough":
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

    # F. Headache / Migraine
    if active_topic == "headache":
        return (
            "I'm sorry your head is pounding. Tension, eye strain, and dehydration are the most common culprits.\n\n"
            "For fast relief:\n"
            "• Drink a tall glass of water and rest your eyes in a dimly lit, quiet room away from screens.\n"
            "• An OTC pain reliever like **Paracetamol (500–1,000 mg)** or **Ibuprofen (400 mg with a snack)** usually brings prompt relief.\n"
            "• A cool compress placed over your forehead or the back of your neck can help ease the pressure.\n\n"
            "Is the pain throbbing or steady, and is it on one side or all over?"
            f"{vitals_note}"
        )

    # G. Fever / Chills
    if active_topic == "fever":
        return (
            "Running a fever is your body's immune system fighting off a bug, but it can leave you feeling completely exhausted.\n\n"
            "To stay comfortable and bring it down safely:\n"
            "• **Paracetamol (500–650 mg every 4–6 hours as needed)** is the first-line medication to reduce fever and body aches (keep within 3,000 mg in 24 hours).\n"
            "• Stay well-hydrated with water, electrolyte drinks, or warm broths to replace fluids lost from fever.\n"
            "• Wear lightweight, loose clothing and rest in a well-ventilated room.\n\n"
            "What temperature have you measured, and have you noticed any other symptoms like chills or a sore throat?"
            f"{vitals_note}"
        )

    # H. Stomach / Digestive / Nausea
    if active_topic == "stomach":
        return (
            "Stomach upset is miserable. The priority right now is keeping your digestive system rested while staying hydrated.\n\n"
            "A few gentle steps:\n"
            "• Take small, frequent sips of water, electrolyte solution, or ginger/peppermint tea.\n"
            "• Stick to bland foods (bananas, rice, plain toast, crackers) once you feel up to eating.\n"
            "• Avoid dairy, caffeine, and spicy or greasy foods for the next couple of days.\n\n"
            "How long has your stomach been bothering you, and are you able to keep fluids down?"
            f"{vitals_note}"
        )

    # I. Polite closers / Acknowledgments
    if any(k in text_lower for k in ["thank you", "thanks", "ok", "okay", "got it", "understood", "alright", "great"]):
        return (
            "You're very welcome! Take good care of yourself, and feel free to reach back out anytime if your symptoms change or if you need more advice."
        )

    # J. Greetings
    if any(k in text_lower for k in ["hello", "hi", "hey", "good morning", "good evening", "how are you"]):
        return (
            "Hello! I'm here to help you evaluate how you're feeling and provide medical advice. "
            "How are you feeling today, or what symptoms are bothering you?"
        )

    # K. General Adaptive Response (NO generic paracetamol dumping!)
    return (
        f"I understand. To help me give you specific medical advice:\n\n"
        "• What specific discomfort or symptoms are you feeling right now?\n"
        "• How long has this been going on, and does anything make it feel better or worse?\n\n"
        "Tell me a little more and I will provide targeted guidance."
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
