"""Request and response contracts for persisted, evidence-backed investigations.

Unlike ``app.schemas.rca``, these *are* ORM-backed - the investigation is a row -
so the read models map explicitly from the ORM in ``from_row`` classmethods
rather than relying on ``from_attributes``. Explicit mapping is what keeps a new
column from silently appearing on the wire.

Wording follows the same rule as the RCA schemas: *driver*, *contributor*,
*contribution*, *offsetting factor*. Evidence records what a segment contributed
to a measured change; they never assert that it caused it.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import (
    EvidenceRecord,
    Investigation,
    InvestigationAuditEvent,
    InvestigationQuery,
)


class InvestigationCreate(BaseModel):
    dataset_id: UUID
    # Defaults to the dataset's active definition.
    kpi_definition_id: UUID | None = None
    max_drivers: int = Field(default=5, ge=1, le=20)
    max_tree_depth: int = Field(default=3, ge=1, le=3)
    # Free text, recorded for the reader's benefit. It steers nothing: the plan
    # is derived entirely from the KPI definition, so an investigation cannot be
    # made to answer a different question by phrasing.
    question: str | None = Field(default=None, max_length=500)


class QualityCheckRead(BaseModel):
    check: str
    status: str
    detail: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class EvidenceQualityRead(BaseModel):
    """The verdict on whether the analysis is well-formed and traceable.

    Separate from KPI severity and from per-record confidence: this says whether
    the analysis can be trusted, not whether the finding is large or whether one
    number rests on enough rows.
    """

    verdict: str | None = None
    checks: list[QualityCheckRead] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class ReconciliationRead(BaseModel):
    status: str | None = None
    contribution_sum: float | None = None
    # The tolerance actually applied, so a raised setting is visible here rather
    # than only in the environment.
    tolerance: float | None = None
    tree_drift_status: str | None = None


class ExecutionRead(BaseModel):
    """The metrics the evidence panel shows first."""

    rows_scanned: int = 0
    rows_in_previous_period: int = 0
    rows_in_current_period: int = 0
    rows_outside_periods: int = 0
    queries_executed: int = 0
    execution_time_ms: int = 0


class DecisionRead(BaseModel):
    sequence: int
    kind: str
    subject: str
    outcome: str
    reason_code: str
    why: str
    dimension: str | None = None
    depth: int = 0
    # Raw and unsafe as a DOM id: it is assembled from user data. Use the tree's
    # ``node_key`` for that.
    node_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceRecordRead(BaseModel):
    id: UUID
    sequence: int
    evidence_type: str
    claim: str
    metric: str | None = None
    dimension: str | None = None
    dimension_value: str | None = None
    dimension_value_is_null: bool = False
    previous_period: str | None = None
    current_period: str | None = None
    previous_value: float | None = None
    current_value: float | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    contribution_percentage: float | None = None
    # Kept separate from ``contribution_percentage`` deliberately: this measures
    # how far segments deviated from moving in proportion, not what share of the
    # change one segment holds. It is not a share, so it can exceed 100%.
    explanatory_power: float | None = None
    filters: list[dict[str, Any]] | None = None
    source_dataset: str
    source_relation: str
    source_columns: list[str] = Field(default_factory=list)
    # The statement that produced these numbers, verbatim, or null for a record
    # derived from other evidence. Never a reconstruction.
    query: str | None = None
    query_sequence: int | None = None
    analysis_tool: str
    validation_status: str
    confidence: str | None = None
    node_id: str | None = None
    depth: int | None = None
    classification: str | None = None
    rank: int = 0
    details: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: EvidenceRecord) -> "EvidenceRecordRead":
        return cls(
            id=row.id,
            sequence=row.sequence,
            evidence_type=row.evidence_type,
            claim=row.claim,
            metric=row.metric,
            dimension=row.dimension,
            dimension_value=row.dimension_value,
            dimension_value_is_null=row.dimension_value_is_null,
            previous_period=row.previous_period,
            current_period=row.current_period,
            previous_value=row.previous_value,
            current_value=row.current_value,
            absolute_change=row.absolute_change,
            percentage_change=row.percentage_change,
            contribution_percentage=row.contribution_percentage,
            explanatory_power=row.explanatory_power,
            filters=row.filters,
            source_dataset=row.source_dataset,
            source_relation=row.source_relation,
            source_columns=list(row.source_columns or []),
            query=row.query,
            query_sequence=row.query_sequence,
            analysis_tool=row.analysis_tool,
            validation_status=row.validation_status,
            confidence=row.confidence,
            node_id=row.node_id,
            depth=row.depth,
            classification=row.classification,
            rank=row.rank,
            details=row.details,
        )


class QueryTraceRead(BaseModel):
    """One executed statement.

    There is no ``parameters`` field, by construction: a bound value can be a
    customer name, so only the count is ever recorded.
    """

    id: UUID
    sequence: int
    purpose: str
    sql: str
    parameter_count: int = 0
    rows_returned: int | None = None
    duration_ms: int = 0
    status: str
    error: str | None = None
    depth: int | None = None
    node_id: str | None = None

    @classmethod
    def from_row(cls, row: InvestigationQuery) -> "QueryTraceRead":
        return cls(
            id=row.id,
            sequence=row.sequence,
            purpose=row.purpose,
            sql=row.sql,
            parameter_count=row.parameter_count,
            rows_returned=row.rows_returned,
            duration_ms=row.duration_ms,
            status=row.status,
            error=row.error,
            depth=row.depth,
            node_id=row.node_id,
        )


class AuditEventRead(BaseModel):
    id: UUID
    sequence: int
    event_type: str
    message: str
    # The reproducible field. ``occurred_at`` is derived from it for display.
    elapsed_ms: int = 0
    occurred_at: datetime
    details: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: InvestigationAuditEvent) -> "AuditEventRead":
        return cls(
            id=row.id,
            sequence=row.sequence,
            event_type=row.event_type,
            message=row.message,
            elapsed_ms=row.elapsed_ms,
            occurred_at=row.occurred_at,
            details=row.details,
        )


class InvestigationSourceRead(BaseModel):
    dataset_id: UUID
    dataset_name: str
    kpi_definition_id: UUID | None = None
    # The storage-key form, not a server temp path.
    source_relation: str
    measure_column: str
    time_column: str | None = None
    aggregation: str
    comparison: str


class InvestigationSummary(BaseModel):
    """A row in the investigation history list."""

    id: UUID
    dataset_id: UUID
    kpi_definition_id: UUID | None = None
    status: str
    analysis_state: str | None = None
    question: str | None = None
    kpi_name: str
    previous_value: float | None = None
    current_value: float | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    change_direction: str | None = None
    severity: str | None = None
    evidence_quality: str | None = None
    reconciliation_status: str | None = None
    evidence_count: int = 0
    created_at: datetime
    completed_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Investigation) -> "InvestigationSummary":
        return cls(
            id=row.id,
            dataset_id=row.dataset_id,
            kpi_definition_id=row.kpi_definition_id,
            status=row.status,
            analysis_state=row.analysis_state,
            question=row.question,
            kpi_name=row.kpi_name,
            previous_value=row.previous_value,
            current_value=row.current_value,
            absolute_change=row.absolute_change,
            percentage_change=row.percentage_change,
            change_direction=row.change_direction,
            severity=row.severity,
            evidence_quality=row.evidence_quality,
            reconciliation_status=row.reconciliation_status,
            evidence_count=row.evidence_count,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )


class InvestigationRead(BaseModel):
    """One investigation, with its findings but not its detail.

    The evidence list, the query trace and the audit trail are deliberately
    absent: they are lazy-loaded from their own endpoints. The counts are here so
    the UI can label and enable those actions without fetching them first.
    """

    id: UUID
    status: str
    analysis_state: str | None = None
    question: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    limitations: list[str] = Field(default_factory=list)

    engine_version: str
    max_drivers: int
    max_tree_depth: int
    grain: str | None = None

    source: InvestigationSourceRead
    execution: ExecutionRead
    evidence_quality: EvidenceQualityRead
    reconciliation: ReconciliationRead
    decisions: list[DecisionRead] = Field(default_factory=list)
    notices: list[dict[str, Any]] = Field(default_factory=list)

    # The findings payload, in the same shape the stateless RCA endpoint returns,
    # so the existing UI components read it unchanged.
    result: dict[str, Any] | None = None
    # The evidence-linked tree. Also inside ``result`` as ``rca_tree``, but that
    # copy carries no evidence ids.
    tree: dict[str, Any] | None = None

    evidence_count: int = 0
    query_count: int = 0
    audit_event_count: int = 0

    @classmethod
    def from_row(
        cls,
        row: Investigation,
        *,
        dataset_name: str,
        query_count: int,
        audit_event_count: int,
    ) -> "InvestigationRead":
        checks = row.quality_checks or {}
        return cls(
            id=row.id,
            status=row.status,
            analysis_state=row.analysis_state,
            question=row.question,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            error_code=row.error_code,
            error_message=row.error_message,
            limitations=list(row.limitations or []),
            engine_version=row.engine_version,
            max_drivers=row.max_drivers,
            max_tree_depth=row.max_tree_depth,
            grain=row.grain,
            source=InvestigationSourceRead(
                dataset_id=row.dataset_id,
                dataset_name=dataset_name,
                kpi_definition_id=row.kpi_definition_id,
                source_relation=row.source_relation,
                measure_column=row.measure_column,
                time_column=row.time_column,
                aggregation=row.aggregation,
                comparison=row.comparison,
            ),
            execution=ExecutionRead(
                rows_scanned=row.rows_scanned,
                rows_in_previous_period=row.rows_in_previous_period,
                rows_in_current_period=row.rows_in_current_period,
                rows_outside_periods=row.rows_outside_periods,
                queries_executed=row.queries_executed,
                execution_time_ms=row.execution_time_ms,
            ),
            evidence_quality=EvidenceQualityRead(
                verdict=row.evidence_quality,
                checks=[QualityCheckRead(**c) for c in checks.get("checks", [])],
                caveats=list(checks.get("caveats", [])),
            ),
            reconciliation=ReconciliationRead(
                status=row.reconciliation_status,
                contribution_sum=row.contribution_sum,
                tolerance=row.reconciliation_tolerance,
                tree_drift_status=row.tree_drift_status,
            ),
            decisions=[DecisionRead(**d) for d in (row.decisions or [])],
            notices=list(row.notices or []),
            result=row.result,
            tree=row.tree,
            evidence_count=row.evidence_count,
            query_count=query_count,
            audit_event_count=audit_event_count,
        )


class InvestigationTreeRead(BaseModel):
    """The tree on its own, for a client that wants only the hierarchy."""

    investigation_id: UUID
    tree: dict[str, Any] | None = None
