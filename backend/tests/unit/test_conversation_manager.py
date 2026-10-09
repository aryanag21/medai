import pytest

from app.conversation.manager import generate_reply, select_action, validate_action
from app.conversation.missing_info import identify_missing_info
from app.patient_state.schema import ExtractedSymptom
from tests.fakes import FakeLLMProvider, NotConfiguredLLMProvider, empty_patient_state as _empty_state


def test_missing_info_priority_order_puts_high_impact_before_lower_priority() -> None:
    state = _empty_state(
        symptoms=[ExtractedSymptom(symptom="headache", severity=None, certainty="user_reported")],
        unknowns=["age", "allergies"],
    )

    items = identify_missing_info(state)
    categories = [item.category for item in items]

    assert categories[0] == "high_impact_missing_information"
    assert "medication_allergy_safety" in categories
    assert categories[-1] == "lower_priority_context"


def test_identify_missing_info_never_asks_about_known_fields() -> None:
    state = _empty_state(unknowns=[])  # nothing missing
    assert identify_missing_info(state) == []


def test_select_action_ask_question_when_items_present() -> None:
    state = _empty_state(unknowns=["age"])
    items = identify_missing_info(state)
    assert select_action(items) == "ASK_QUESTION"


def test_select_action_run_assessment_when_nothing_missing() -> None:
    assert select_action([]) == "RUN_ASSESSMENT"


def test_validate_action_rejects_unlisted_action() -> None:
    with pytest.raises(ValueError):
        validate_action("DELETE_PATIENT")


def test_validate_action_rejects_not_yet_implemented_action() -> None:
    with pytest.raises(NotImplementedError):
        validate_action("ESCALATE")


async def test_generate_reply_uses_llm_phrasing_when_available() -> None:
    class EchoingFakeLLM(FakeLLMProvider):
        async def generate(self, prompt: str, *, system: str | None = None) -> str:
            return "Custom phrased question?"

    state = _empty_state(unknowns=["age"])
    reply = await generate_reply(EchoingFakeLLM(), state)
    assert reply == "Custom phrased question?"


async def test_generate_reply_falls_back_to_template_when_llm_not_configured() -> None:
    state = _empty_state(unknowns=["age"])
    reply = await generate_reply(NotConfiguredLLMProvider(), state)
    assert reply == "Could you tell me your age?"


async def test_generate_reply_returns_none_when_nothing_missing() -> None:
    """None signals the caller (app/api/messages.py) to run the real RUN_ASSESSMENT pipeline —
    this function deliberately has no DB/evidence/safety access to do that itself."""

    state = _empty_state(unknowns=[])
    reply = await generate_reply(NotConfiguredLLMProvider(), state)
    assert reply is None


async def test_generate_reply_does_not_repeat_previously_asked_questions() -> None:
    state = _empty_state(unknowns=["allergies", "current_medications"])
    # Turn 1: No previous questions asked -> asks about allergies
    reply1 = await generate_reply(NotConfiguredLLMProvider(), state, past_assistant_messages=[])
    assert "allerg" in reply1.lower()

    # Turn 2: Assistant previously asked about allergies -> advances to medications, does NOT repeat allergies
    reply2 = await generate_reply(
        NotConfiguredLLMProvider(),
        state,
        past_assistant_messages=["Could you tell me about any known drug allergies?"],
    )
    assert "allerg" not in reply2.lower()
    assert "medication" in reply2.lower()

    # Turn 3: Both allergies and medications were asked -> returns None (ready for assessment)
    reply3 = await generate_reply(
        NotConfiguredLLMProvider(),
        state,
        past_assistant_messages=[
            "Could you tell me about any known drug allergies?",
            "Could you tell me about any medications you're currently taking?",
        ],
    )
    assert reply3 is None
