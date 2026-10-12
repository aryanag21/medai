from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiagnosticRecord, Patient, Symptom, User
from app.diagnostics.service import (
    auto_record_consultation_diagnosis,
    build_vitals_snapshot,
    get_patient_diagnostic_history,
    record_diagnostic_entry,
    update_diagnostic_status,
    update_symptom_status_by_name,
)


@pytest.mark.asyncio
async def test_record_diagnostic_entry_and_vitals_snapshot(db_session: AsyncSession) -> None:
    user = User(id=uuid4(), email=f"user-{uuid4()}@example.com", hashed_password="pw")
    patient = Patient(id=uuid4(), user_id=user.id)
    db_session.add(user)
    db_session.add(patient)
    await db_session.flush()

    vitals = {
        "heart_rate": {"value": 78.0, "unit": "bpm", "timestamp": "2026-10-12T10:00:00Z"},
        "oxygen_saturation": {"value": 98.0, "unit": "%", "timestamp": "2026-10-12T10:00:00Z"},
        "body_temperature": {"value": 98.6, "unit": "F", "timestamp": "2026-10-12T10:00:00Z"},
    }

    record = await record_diagnostic_entry(
        db=db_session,
        patient_id=patient.id,
        conversation_id=None,
        title="Acute Respiratory / Cough & Cold Evaluation",
        chief_complaint="Persistent dry cough and chest tickle",
        assessment="Probable post-viral bronchitis",
        plan="Dextromethorphan 15mg at night, hydration, honey",
        symptoms=["cough", "chest tickle"],
        vitals=vitals,
    )
    await db_session.commit()

    assert record.id is not None
    assert record.status == "active"
    assert record.title == "Acute Respiratory / Cough & Cold Evaluation"
    assert record.symptoms == ["cough", "chest tickle"]
    assert record.vitals_snapshot is not None
    assert record.vitals_snapshot["heart_rate"]["value"] == 78.0
    assert record.vitals_snapshot["oxygen_saturation"]["value"] == 98.0

    # Retrieve history
    history = await get_patient_diagnostic_history(db_session, patient.id)
    assert len(history) == 1
    assert history[0].title == "Acute Respiratory / Cough & Cold Evaluation"
    assert history[0].vitals_snapshot["heart_rate"]["value"] == 78.0


@pytest.mark.asyncio
async def test_diagnostic_status_toggle_and_symptom_resolution(db_session: AsyncSession) -> None:
    user = User(id=uuid4(), email=f"user-{uuid4()}@example.com", hashed_password="pw")
    patient = Patient(id=uuid4(), user_id=user.id)
    db_session.add(user)
    db_session.add(patient)
    await db_session.flush()

    symptom = Symptom(
        patient_id=patient.id,
        symptom="cough",
        certainty="user_reported",
        status="active",
    )
    db_session.add(symptom)
    await db_session.flush()

    record = await record_diagnostic_entry(
        db=db_session,
        patient_id=patient.id,
        conversation_id=None,
        title="Cough Evaluation",
        chief_complaint="I have a bad cough",
        assessment="Acute viral cough",
        plan="Hydration",
        symptoms=["cough"],
        vitals={"heart_rate": {"value": 72.0, "unit": "bpm"}},
    )
    await db_session.commit()

    # Mark as resolved
    updated = await update_diagnostic_status(db_session, patient.id, record.id, "resolved")
    assert updated is not None
    assert updated.status == "resolved"

    # Symptom should also be resolved
    await db_session.refresh(symptom)
    assert symptom.status == "resolved"


@pytest.mark.asyncio
async def test_auto_record_consultation_diagnosis(db_session: AsyncSession) -> None:
    user = User(id=uuid4(), email=f"user-{uuid4()}@example.com", hashed_password="pw")
    patient = Patient(id=uuid4(), user_id=user.id)
    db_session.add(user)
    db_session.add(patient)
    await db_session.flush()

    class MockState:
        symptoms = [Symptom(patient_id=patient.id, symptom="stubbed foot", certainty="user_reported")]
        vitals = {"heart_rate": {"value": 84.0, "unit": "bpm"}}

    record = await auto_record_consultation_diagnosis(
        db=db_session,
        patient_id=patient.id,
        conversation_id=uuid4(),
        user_text="I slammed my foot into the door and it hurts",
        reply_text="That kind of impact causes acute contusion. Elevate with R.I.C.E. and take Ibuprofen 400mg.",
        state=MockState(),
    )
    await db_session.commit()

    assert record is not None
    assert "Foot" in record.title or "Contusion" in record.title
    assert record.vitals_snapshot["heart_rate"]["value"] == 84.0
