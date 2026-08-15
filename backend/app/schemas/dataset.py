from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DatasetSummary(BaseModel):
    """Row shape for the dataset list (PRD section 13)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    original_filename: str | None = None
    source_type: str
    file_format: str
    size_bytes: int
    row_count: int | None = None
    column_count: int | None = None
    status: str
    upload_status: str
    quality_state: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_analysis_ready(self) -> bool:
        return self.status == "analysis_ready"


class DatasetDetail(DatasetSummary):
    description: str | None = None
    checksum_sha256: str | None = None
    schema_version: int = 1
    storage_key: str | None = None
    source_query: str | None = None
    source_connection_id: UUID | None = None
    profiling_started_at: datetime | None = None
    profiling_completed_at: datetime | None = None
    has_profile: bool = False
    has_kpi_definition: bool = False
    analysis_ready: bool = False


class DatasetStatusRead(BaseModel):
    """Small payload polled by the upload screen."""

    dataset_id: UUID
    status: str
    upload_status: str
    quality_state: str | None = None
    profile_ready: bool = False
    validation_ready: bool = False
    analysis_ready: bool = False
    row_count: int | None = None
    column_count: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    updated_at: datetime


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class UploadResponse(BaseModel):
    dataset: DatasetDetail
    duplicate_of: UUID | None = None


class PreviewColumn(BaseModel):
    name: str
    type: str


class PreviewRead(BaseModel):
    columns: list[PreviewColumn]
    rows: list[list[Any]]
    total_rows: int | None = None
    limit: int
    offset: int
