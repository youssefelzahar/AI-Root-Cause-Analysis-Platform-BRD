"""Persisted investigations and the evidence behind them.

Four tables rather than one JSON blob, because the read patterns differ. An
investigation is fetched whole; evidence is filtered and paged and has to be
addressable by its own id; the query trace is the largest thing in the record and
must not be dragged along by every read; audit events are worth querying across
investigations. What nothing ever filters inside - the findings payload, the
tree, the decision trace - stays in JSON.

``EvidenceRecord`` here is the persisted row. It is *not*
``app.analysis.rca.models.Evidence`` (flat execution counters on one result) nor
``app.analysis.anomaly.models.Evidence`` (the same idea for a series). Those two
predate this table and are deliberately left alone.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, JSONColumn, TimestampMixin, UUIDPkMixin
from app.db.models.enums import (
    AuditEventType,
    EvidenceConfidence,
    EvidenceQuality,
    EvidenceType,
    EvidenceValidationStatus,
    InvestigationStatus,
    QueryStatus,
    ReconciliationStatus,
    TreeDriftStatus,
)

if TYPE_CHECKING:
    from app.db.models.dataset import Dataset

# Bumped when a change to the engines or the evidence builder would make two
# investigations of the same data non-comparable. The de-dup key includes it, so
# a bump invalidates reuse rather than silently serving a stale shape.
ENGINE_VERSION = "1.0"


def _check(column: str, enum: type, name: str) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class Investigation(UUIDPkMixin, TimestampMixin, Base):
    """One investigation: the plan, the answer, and the verdicts on it.

    The execution metrics and the verdicts are typed columns rather than JSON
    because the evidence panel shows them first and they are worth filtering on
    ("every failed reconciliation this week"). The findings payload behind them
    is never queried into, so it stays JSON.
    """

    __tablename__ = "investigations"
    __table_args__ = (
        _check("status", InvestigationStatus, "status_valid"),
        _check("reconciliation_status", ReconciliationStatus, "reconciliation_status_valid"),
        _check("tree_drift_status", TreeDriftStatus, "tree_drift_status_valid"),
        _check("evidence_quality", EvidenceQuality, "evidence_quality_valid"),
        Index("ix_investigations_company_created", "company_id", "created_at"),
        Index("ix_investigations_dataset_created", "dataset_id", "created_at"),
        Index("ix_investigations_company_status", "company_id", "status"),
    )

    # --- ownership ---------------------------------------------------------
    # Denormalised and un-FK'd, like kpi_definitions.company_id: every read is
    # already scoped by it, and the join buys nothing.
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kpi_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kpi_definitions.id", ondelete="SET NULL")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # --- lifecycle ---------------------------------------------------------
    question: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(20), default=InvestigationStatus.PLANNED.value, nullable=False
    )
    # RcaResult.state, mirrored out of the JSON payload so "show me every
    # investigation that found no previous period" is one indexed query.
    analysis_state: Mapped[str | None] = mapped_column(String(30))

    # --- the plan ----------------------------------------------------------
    kpi_name: Mapped[str] = mapped_column(String(150), nullable=False)
    measure_column: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregation: Mapped[str] = mapped_column(String(20), nullable=False)
    time_column: Mapped[str | None] = mapped_column(String(255))
    comparison: Mapped[str] = mapped_column(String(30), nullable=False)
    grain: Mapped[str | None] = mapped_column(String(20))
    max_drivers: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tree_depth: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- the answer, in typed form -----------------------------------------
    # Half-open windows: end is exclusive, matching Period.
    previous_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_value: Mapped[float | None] = mapped_column(Float)
    current_value: Mapped[float | None] = mapped_column(Float)
    absolute_change: Mapped[float | None] = mapped_column(Float)
    percentage_change: Mapped[float | None] = mapped_column(Float)
    change_direction: Mapped[str | None] = mapped_column(String(10))
    severity: Mapped[str | None] = mapped_column(String(10))

    # --- execution metadata ------------------------------------------------
    rows_scanned: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_in_previous_period: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_in_current_period: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rows_outside_periods: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    queries_executed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    execution_time_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- verdicts ----------------------------------------------------------
    contribution_sum: Mapped[float | None] = mapped_column(Float)
    reconciliation_status: Mapped[str | None] = mapped_column(String(20))
    # The tolerance actually applied, so a raised setting is visible in the
    # record rather than only in the environment.
    reconciliation_tolerance: Mapped[float | None] = mapped_column(Float)
    tree_drift_status: Mapped[str | None] = mapped_column(String(20))
    evidence_quality: Mapped[str | None] = mapped_column(String(20))
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- payloads nothing filters inside -----------------------------------
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    tree: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    decisions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONColumn)
    quality_checks: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    limitations: Mapped[list[str] | None] = mapped_column(JSONColumn)
    notices: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONColumn)

    # --- provenance and timing ---------------------------------------------
    engine_version: Mapped[str] = mapped_column(
        String(20), default=ENGINE_VERSION, nullable=False
    )
    # The storage-key form, never what open_dataset_relation yields: that embeds
    # an absolute server temp path which differs per request for xlsx.
    source_relation: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)

    dataset: Mapped["Dataset"] = relationship()


class EvidenceRecord(UUIDPkMixin, Base):
    """One structured claim, with the provenance to check it.

    No ``TimestampMixin``: rows are written once with their investigation and
    never updated, so an ``updated_at`` would only ever repeat ``created_at``.

    The id is derived, not random - see ``investigation.models.evidence_id``.
    That is what lets the tree reference evidence before any row exists.
    """

    __tablename__ = "investigation_evidence"
    __table_args__ = (
        _check("evidence_type", EvidenceType, "evidence_type_valid"),
        _check("validation_status", EvidenceValidationStatus, "validation_status_valid"),
        _check("confidence", EvidenceConfidence, "confidence_valid"),
        Index(
            "ix_investigation_evidence_investigation_type",
            "investigation_id",
            "evidence_type",
        ),
        Index(
            "ix_investigation_evidence_investigation_sequence",
            "investigation_id",
            "sequence",
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised so GET /api/evidence/{id} - whose path carries no dataset - is
    # one indexed read rather than a two-table join.
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)

    metric: Mapped[str | None] = mapped_column(String(150))
    dimension: Mapped[str | None] = mapped_column(String(255))
    dimension_value: Mapped[str | None] = mapped_column(String(500))
    # A genuine NULL segment is a finding; the string "None" is a different one.
    dimension_value_is_null: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    previous_period: Mapped[str | None] = mapped_column(String(100))
    current_period: Mapped[str | None] = mapped_column(String(100))
    previous_value: Mapped[float | None] = mapped_column(Float)
    current_value: Mapped[float | None] = mapped_column(Float)
    absolute_change: Mapped[float | None] = mapped_column(Float)
    percentage_change: Mapped[float | None] = mapped_column(Float)
    contribution_percentage: Mapped[float | None] = mapped_column(Float)
    # Its own column, never folded into contribution_percentage: contribution is
    # how much a segment moved the total, explainability is how far the segments
    # deviated from moving in proportion. It can legitimately exceed 100%.
    explanatory_power: Mapped[float | None] = mapped_column(Float)

    filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONColumn)
    source_dataset: Mapped[str] = mapped_column(String(255), nullable=False)
    source_relation: Mapped[str] = mapped_column(Text, nullable=False)
    source_columns: Mapped[list[str]] = mapped_column(
        JSONColumn, default=list, nullable=False
    )
    # The statement that produced these numbers, verbatim, or NULL for a derived
    # record. Never a plausible-looking reconstruction.
    query: Mapped[str | None] = mapped_column(Text)
    query_sequence: Mapped[int | None] = mapped_column(Integer)
    analysis_tool: Mapped[str] = mapped_column(String(50), nullable=False)
    validation_status: Mapped[str] = mapped_column(
        String(20), default=EvidenceValidationStatus.UNVERIFIED.value, nullable=False
    )
    confidence: Mapped[str | None] = mapped_column(String(10))

    # --- link back to the tree --------------------------------------------
    node_id: Mapped[str | None] = mapped_column(String(500))
    depth: Mapped[int | None] = mapped_column(Integer)
    classification: Mapped[str | None] = mapped_column(String(20))
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    details: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InvestigationQuery(UUIDPkMixin, Base):
    """One statement the engines actually executed.

    Its own table because the SQL text is the largest thing in the record and
    ``GET /api/investigations/{id}`` must not carry it.

    There is no ``parameters`` column, deliberately: filters and drill predicates
    bind their values, so a parameter can be a customer name. Only the count is
    kept - the same reasoning that pins ``sqlalchemy.engine`` logging to WARNING.
    """

    __tablename__ = "investigation_queries"
    __table_args__ = (
        _check("status", QueryStatus, "status_valid"),
        UniqueConstraint(
            "investigation_id", "sequence", name="uq_investigation_queries_sequence"
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_returned: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), default=QueryStatus.OK.value, nullable=False
    )
    error: Mapped[str | None] = mapped_column(String(500))
    depth: Mapped[int | None] = mapped_column(Integer)
    node_id: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InvestigationAuditEvent(UUIDPkMixin, Base):
    """What happened during one investigation, in order.

    ``elapsed_ms`` is the reproducible field - a monotonic offset from the run's
    start, which is identical across two runs of the same data. ``occurred_at``
    is derived from it for display and is not.
    """

    __tablename__ = "investigation_audit_events"
    __table_args__ = (
        _check("event_type", AuditEventType, "event_type_valid"),
        Index(
            "ix_investigation_audit_events_sequence", "investigation_id", "sequence"
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
