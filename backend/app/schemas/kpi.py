from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import Aggregation, ComparisonPeriod


class KpiCandidateRead(BaseModel):
    column: str
    score: float
    reasons: list[str] = []
    suggested_aggregation: str | None = None
    # Extra context the UI shows next to the recommendation.
    dtype: str | None = None
    cardinality: int | None = None
    detected_frequency: str | None = None
    min_date: str | None = None
    max_date: str | None = None
    distinct_periods: int | None = None


class KpiCandidatesRead(BaseModel):
    measures: list[KpiCandidateRead] = []
    time_columns: list[KpiCandidateRead] = []
    dimensions: list[KpiCandidateRead] = []
    recommended_default: dict[str, Any] = {}


class KpiDefinitionCreate(BaseModel):
    """The user's KPI selection (PRD section 11)."""

    name: str = Field(min_length=1, max_length=150)
    column: str = Field(min_length=1, max_length=255)
    aggregation: Aggregation = Aggregation.SUM
    time_column: str | None = Field(default=None, max_length=255)
    dimensions: list[str] = Field(default_factory=list)
    comparison: ComparisonPeriod = ComparisonPeriod.PREVIOUS_PERIOD
    comparison_config: dict[str, Any] | None = None
    filters: list[dict[str, Any]] | None = None

    @field_validator("dimensions")
    @classmethod
    def _unique_dimensions(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            if item and item not in seen:
                seen.append(item)
        return seen


class KpiDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    name: str
    column_name: str
    aggregation: str
    time_column: str | None = None
    dimensions: list[str]
    comparison: str
    comparison_config: dict[str, Any] | None = None
    filters: list[dict[str, Any]] | None = None
    # The exact PRD contract handed to the RCA engine.
    definition: dict[str, Any]
    is_active: bool
    validation_state: str | None = None
    created_at: datetime


class KpiDefinitionEnvelope(BaseModel):
    kpi_definition: KpiDefinitionRead
    validation: dict[str, Any]
    analysis_ready: bool
