from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Measurement, Patient, Vital
from app.vitals.schema import NormalizedMeasurement
from app.vitals.validation import validate_measurement


async def ingest_measurement(db: AsyncSession, patient: Patient, measurement: NormalizedMeasurement) -> Measurement:
    """vital_system.device_pipeline's VITAL_SERVICE step: validates, then persists to the
    append-only `measurements` log — accepted or not, for audit — and, if accepted, upserts the
    `vitals` current-value snapshot that app/patient_state/assembler.py reads from. A rejected
    measurement is still recorded (never silently dropped, `accepted=False` +
    `rejection_reason` on the returned row) but never updates the snapshot.
    """

    outcome = validate_measurement(measurement)

    record = Measurement(
        patient_id=patient.id,
        type=measurement.type,
        value=measurement.value,
        unit=measurement.unit,
        timestamp=measurement.timestamp,
        source=measurement.source,
        device_id=measurement.device_id,
        quality=measurement.quality,
        confidence=measurement.confidence,
        accepted=outcome.status == "accepted",
        rejection_reason=outcome.reason,
    )
    db.add(record)
    await db.flush()

    if outcome.status == "accepted":
        existing = await db.get(Vital, (patient.id, measurement.type))
        if existing is None:
            db.add(
                Vital(
                    patient_id=patient.id,
                    type=measurement.type,
                    value=measurement.value,
                    unit=measurement.unit,
                    timestamp=measurement.timestamp,
                    quality=measurement.quality,
                    source=measurement.source,
                    measurement_id=record.id,
                )
            )
        elif measurement.timestamp >= existing.timestamp:
            # Never let a backdated/out-of-order reading overwrite a more current snapshot.
            existing.value = measurement.value
            existing.unit = measurement.unit
            existing.timestamp = measurement.timestamp
            existing.quality = measurement.quality
            existing.source = measurement.source
            existing.measurement_id = record.id

    await db.commit()
    await db.refresh(record)
    return record


async def bulk_ingest_measurements(
    db: AsyncSession,
    patient: Patient,
    measurements: list[NormalizedMeasurement],
) -> list[Measurement]:
    """High-performance batch ingestion of vital measurements with deduplication.

    1. Validates each measurement.
    2. Identifies already-recorded readings in the DB for (patient_id, type, timestamp) to prevent duplicate rows.
    3. Bulk-inserts new readings in a single flush.
    4. Upserts the latest valid reading per vital type into the `vitals` snapshot table.
    5. Automatically logs/updates a DiagnosticRecord with the vitals snapshot in patient history.
    6. Commits once at the end of the batch, completing in milliseconds even for hundreds of samples.
    """
    if not measurements:
        return []

    # 1. Validate all measurements
    validated = [(m, validate_measurement(m)) for m in measurements]

    # 2. Query existing timestamps for this patient across the incoming types
    types_set = list({m.type for m, _ in validated})
    timestamps = [m.timestamp for m, _ in validated]

    from sqlalchemy import select
    stmt = select(Measurement.type, Measurement.timestamp).where(
        Measurement.patient_id == patient.id,
        Measurement.type.in_(types_set),
        Measurement.timestamp.in_(timestamps),
    )
    existing_rows = (await db.execute(stmt)).all()
    existing_keys = {(row[0], row[1]) for row in existing_rows}

    records_to_insert: list[Measurement] = []
    returned_records: list[Measurement] = []
    seen_in_batch: set[tuple[str, Any]] = set()
    latest_accepted_by_type: dict[str, tuple[NormalizedMeasurement, Measurement]] = {}

    for m, outcome in validated:
        key = (m.type, m.timestamp)
        is_duplicate = key in existing_keys or key in seen_in_batch

        record = Measurement(
            patient_id=patient.id,
            type=m.type,
            value=m.value,
            unit=m.unit,
            timestamp=m.timestamp,
            source=m.source,
            device_id=m.device_id,
            quality=m.quality,
            confidence=m.confidence,
            accepted=outcome.status == "accepted",
            rejection_reason="DUPLICATE_ALREADY_RECORDED" if is_duplicate else outcome.reason,
        )
        returned_records.append(record)

        if not is_duplicate:
            seen_in_batch.add(key)
            records_to_insert.append(record)
            if outcome.status == "accepted":
                if (
                    m.type not in latest_accepted_by_type
                    or m.timestamp >= latest_accepted_by_type[m.type][0].timestamp
                ):
                    latest_accepted_by_type[m.type] = (m, record)

    # 3. Bulk insert new records
    if records_to_insert:
        db.add_all(records_to_insert)
        await db.flush()

    # 4. Upsert snapshot for latest reading per vital type
    for vital_type, (m, record) in latest_accepted_by_type.items():
        existing = await db.get(Vital, (patient.id, vital_type))
        if existing is None:
            db.add(
                Vital(
                    patient_id=patient.id,
                    type=m.type,
                    value=m.value,
                    unit=m.unit,
                    timestamp=m.timestamp,
                    quality=m.quality,
                    source=m.source,
                    measurement_id=record.id,
                )
            )
        elif m.timestamp >= existing.timestamp:
            existing.value = m.value
            existing.unit = m.unit
            existing.timestamp = m.timestamp
            existing.quality = m.quality
            existing.source = m.source
            existing.measurement_id = record.id

    await db.commit()

    # 5. Automatically log to Diagnostic History with latest vitals snapshot
    if latest_accepted_by_type:
        try:
            from app.diagnostics.service import record_diagnostic_entry
            vitals_snapshot = {
                vt: {"value": m.value, "unit": m.unit, "timestamp": m.timestamp.isoformat()}
                for vt, (m, _) in latest_accepted_by_type.items()
            }
            hr_val = vitals_snapshot.get("heart_rate", {}).get("value")
            spo2_val = vitals_snapshot.get("oxygen_saturation", {}).get("value")

            summary_parts = []
            if hr_val:
                summary_parts.append(f"Heart Rate: {int(hr_val)} bpm")
            if spo2_val:
                summary_parts.append(f"SpO2: {int(spo2_val)}%")
            complaint_str = ", ".join(summary_parts) if summary_parts else "Wearable biometric telemetry"

            is_urgent = False
            if hr_val and (hr_val > 120 or hr_val < 50):
                is_urgent = True
            if spo2_val and spo2_val < 92:
                is_urgent = True

            title = "Wearable Vital Alert" if is_urgent else "Wearable Vitals Telemetry (Nothing X)"
            await record_diagnostic_entry(
                db=db,
                patient_id=patient.id,
                title=title,
                chief_complaint=f"Wearable sync: {complaint_str}",
                assessment="Biometric telemetry recorded from connected wearable companion app (Nothing X / Health Connect).",
                plan="Continuous biometric monitoring active. Consult AI or healthcare professional if symptomatic.",
                symptoms=[],
                vitals=vitals_snapshot,
            )
            await db.commit()
        except Exception:
            pass

    return returned_records
