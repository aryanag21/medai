import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DiagnosticRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    title: str
    chief_complaint: str
    assessment: str
    plan: str | None = None
    status: str
    symptoms: list[str] | None = None
    vitals_snapshot: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DiagnosticStatusUpdate(BaseModel):
    status: str  # active | resolved
