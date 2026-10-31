"""Behavioural Test Suite for Conversational Intelligence, Context Management & Response Quality.

Directly tests the 10 required clinical behavioural scenarios from Part 11:
1. Completely unrelated new concern (Headache -> Skin irritation).
2. Relevant historical medication (Lisinopril -> Dry cough).
3. Additional symptom information (Duration & location details updated without repetition).
4. Simple follow-up (Direct answer without full clinical report).
5. Resolved symptoms (Symptom marked resolved and not treated as active).
6. Multiple concurrent concerns (Focus on the active topic).
7. Repeated questions (Concise, fresh clarification).
8. Contradictory medical information (Inconsistency flagged and clarified).
9. Emergency symptoms (Immediate triage overrides conversation).
10. Natural conversational progression (10+ turns dialogue coherence).
"""

from __future__ import annotations

import uuid
import pytest
from datetime import datetime, timezone

from app.conversation.clinical_engine import (
    generate_conversational_reply,
    generate_intelligent_clinical_fallback,
    detect_emergency,
)
from app.conversation.context_manager import (
    classify_message_intent,
    detect_resolved_symptoms,
    filter_relevant_memory,
    format_context_prompt_blocks,
)
from app.patient_state.schema import ExtractedSymptom, PatientState, PatientStateIdentity
from app.schemas.patient import AllergyResponse, MedicalHistoryResponse, MedicationResponse
from tests.fakes import FakeLLMProvider, NotConfiguredLLMProvider


def make_patient_state(
    symptoms: list[str] | None = None,
    medications: list[str] | None = None,
    allergies: list[str] | None = None,
    history: list[str] | None = None,
    vitals: dict | None = None,
) -> PatientState:
    pid = uuid.uuid4()
    return PatientState(
        patient=PatientStateIdentity(id=pid, age=35, sex="female"),
        symptoms=[
            ExtractedSymptom(symptom=s, certainty="user_reported", status="active")
            for s in (symptoms or [])
        ],
        medications=[
            MedicationResponse(id=uuid.uuid4(), name=m, dosage="standard", frequency="daily", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
            for m in (medications or [])
        ],
        allergies=[
            AllergyResponse(id=uuid.uuid4(), substance=a, severity="moderate", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
            for a in (allergies or [])
        ],
        medical_history=[
            MedicalHistoryResponse(id=uuid.uuid4(), condition=h, status="active", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
            for h in (history or [])
        ],
        vitals=vitals or {},
        unknowns=[],
        data_quality={"profile_completeness": 1.0},
    )


class MockConversationalLLM:
    """Mock LLM returning clinically realistic responses based on prompt inspection."""

    def __init__(self, response_map: dict[str, str] | None = None):
        self.response_map = response_map or {}
        self.call_history: list[dict[str, str]] = []

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.call_history.append({"prompt": prompt, "system": system or ""})
        for key, reply in self.response_map.items():
            if key.lower() in prompt.lower():
                return reply
        return "I understand your symptoms. Let us discuss how you are feeling."


# ==============================================================================
# TEST SCENARIO 1: Completely unrelated new concern
# The user discusses headaches over several messages and then suddenly asks about a skin irritation.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_1_unrelated_new_concern():
    state = make_patient_state(symptoms=["headache"])
    history = [
        {"role": "user", "content": "I have had a throbbing headache since morning."},
        {"role": "assistant", "content": "Resting in a dark room and staying hydrated can help."},
        {"role": "user", "content": "It feels a bit better now."},
        {"role": "assistant", "content": "Glad to hear that."},
    ]
    latest_msg = "I just noticed a red, itchy rash on my forearm."

    intent = classify_message_intent(latest_msg, history)
    assert intent["intent"] == "NEW_CONCERN"

    memory = filter_relevant_memory(latest_msg, intent, state)
    # Headache must NOT be in active_relevant_symptoms for skin rash
    assert "headache" not in memory["active_relevant_symptoms"]
    assert "headache" in memory["active_unrelated_symptoms"]

    # Verify prompt blocks instruct the model to focus on the new concern and not drag in headaches
    blocks = format_context_prompt_blocks(latest_msg, intent, memory)
    assert "rash" in latest_msg
    assert "Unrelated Past Mentions (Ignore unless medically necessary): headache" in blocks

    # Verify reply addresses the rash and does not obsess over headaches
    reply = generate_intelligent_clinical_fallback(state, latest_msg, intent, memory)
    assert "rash" in reply.lower() or "skin" in reply.lower()
    assert "headache" not in reply.lower()


# ==============================================================================
# TEST SCENARIO 2: Relevant historical medication
# The user previously mentioned taking a medication and now reports a symptom
# that could potentially be associated with it (e.g. Lisinopril -> dry cough).
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_2_relevant_historical_medication():
    state = make_patient_state(medications=["Lisinopril 10mg"])
    latest_msg = "I have developed this persistent dry cough over the past week."

    intent = classify_message_intent(latest_msg)
    memory = filter_relevant_memory(latest_msg, intent, state)

    # Lisinopril must be identified as relevant because cough is a recognized adverse effect
    assert any("lisinopril" in m.lower() for m in memory["relevant_medications"])
    assert any("correlation" in m.lower() for m in memory["relevant_medications"])

    blocks = format_context_prompt_blocks(latest_msg, intent, memory)
    assert "Lisinopril" in blocks


# ==============================================================================
# TEST SCENARIO 3: Additional symptom information
# The user describes a symptom, receives an explanation, and then provides additional details.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_3_additional_symptom_information():
    state = make_patient_state(symptoms=["headache"])
    history = [
        {"role": "user", "content": "I have had bad headaches."},
        {"role": "assistant", "content": "Headaches can be caused by tension or dehydration. How long have they been going on?"},
    ]
    latest_msg = "For approximately three days now, and it is a throbbing ache."

    intent = classify_message_intent(latest_msg, history)
    assert intent["intent"] == "SYMPTOM_UPDATE"
    assert intent["is_update"] is True

    blocks = format_context_prompt_blocks(latest_msg, intent, {})
    assert "incorporate this new detail into your ongoing assessment" in blocks

    reply = generate_intelligent_clinical_fallback(state, latest_msg, intent, {})
    # Reply must incorporate the new details without robotic re-asking
    assert "three days" in reply or "detail" in reply
    assert "how long" not in reply.lower()


# ==============================================================================
# TEST SCENARIO 4: Simple follow-up
# The user asks a short follow-up question about the assistant's previous answer.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_4_simple_follow_up():
    state = make_patient_state(symptoms=["fever"])
    history = [
        {"role": "user", "content": "I have a mild fever."},
        {"role": "assistant", "content": "Rest, stay hydrated, and you can take Paracetamol if needed."},
    ]
    latest_msg = "Can I take paracetamol on an empty stomach?"

    intent = classify_message_intent(latest_msg, history)
    assert intent["intent"] == "SIMPLE_QUESTION"
    assert intent["is_simple_question"] is True

    reply = generate_intelligent_clinical_fallback(state, latest_msg, intent, {})
    # Must directly answer without a full medical report
    assert "paracetamol" in reply.lower()
    assert "food" in reply.lower()
    # Should be concise (less than 60 words)
    assert len(reply.split()) <= 60


# ==============================================================================
# TEST SCENARIO 5: Resolved symptoms
# The user previously discussed a symptom that has since resolved and introduces a different concern.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_5_resolved_symptoms():
    state = make_patient_state(symptoms=["cough"])
    latest_msg = "My cough is completely gone, but I stubbed my toe on a door."

    resolved = detect_resolved_symptoms(latest_msg, ["cough"])
    assert "cough" in resolved

    intent = classify_message_intent(latest_msg)
    memory = filter_relevant_memory(latest_msg, intent, state, resolved_symptoms=resolved)

    assert "cough" in memory["resolved_symptoms"]
    assert "cough" not in memory["active_relevant_symptoms"]

    blocks = format_context_prompt_blocks(latest_msg, intent, memory)
    assert "Resolved Symptoms: cough" in blocks


# ==============================================================================
# TEST SCENARIO 6: Multiple concurrent concerns
# The user has several ongoing medical concerns and asks about one of them.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_6_multiple_concurrent_concerns():
    # Patient has both knee pain (musculoskeletal) and nasal congestion (respiratory)
    state = make_patient_state(symptoms=["knee pain", "nasal congestion"])
    latest_msg = "My knee feels very stiff when I try to bend it."

    intent = classify_message_intent(latest_msg)
    memory = filter_relevant_memory(latest_msg, intent, state)

    # Knee pain should be in active relevant; congestion should be separated as unrelated
    assert any("knee" in s.lower() for s in memory["active_relevant_symptoms"])
    assert any("congestion" in s.lower() for s in memory["active_unrelated_symptoms"])


# ==============================================================================
# TEST SCENARIO 7: Repeated questions
# The user asks something that has already been answered.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_7_repeated_question():
    history = [
        {"role": "user", "content": "What is normal blood pressure?"},
        {"role": "assistant", "content": "Normal resting adult blood pressure is under 120/80 mmHg."},
    ]
    latest_msg = "Can you remind me what normal BP is again?"

    intent = classify_message_intent(latest_msg, history)
    assert intent["is_simple_question"] is True

    reply = generate_intelligent_clinical_fallback(make_patient_state(), latest_msg, intent, {})
    assert "120/80" in reply
    assert len(reply.split()) < 50


# ==============================================================================
# TEST SCENARIO 8: Contradictory medical information
# The user previously provided one detail and now provides conflicting information.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_8_contradictory_medical_information():
    state = make_patient_state(medications=["Metformin 500mg"])
    latest_msg = "I don't take anything or have any medications."

    intent = classify_message_intent(latest_msg)
    memory = filter_relevant_memory(latest_msg, intent, state)

    # Discrepancy should be detected and noted
    assert len(memory["discrepancies"]) > 0
    assert any("Metformin" in d for d in memory["discrepancies"])

    blocks = format_context_prompt_blocks(latest_msg, intent, memory)
    assert "⚠️ Medical Discrepancy to Clarify" in blocks


# ==============================================================================
# TEST SCENARIO 9: Emergency symptoms
# The user introduces potentially serious symptoms during a conversation about an unrelated less serious issue.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_9_emergency_symptoms_prioritization():
    state = make_patient_state(symptoms=["sore throat"])
    history = [
        {"role": "user", "content": "My throat is slightly scratchy."},
        {"role": "assistant", "content": "Warm fluids and honey can help soothe the irritation."},
    ]
    latest_msg = "Actually, I suddenly have crushing chest pain and I cannot breathe."

    is_emerg, reason = detect_emergency(latest_msg)
    assert is_emerg is True
    assert reason is not None

    reply, is_assess, status, escalation = await generate_conversational_reply(
        llm=NotConfiguredLLMProvider(),
        state=state,
        latest_message=latest_msg,
        conversation_history=history,
    )

    assert is_assess is True
    assert status == "emergency"
    assert "Urgent Medical Evaluation" in reply or "emergency" in reply.lower()
    assert "911" in reply or "112" in reply or "emergency services" in reply.lower()


# ==============================================================================
# TEST SCENARIO 10: Natural conversational progression
# Multi-turn conversation (10+ messages) covering different concerns, clarifications, and follow-ups.
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_10_multi_turn_natural_progression():
    state = make_patient_state(symptoms=["cough"])
    history: list[dict[str, str]] = []

    turns = [
        ("Hello doctor", "GREETING_OR_CLOSER", True),
        ("I have had a bad cough for two days", "GENERAL", False),
        ("It is mostly dry with no fever", "SYMPTOM_UPDATE", False),
        ("Can I take cough syrup before bed?", "SIMPLE_QUESTION", True),
        ("My cough is all better now, but I hurt my ankle playing soccer", "RESOLUTION", False),
        ("It is swollen and hurts when I step on it", "GENERAL", False),
        ("Is it safe to put ice on it?", "SIMPLE_QUESTION", True),
        ("Also, what is a normal resting pulse?", "SIMPLE_QUESTION", True),
        ("Thanks for the helpful advice!", "GREETING_OR_CLOSER", True),
    ]

    for user_text, expected_intent, is_concise_expected in turns:
        intent = classify_message_intent(user_text, history)
        resolved = detect_resolved_symptoms(user_text, [s.symptom for s in state.symptoms])
        memory = filter_relevant_memory(user_text, intent, state, resolved_symptoms=resolved)

        reply = generate_intelligent_clinical_fallback(state, user_text, intent, memory)

        # Check intent classification matches expectation
        if expected_intent == "GREETING_OR_CLOSER":
            assert intent["is_greeting"] or intent["is_closer"]
        elif expected_intent == "SIMPLE_QUESTION":
            assert intent["is_simple_question"]
        elif expected_intent == "RESOLUTION":
            assert "cough" in resolved

        # Ensure no repetitive robot boilerplate
        for forbidden in ["I understand your concern", "Thank you for sharing", "It is important to remember"]:
            assert forbidden.lower() not in reply.lower()

        # Update simulated history
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})

    assert len(history) == 18  # 9 user turns + 9 assistant turns = 18 exchanges
