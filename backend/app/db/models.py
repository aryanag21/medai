import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.encrypted_types import EncryptedString
from app.db.session import Base

# BAAI/bge-large-en-v1.5 (app/providers/embeddings/sentence_transformers_provider.py) — 1024-dim.
EMBEDDING_DIMENSIONS = 1024


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    patient: Mapped["Patient"] = relationship(back_populates="user", uselist=False)


class Patient(Base):
    """A patient record, 1:1 with a user — this prototype models a single-patient-per-account
    personal health assistant, per ux.primary_action "Talk to Health AI"."""

    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="patient")
    profile: Mapped["PatientProfile"] = relationship(back_populates="patient", uselist=False)
    medical_history: Mapped[list["MedicalHistory"]] = relationship(back_populates="patient")
    allergies: Mapped[list["Allergy"]] = relationship(back_populates="patient")
    current_medications: Mapped[list["CurrentMedication"]] = relationship(back_populates="patient")


class PatientProfile(Base):
    """Covers patient_profile.required_fields that are scalar demographic/consent data.
    known_conditions/allergies/current_medications/relevant_history live in their own tables
    per database.tables, and are assembled into the logical "profile" at the API layer."""

    __tablename__ = "patient_profiles"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patients.id"), primary_key=True
    )
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sex: Mapped[str | None] = mapped_column(String, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    consent_status: Mapped[str] = mapped_column(String, default="not_provided", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped["Patient"] = relationship(back_populates="profile")


class MedicalHistory(Base):
    """Covers both patient_profile's known_conditions and relevant_history."""

    __tablename__ = "medical_history"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    condition: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)  # active | resolved
    notes: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped["Patient"] = relationship(back_populates="medical_history")


class Allergy(Base):
    __tablename__ = "allergies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    substance: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    reaction: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped["Patient"] = relationship(back_populates="allergies")


class CurrentMedication(Base):
    __tablename__ = "current_medications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    name: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    dosage: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    frequency: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped["Patient"] = relationship(back_populates="current_medications")


class Consent(Base):
    """Not an explicit table in database.tables, but required to satisfy
    security.consent_record_fields (user, consent_type, timestamp, version, status) as an
    append-only consent event log, distinct from patient_profiles.consent_status (current
    value). Flagged in IMPLEMENTATION_PLAN.md / the Phase 1 completion report."""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    consent_type: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # granted | withdrawn
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Symptom(Base):
    """Phase 3: symptom_extraction.fields, populated by app/patient_state/extraction.py from a
    conversation's user messages. All free-text fields encrypted at rest like Phase 1/2's
    sensitive columns. associated_symptoms is stored as free text (LLM-written, comma-separated)
    rather than a structured list — consistent with how the other descriptive fields (triggers,
    relieving_factors) are natural-language text, not enums."""

    __tablename__ = "symptoms"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )
    symptom: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    onset: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    duration: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    location: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    progression: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    triggers: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    relieving_factors: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    associated_symptoms: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    certainty: Mapped[str] = mapped_column(String, default="user_reported", nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)  # active | resolved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    """Phase 2: raw conversation container. No medical reasoning happens against these rows —
    see phases.2_conversation.constraint. Structured extraction into patient_state is Phase 3."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", order_by="Message.created_at")


class Message(Base):
    """content is encrypted at rest like Phase 1's sensitive fields — a conversation transcript
    can carry the same kind of sensitive information as medical_history/allergies. Untrusted
    per prompt_safety.untrusted_inputs; never reasoned over directly by an LLM here — only
    app/patient_state/extraction.py (Phase 3) and app/conversation/manager.py (Phase 4) ever
    hand message content to an LLM, and only in tightly scoped, structured ways."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class ClinicalSource(Base):
    """knowledge_base — one row per ingested document version. Never overwritten: re-ingesting
    the same url marks the prior row's superseded_status and inserts a new one, per
    knowledge_base.versioning ("Never overwrite knowledge without tracking versions"). Content
    here is public reference material, not patient data — no EncryptedString needed."""

    __tablename__ = "clinical_sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String, nullable=False)
    publisher: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # knowledge_base.approved_sources
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    update_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieval_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_status: Mapped[str] = mapped_column(String, default="current", nullable=False)  # current | superseded

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="source")


class KnowledgeChunk(Base):
    """rag_pipeline: one row per chunk of an ingested ClinicalSource, with its embedding."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clinical_sources.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["ClinicalSource"] = relationship(back_populates="chunks")


class SafetyEvent(Base):
    """Phase 7: one row per non-PASS safety_engine decision, per
    "safety_events... log every BLOCK/ESCALATE/MODIFY with rule_id and version." PASS is never
    logged here — only when a rule actually fires."""

    __tablename__ = "safety_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    rule_version: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)  # MODIFY | BLOCK | ESCALATE
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    """Phase 9: vital_system.device_pipeline's DEVICE. Only a "manual" device type is reachable
    via the API today (no real integration exists — Phase 10) — this table exists now so a
    real device registered later (Phase 10) doesn't need a schema change, per
    "clinical engine must not depend on a specific wearable"."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    device_type: Mapped[str] = mapped_column(String, nullable=False)  # "manual" today; a real vendor/type in Phase 10
    label: Mapped[str | None] = mapped_column(String, nullable=True)  # user-facing name, e.g. "My smartwatch"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Measurement(Base):
    """vital_system.canonical_measurement, verbatim field set — the append-only log of every
    reading ever ingested, valid or not (accepted ones also update the `vitals` snapshot
    below). `type` uses vital_system.initial_measurements' own vocabulary, with blood_pressure
    split into blood_pressure_systolic/blood_pressure_diastolic since canonical_measurement's
    `value` is a single scalar, not a compound reading."""

    __tablename__ = "measurements"

    id: Mapped[uuid.UUID] = _uuid_pk()  # measurement_id
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # manual | device | health_platform | simulated
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id"), nullable=True)
    quality: Mapped[str] = mapped_column(String, nullable=False)  # good | acceptable | poor
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # False = failed validation, still logged
    rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Vital(Base):
    """The current/latest value per (patient, type) — what patient_state.vitals is actually
    assembled from (app/patient_state/assembler.py), distinct from `measurements`' full
    history. Upserted only from *accepted* measurements."""

    __tablename__ = "vitals"

    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), primary_key=True)
    type: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    measurement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("measurements.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    """Only fields in security.logging.allowed are ever written here — see app/audit/middleware.py."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    module: Mapped[str] = mapped_column(String, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)


class DiagnosticRecord(Base):
    """Records a patient diagnostic consultation summary along with the exact snapshot of
    vital signs captured at the time of diagnosis. Accessible directly by the patient
    in the mobile application."""

    __tablename__ = "diagnostic_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    chief_complaint: Mapped[str] = mapped_column(Text, nullable=False)
    assessment: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)  # active | resolved
    symptoms: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    vitals_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

