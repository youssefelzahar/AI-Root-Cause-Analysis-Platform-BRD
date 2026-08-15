from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ValidationIssueRead(BaseModel):
    code: str
    severity: str
    message: str
    column: str | None = None
    details: dict[str, Any] = {}
    suggested_fix: dict[str, Any] | None = None


class SchemaValidationRead(BaseModel):
    """PRD section 10: PASS / WARNING / BLOCKED."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    kpi_definition_id: UUID | None = None
    mode: str
    state: str
    error_count: int
    warning_count: int
    info_count: int
    issues: list[ValidationIssueRead]
    rules_version: str
    created_at: datetime


class ValidationRunRequest(BaseModel):
    mode: str = "structural"
    kpi_definition_id: UUID | None = None
