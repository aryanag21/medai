from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient
from app.db.models import Device, Measurement, Patient, Vital
from app.db.session import get_db
from app.schemas.vital import (
    MeasurementResponse,
    VitalCreateRequest,
    VitalResponse,
    VitalSyncRequest,
    VitalSyncResponse,
    VitalSyncResult,
)
from app.vitals.schema import NormalizedMeasurement
from app.vitals.service import bulk_ingest_measurements, ingest_measurement
from app.vitals.trends import calculate_vital_trends

router = APIRouter(prefix="/vitals", tags=["vitals"])


@router.post("", response_model=MeasurementResponse, status_code=status.HTTP_201_CREATED)
async def create_vital(
    payload: VitalCreateRequest,
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> Measurement:
    """Manual entry — the only DEVICE step reachable today
    (automatic_retrieval_preference: DEVICE -> HEALTH_PLATFORM -> MEDAI isn't available until
    Phase 10 exists). `source` is always "manual" server-side regardless of client input —
    nothing else is real yet, so nothing else may claim to be (never fabricate a measurement's
    provenance). Returns 422 (not silently accepted) if the measurement fails validation —
    vital_system.on_suspicious_measurement: MEASUREMENT_UNRELIABLE, request another.
    """

    measurement = NormalizedMeasurement(
        type=payload.type,
        value=payload.value,
        unit=payload.unit,
        timestamp=payload.timestamp or datetime.now(timezone.utc),
        source="manual",
        quality="good",
    )
    record = await ingest_measurement(db, patient, measurement)

    if not record.accepted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"MEASUREMENT_UNRELIABLE: {record.rejection_reason} — please take another measurement.",
        )
    return record


@router.get("", response_model=list[VitalResponse])
async def list_current_vitals(
    patient: Patient = Depends(get_current_patient), db: AsyncSession = Depends(get_db)
) -> list[Vital]:
    result = await db.execute(select(Vital).where(Vital.patient_id == patient.id))
    return list(result.scalars().all())


@router.post("/sync", response_model=VitalSyncResponse)
async def sync_vitals_from_health_platform(
    payload: VitalSyncRequest,
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> VitalSyncResponse:
    """Phase 10's VITAL_SERVICE entry point for a real on-device health platform (Health
    Connect). Health Connect's data lives in the OS-level store on the user's own device,
    gated by that device's own permission grants — the backend cannot poll it the way
    app/providers/devices/base.py's DeviceAdapter.read() assumes (see that file's docstring),
    so the concrete adapter runs client-side in the app; this endpoint is where its readings
    enter the canonical pipeline. `source` is always forced to "health_platform" server-side
    regardless of client input (never fabricate provenance, same rule as POST /vitals) —
    Health Connect aggregates across apps/devices rather than being one physical DEVICE, so
    `automatic_retrieval_preference`'s HEALTH_PLATFORM tier, not DEVICE, is the correct label.
    Every reading runs through the same validation/persistence path as manual entry — accepted
    or rejected, never silently dropped — and the response reports one outcome per reading.
    """

    device = await db.get(Device, payload.device_id)
    if device is None or device.patient_id != patient.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")

    measurements = [
        NormalizedMeasurement(
            type=reading.type,
            value=reading.value,
            unit=reading.unit,
            timestamp=reading.timestamp,
            source="health_platform",
            device_id=device.id,
            quality="good",
        )
        for reading in payload.readings
    ]

    records = await bulk_ingest_measurements(db, patient, measurements)
    results = [
        VitalSyncResult(
            type=record.type,
            value=record.value,
            unit=record.unit,
            timestamp=record.timestamp,
            accepted=record.accepted,
            rejection_reason=record.rejection_reason,
        )
        for record in records
    ]

    accepted_count = sum(1 for r in results if r.accepted)
    return VitalSyncResponse(
        synced=len(results),
        accepted=accepted_count,
        rejected=len(results) - accepted_count,
        results=results,
    )


@router.get("/sync/latest")
async def get_latest_sync_timestamp(
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    """Returns the latest measurement timestamp recorded for this patient so the mobile
    client only syncs new readings rather than re-requesting old historical ranges.
    """
    from sqlalchemy import func

    result = await db.execute(
        select(func.max(Measurement.timestamp)).where(Measurement.patient_id == patient.id)
    )
    latest = result.scalar_one_or_none()
    return {"latest_timestamp": latest.isoformat() if latest else None}


@router.get("/trends")
async def get_patient_vital_trends(
    window_hours: int = Query(default=24, ge=1, le=168),
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Calculates statistical health trends, min/max/average ranges, and detects extreme
    vital anomalies across the patient's vitals for the specified time window.
    """
    from typing import Any
    return await calculate_vital_trends(db, patient.id, window_hours=window_hours)


@router.get("/history", response_model=list[MeasurementResponse])
async def list_measurement_history(
    type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> list[Measurement]:
    query = select(Measurement).where(Measurement.patient_id == patient.id)
    if type is not None:
        query = query.where(Measurement.type == type)
    query = query.order_by(Measurement.timestamp.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())
