from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ColumnProfileRead(BaseModel):
    """Per-column profile (PRD section 9)."""

    model_config = ConfigDict(from_attributes=True)

    column_name: str
    ordinal_position: int
    raw_type: str
    inferred_type: str
    semantic_type: str

    # Type inference / convertibility (PRD section 10)
    conversion_confidence: float | None = None
    requires_conversion: bool = False
    invalid_value_count: int = 0
    sample_invalid_values: list[str] | None = None

    # Universal
    null_count: int
    null_pct: float
    unique_count: int | None = None
    unique_pct: float | None = None
    min_value: str | None = None
    max_value: str | None = None

    # Numeric
    mean: float | None = None
    median: float | None = None
    stddev: float | None = None
    percentiles: dict[str, float] | None = None
    outlier_count: int | None = None
    outlier_lower: float | None = None
    outlier_upper: float | None = None

    # Categorical
    top_values: list[dict[str, Any]] | None = None

    # Datetime
    datetime_stats: dict[str, Any] | None = None

    # KPI detection (PRD section 11)
    kpi_measure_score: float | None = None
    kpi_dimension_score: float | None = None
    kpi_time_score: float | None = None
    suggested_aggregation: str | None = None
    candidate_reasons: dict[str, list[str]] | None = None


class DatasetProfileRead(BaseModel):
    """Dataset-level profile (PRD section 9)."""

    model_config = ConfigDict(from_attributes=True)

    dataset_id: UUID
    profile_version: int
    row_count: int
    column_count: int
    file_size_bytes: int
    duplicate_row_count: int | None = None
    duplicate_row_pct: float | None = None
    duplicate_check_skipped: bool = False
    missing_cell_count: int
    missing_cell_pct: float
    quality_status: str | None = None
    engine: str
    exact_quantiles: bool = True
    duration_ms: int | None = None
    generated_at: datetime


class ProfileEnvelope(BaseModel):
    """Always 200 with a ``state`` field.

    The upload screen polls this; returning 404 while profiling is still
    running would force the client to treat a normal state as an error.
    """

    state: str  # pending | running | ready | failed
    profile: DatasetProfileRead | None = None
    columns: list[ColumnProfileRead] = []
    message: str | None = None
