import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient
from app.db.models import Patient
from app.db.session import get_db
from app.diagnostics.service import (
    get_diagnostic_record,
    get_patient_diagnostic_history,
    update_diagnostic_status,
)
from app.schemas.diagnostic import DiagnosticRecordResponse, DiagnosticStatusUpdate

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("", response_model=list[DiagnosticRecordResponse])
async def list_diagnostic_history(
    status_filter: str | None = Query(default=None, alias="status"),
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> list[Any]:
    """Returns the patient's complete diagnostic and consultation history, including the
    vitals recorded at the exact time of each diagnosis."""
    return await get_patient_diagnostic_history(db, patient.id, status_filter=status_filter)


@router.get("/{record_id}", response_model=DiagnosticRecordResponse)
async def get_diagnostic_detail(
    record_id: uuid.UUID,
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Returns detailed assessment, treatment plan, and vitals snapshot for a specific diagnosis."""
    record = await get_diagnostic_record(db, patient.id, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostic record not found",
        )
    return record


@router.patch("/{record_id}/status", response_model=DiagnosticRecordResponse)
async def update_status(
    record_id: uuid.UUID,
    payload: DiagnosticStatusUpdate,
    patient: Patient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Updates the status of a diagnosis (e.g. marking as 'resolved' or 'active')."""
    if payload.status not in ("active", "resolved"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be 'active' or 'resolved'",
        )

    updated = await update_diagnostic_status(db, patient.id, record_id, payload.status)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostic record not found",
        )
    return updated
