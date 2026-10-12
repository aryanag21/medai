import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiagnosticRecord, Symptom

logger = logging.getLogger(__name__)


def build_vitals_snapshot(vitals: dict[str, Any] | None) -> dict[str, Any]:
    """Extracts a clean, patient-readable snapshot of vital signs recorded at diagnosis time."""
    if not vitals:
        return {}

    snapshot: dict[str, Any] = {}
    for key, data in vitals.items():
        if isinstance(data, dict) and "value" in data:
            snapshot[key] = {
                "value": data["value"],
                "unit": data.get("unit", ""),
                "timestamp": data.get("timestamp"),
            }
        elif isinstance(data, (int, float, str)):
            snapshot[key] = {"value": data}

    return snapshot


async def record_diagnostic_entry(
    db: AsyncSession,
    patient_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title: str,
    chief_complaint: str,
    assessment: str,
    plan: str | None = None,
    symptoms: list[str] | None = None,
    vitals: dict[str, Any] | None = None,
) -> DiagnosticRecord:
    """Records a new patient diagnostic consultation record with timestamp and vitals snapshot."""
    vitals_snapshot = build_vitals_snapshot(vitals)

    # Verify conversation exists before using as foreign key
    valid_conv_id: uuid.UUID | None = None
    if conversation_id:
        from app.db.models import Conversation
        conv_check = await db.get(Conversation, conversation_id)
        if conv_check:
            valid_conv_id = conversation_id

    # Check if a recent diagnostic record already exists in this conversation within last 1 hour
    # with the same or similar chief complaint, update it instead of creating duplicates
    if valid_conv_id:
        existing = await db.execute(
            select(DiagnosticRecord)
            .where(
                DiagnosticRecord.patient_id == patient_id,
                DiagnosticRecord.conversation_id == valid_conv_id,
            )
            .order_by(DiagnosticRecord.created_at.desc())
        )
        recent = existing.scalars().first()
        if recent and recent.status == "active":
            recent.title = title
            recent.chief_complaint = chief_complaint
            recent.assessment = assessment
            if plan:
                recent.plan = plan
            if symptoms:
                merged_symptoms = list(dict.fromkeys((recent.symptoms or []) + symptoms))
                recent.symptoms = merged_symptoms
            if vitals_snapshot:
                recent.vitals_snapshot = vitals_snapshot
            await db.flush()
            return recent

    record = DiagnosticRecord(
        patient_id=patient_id,
        conversation_id=valid_conv_id,
        title=title,
        chief_complaint=chief_complaint,
        assessment=assessment,
        plan=plan,
        status="active",
        symptoms=symptoms or [],
        vitals_snapshot=vitals_snapshot,
    )
    db.add(record)
    await db.flush()
    return record


async def get_patient_diagnostic_history(
    db: AsyncSession,
    patient_id: uuid.UUID,
    status_filter: str | None = None,
) -> list[DiagnosticRecord]:
    """Retrieves all diagnostic history records for a patient ordered newest first."""
    query = select(DiagnosticRecord).where(DiagnosticRecord.patient_id == patient_id)
    if status_filter:
        query = query.where(DiagnosticRecord.status == status_filter)
    query = query.order_by(DiagnosticRecord.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_diagnostic_record(
    db: AsyncSession,
    patient_id: uuid.UUID,
    record_id: uuid.UUID,
) -> DiagnosticRecord | None:
    """Retrieves a single diagnostic record verifying patient ownership."""
    result = await db.execute(
        select(DiagnosticRecord).where(
            DiagnosticRecord.id == record_id,
            DiagnosticRecord.patient_id == patient_id,
        )
    )
    return result.scalar_one_or_none()


async def update_diagnostic_status(
    db: AsyncSession,
    patient_id: uuid.UUID,
    record_id: uuid.UUID,
    new_status: str,
) -> DiagnosticRecord | None:
    """Updates status ('active' | 'resolved') of a diagnostic record."""
    record = await get_diagnostic_record(db, patient_id, record_id)
    if not record:
        return None

    record.status = new_status

    # If marking as resolved, also mark associated symptoms as resolved
    # Note: Symptom.symptom is an EncryptedString, so matching is done in Python
    if new_status == "resolved" and record.symptoms:
        sym_results = await db.execute(select(Symptom).where(Symptom.patient_id == patient_id))
        target_names = [s.lower() for s in record.symptoms]
        for s in sym_results.scalars().all():
            if any(t in s.symptom.lower() for t in target_names):
                s.status = "resolved"

    await db.commit()
    await db.refresh(record)
    return record


async def update_symptom_status_by_name(
    db: AsyncSession,
    patient_id: uuid.UUID,
    symptom_keyword: str,
    status: str,
) -> int:
    """Finds symptoms matching keyword and updates their status.
    Note: Symptom.symptom is an EncryptedString, so matching is evaluated after decryption in Python."""
    result = await db.execute(select(Symptom).where(Symptom.patient_id == patient_id))
    symptoms = result.scalars().all()
    count = 0
    kw = symptom_keyword.lower()
    for s in symptoms:
        if kw in s.symptom.lower():
            s.status = status
            count += 1
    if count > 0:
        await db.commit()
    return count


def determine_diagnosis_title(text: str) -> str:
    text_l = text.lower()
    if any(k in text_l for k in ["cough", "cold", "congestion", "phlegm", "mucus", "runny nose", "sneeze"]):
        return "Acute Respiratory / Cough & Cold Evaluation"
    if any(k in text_l for k in ["throat", "swallow", "tonsil", "strep"]):
        return "Sore Throat / Pharyngitis Evaluation"
    if any(k in text_l for k in ["slam", "door", "foot", "toe", "ankle", "hit my", "stub", "sprain", "bruis", "injur"]):
        return "Acute Foot Trauma / Soft Tissue Contusion"
    if any(k in text_l for k in ["back", "spine", "lumbar", "sciatica"]):
        return "Lumbar Spine / Back Pain Evaluation"
    if any(k in text_l for k in ["headache", "migraine", "head hurts"]):
        return "Headache / Cephalea Assessment"
    if any(k in text_l for k in ["fever", "chills", "high temp"]):
        return "Acute Febrile Illness Assessment"
    if any(k in text_l for k in ["stomach", "nausea", "vomit", "diarrhea", "belly"]):
        return "Gastrointestinal Assessment"
    if any(k in text_l for k in ["knee", "shoulder", "neck", "joint", "elbow", "wrist"]):
        return "Musculoskeletal Assessment"
    return "Clinical Health Consultation"


async def auto_record_consultation_diagnosis(
    db: AsyncSession,
    patient_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    user_text: str,
    reply_text: str,
    state: Any,
) -> DiagnosticRecord | None:
    """Automatically records or updates a diagnostic consultation record when health symptoms are discussed."""
    text_l = user_text.lower()
    # Skip meta/server questions
    if any(k in text_l for k in ["server", "app is", "app server", "backend"]):
        return None

    # Check if user mentioned health symptoms or pain
    health_keywords = [
        "pain", "hurts", "ache", "cough", "cold", "fever", "sore", "injury", "swelling",
        "foot", "toe", "door", "slam", "back", "throat", "head", "stomach", "medicine",
        "paracetamol", "ibuprofen", "vomit", "nausea", "dizzy", "fatigue", "tired", "sleep",
        "breath", "congestion", "infection", "rash", "bruise",
    ]
    if not any(k in text_l for k in health_keywords):
        return None

    title = determine_diagnosis_title(user_text)
    symptom_names = [s.symptom for s in getattr(state, "symptoms", [])] if getattr(state, "symptoms", None) else []
    vitals = getattr(state, "vitals", {})

    return await record_diagnostic_entry(
        db=db,
        patient_id=patient_id,
        conversation_id=conversation_id,
        title=title,
        chief_complaint=user_text,
        assessment=reply_text,
        plan=reply_text,
        symptoms=symptom_names,
        vitals=vitals,
    )

