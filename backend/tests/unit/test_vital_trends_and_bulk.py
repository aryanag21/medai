from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, Measurement, Patient
from app.vitals.schema import NormalizedMeasurement
from app.vitals.service import bulk_ingest_measurements
from app.vitals.trends import calculate_vital_trends, format_trends_summary


@pytest.mark.asyncio
async def test_bulk_ingest_and_deduplication(db_session: AsyncSession) -> None:
    from app.db.models import User
    user = User(id=uuid4(), email=f"user-{uuid4()}@example.com", hashed_password="pw")
    patient = Patient(id=uuid4(), user_id=user.id)
    db_session.add(user)
    db_session.add(patient)
    await db_session.flush()

    device = Device(id=uuid4(), patient_id=patient.id, device_type="health_connect", label="Nothing Watch")
    db_session.add(device)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    readings = [
        NormalizedMeasurement(
            type="heart_rate",
            value=75.0 + i,
            unit="bpm",
            timestamp=now - timedelta(minutes=i * 5),
            source="health_platform",
            device_id=device.id,
            quality="good",
        )
        for i in range(10)
    ]

    # First ingest
    records = await bulk_ingest_measurements(db_session, patient, readings)
    assert len(records) == 10
    assert all(r.accepted for r in records)

    # Second ingest with same readings (should deduplicate!)
    dupe_records = await bulk_ingest_measurements(db_session, patient, readings)
    assert len(dupe_records) == 10
    # Duplicate records should have rejection_reason marked
    assert all(r.rejection_reason == "DUPLICATE_ALREADY_RECORDED" for r in dupe_records)


@pytest.mark.asyncio
async def test_vital_trends_and_extreme_alerts(db_session: AsyncSession) -> None:
    from app.db.models import User
    user = User(id=uuid4(), email=f"user-{uuid4()}@example.com", hashed_password="pw")
    patient = Patient(id=uuid4(), user_id=user.id)
    db_session.add(user)
    db_session.add(patient)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    # Add normal readings and an extreme SpO2 dip + high heart rate spike
    samples = [
        Measurement(
            patient_id=patient.id,
            type="heart_rate",
            value=135.0,  # Extreme tachycardia
            unit="bpm",
            timestamp=now - timedelta(hours=1),
            source="health_platform",
            quality="good",
            accepted=True,
        ),
        Measurement(
            patient_id=patient.id,
            type="oxygen_saturation",
            value=89.0,  # Critical hypoxemia
            unit="%",
            timestamp=now - timedelta(hours=2),
            source="health_platform",
            quality="good",
            accepted=True,
        ),
        Measurement(
            patient_id=patient.id,
            type="body_temperature",
            value=103.0,  # High fever
            unit="F",
            timestamp=now - timedelta(hours=3),
            source="health_platform",
            quality="good",
            accepted=True,
        ),
    ]
    db_session.add_all(samples)
    await db_session.commit()

    trends = await calculate_vital_trends(db_session, patient.id, window_hours=24)
    assert "heart_rate" in trends["trends"]
    assert "oxygen_saturation" in trends["trends"]
    assert "body_temperature" in trends["trends"]
    assert len(trends["extreme_alerts"]) >= 3
    assert any("135" in a for a in trends["extreme_alerts"])
    assert any("89" in a for a in trends["extreme_alerts"])

    summary = format_trends_summary(trends)
    assert "EXTREME VITAL ALERTS" in summary
