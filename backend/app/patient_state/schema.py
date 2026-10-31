import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.patient import AllergyResponse, MedicalHistoryResponse, MedicationResponse

Certainty = Literal["user_reported", "measured", "inferred", "external"]


class ExtractedSymptom(BaseModel):
    """symptom_extraction.fields — the LLM's structured output for one symptom. Never invent
    missing values (symptom_extraction.rule): omit a field (null) rather than guess it."""

    symptom: str
    onset: str | None = None
    duration: str | None = None
    severity: int | None = Field(default=None, ge=0, le=10)
    frequency: str | None = None
    location: str | None = None
    progression: str | None = None
    triggers: str | None = None
    relieving_factors: str | None = None
    associated_symptoms: str | None = None
    certainty: Certainty = "user_reported"
    status: str = "active"


class SymptomExtractionResult(BaseModel):
    """Wrapper root object — OpenAI structured outputs requires a single JSON object, not a
    bare array, as the top-level schema."""

    symptoms: list[ExtractedSymptom]


class PatientStateIdentity(BaseModel):
    id: uuid.UUID
    age: int | None = None
    sex: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None


class PatientState(BaseModel):
    """The canonical structured state — patient_state.schema, assembled (not stored as one
    blob) from patients/patient_profiles/medical_history/allergies/current_medications/symptoms
    (Phase 1 + Phase 3 tables) and vitals (Phase 9's `vitals` snapshot table). recent_events and
    risk_factors are intentionally left empty: computing them is clinical inference, out of
    scope until Phase 4 (conversation_manager) / Phase 6 (clinical_reasoner) — populating them
    here would violate patient_state.rules ("LLM must NOT reason directly from unstructured
    conversation alone") by having this assembly step quietly do reasoning no one asked it to
    do.
    """

    patient: PatientStateIdentity
    symptoms: list[ExtractedSymptom] = Field(default_factory=list)
    medical_history: list[MedicalHistoryResponse] = Field(default_factory=list)
    allergies: list[AllergyResponse] = Field(default_factory=list)
    medications: list[MedicationResponse] = Field(default_factory=list)
    vitals: dict = Field(default_factory=dict)
    recent_events: list = Field(default_factory=list)
    risk_factors: list = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    conversation_context: dict = Field(default_factory=dict)
    data_quality: dict = Field(default_factory=dict)
    version: int = 1
