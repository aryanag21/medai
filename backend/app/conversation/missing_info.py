from typing import Literal

from pydantic import BaseModel

from app.patient_state.schema import PatientState

QuestionCategory = Literal[
    "emergency_indicators",
    "high_impact_missing_information",
    "medication_allergy_safety",
    "relevant_history",
    "required_measurements",
    "lower_priority_context",
]

# conversation_manager.question_priority, highest first.
_CATEGORY_ORDER: list[QuestionCategory] = [
    "emergency_indicators",
    "high_impact_missing_information",
    "medication_allergy_safety",
    "relevant_history",
    "required_measurements",
    "lower_priority_context",
]

_DEMOGRAPHIC_HINTS: dict[str, str] = {
    "age": "your age",
    "sex": "your sex",
    "height_cm": "your height",
    "weight_kg": "your weight",
}


class MissingInfoItem(BaseModel):
    field: str
    category: QuestionCategory
    prompt_hint: str  # completes "Could you tell me {prompt_hint}?"


def _passes_pre_question_checks(item: MissingInfoItem, state: PatientState) -> bool:
    """conversation_manager.pre_question_checks, applied per candidate item:

    - already_in_state: satisfied by construction — every item here was derived from a gap in
      `state`, so nothing already-known is ever offered as a candidate.
    - available_from_authorised_health_source: no health-platform integration exists yet
      (Phase 10) — nothing to check against, so this never suppresses a candidate today.
    - safely_obtainable_from_validated_measurement: no vital/device subsystem exists yet
      (Phase 9) — same as above.
    - actually_necessary: every candidate this module generates comes from a fixed,
      clinically-relevant field list (see identify_missing_info), so this is true by
      construction rather than computed per-item.
    """

    return True


def identify_missing_info(state: PatientState, asked_fields: set[str] | None = None) -> list[MissingInfoItem]:
    """conversation_manager.flow step 3 (identify_missing_clinically_relevant_information).

    Deterministic and DB-driven only — never invents a value, never infers from unstructured
    text (patient_state.rules). The 'emergency_indicators' and 'required_measurements' tiers
    are structurally present in the priority order but have no generator here: real emergency
    detection needs sourced, authoritative rules (safety_engine.emergency_triage, Phase 7) and
    measurements need the vital/device subsystem (Phase 9) — conversation_manager must not
    invent either.
    """

    asked = asked_fields or set()
    items: list[MissingInfoItem] = []

    for symptom in state.symptoms:
        if symptom.severity is None and f"symptom:{symptom.symptom}:severity" not in asked and "severity" not in asked:
            items.append(
                MissingInfoItem(
                    field=f"symptom:{symptom.symptom}:severity",
                    category="high_impact_missing_information",
                    prompt_hint=f"how severe your {symptom.symptom} is, on a scale of 0 to 10",
                )
            )
        if symptom.duration is None and f"symptom:{symptom.symptom}:duration" not in asked and "duration" not in asked:
            items.append(
                MissingInfoItem(
                    field=f"symptom:{symptom.symptom}:duration",
                    category="high_impact_missing_information",
                    prompt_hint=f"how long you've had {symptom.symptom}",
                )
            )
        if symptom.onset is None and f"symptom:{symptom.symptom}:onset" not in asked and "onset" not in asked:
            items.append(
                MissingInfoItem(
                    field=f"symptom:{symptom.symptom}:onset",
                    category="high_impact_missing_information",
                    prompt_hint=f"when your {symptom.symptom} started",
                )
            )

    if "allergies" in state.unknowns and "allergies" not in asked:
        items.append(
            MissingInfoItem(field="allergies", category="medication_allergy_safety", prompt_hint="about any known drug allergies")
        )
    if "current_medications" in state.unknowns and "current_medications" not in asked:
        items.append(
            MissingInfoItem(
                field="current_medications",
                category="medication_allergy_safety",
                prompt_hint="about any medications you're currently taking",
            )
        )

    if "known_conditions" in state.unknowns and "known_conditions" not in asked:
        items.append(
            MissingInfoItem(
                field="known_conditions",
                category="relevant_history",
                prompt_hint="about any relevant medical history or ongoing conditions",
            )
        )

    for field, hint in _DEMOGRAPHIC_HINTS.items():
        if field in state.unknowns and field not in asked:
            items.append(MissingInfoItem(field=field, category="lower_priority_context", prompt_hint=hint))

    items = [item for item in items if _passes_pre_question_checks(item, state)]
    items.sort(key=lambda item: _CATEGORY_ORDER.index(item.category))
    return items
