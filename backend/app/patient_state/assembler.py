from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Allergy, CurrentMedication, MedicalHistory, Patient, PatientProfile, Symptom, Vital
from app.patient_state.schema import ExtractedSymptom, PatientState, PatientStateIdentity
from app.schemas.patient import AllergyResponse, MedicalHistoryResponse, MedicationResponse

# patient_profile.required_fields that patient_profiles/consent actually cover (the rest —
# known_conditions/allergies/current_medications/relevant_history — are covered by having at
# least one row in their own tables, checked separately below).
_PROFILE_SCALAR_FIELDS = ["age", "sex", "height_cm", "weight_kg", "emergency_contact_name", "emergency_contact_phone"]


async def build_patient_state(db: AsyncSession, patient: Patient) -> PatientState:
    """Assembles the canonical patient_state.schema from Phase 1's tables (profile, history,
    allergies, medications), Phase 3's symptoms table, and Phase 9's vitals snapshot. Never
    invents a value for a field it can't find — missing data is surfaced in `unknowns`, not
    guessed (patient_state.rules)."""

    profile = await db.get(PatientProfile, patient.id)

    history_rows = (
        await db.execute(select(MedicalHistory).where(MedicalHistory.patient_id == patient.id))
    ).scalars().all()
    allergy_rows = (await db.execute(select(Allergy).where(Allergy.patient_id == patient.id))).scalars().all()
    medication_rows = (
        await db.execute(select(CurrentMedication).where(CurrentMedication.patient_id == patient.id))
    ).scalars().all()
    symptom_rows = (
        await db.execute(select(Symptom).where(Symptom.patient_id == patient.id).order_by(Symptom.created_at))
    ).scalars().all()
    vital_rows = (await db.execute(select(Vital).where(Vital.patient_id == patient.id))).scalars().all()

    unknowns: list[str] = []
    for field in _PROFILE_SCALAR_FIELDS:
        if profile is None or getattr(profile, field) is None:
            unknowns.append(field)
    if profile is None or profile.consent_status != "granted":
        unknowns.append("consent_status")
    if not history_rows:
        unknowns.append("known_conditions")
    if not allergy_rows:
        unknowns.append("allergies")
    if not medication_rows:
        unknowns.append("current_medications")

    total_fields = len(_PROFILE_SCALAR_FIELDS) + 1  # + consent_status
    known_fields = total_fields - sum(1 for u in unknowns if u in (*_PROFILE_SCALAR_FIELDS, "consent_status"))
    data_quality = {"profile_completeness": round(known_fields / total_fields, 2)}

    return PatientState(
        patient=PatientStateIdentity(
            id=patient.id,
            age=profile.age if profile else None,
            sex=profile.sex if profile else None,
            height_cm=profile.height_cm if profile else None,
            weight_kg=profile.weight_kg if profile else None,
        ),
        symptoms=[
            ExtractedSymptom(
                symptom=s.symptom,
                onset=s.onset,
                duration=s.duration,
                severity=s.severity,
                frequency=s.frequency,
                location=s.location,
                progression=s.progression,
                triggers=s.triggers,
                relieving_factors=s.relieving_factors,
                associated_symptoms=s.associated_symptoms,
                certainty=s.certainty,  # type: ignore[arg-type]
                status=getattr(s, "status", "active"),
            )
            for s in symptom_rows
        ],
        medical_history=[MedicalHistoryResponse.model_validate(h) for h in history_rows],
        allergies=[AllergyResponse.model_validate(a) for a in allergy_rows],
        medications=[MedicationResponse.model_validate(m) for m in medication_rows],
        vitals={
            v.type: {
                "value": v.value,
                "unit": v.unit,
                "timestamp": v.timestamp.isoformat(),
                "quality": v.quality,
                "source": v.source,
            }
            for v in vital_rows
        },
        recent_events=[],
        risk_factors=[],
        unknowns=unknowns,
        conversation_context={},
        data_quality=data_quality,
    )
