"""Orchestration and persistence for evidence-backed investigations.

Resolves the dataset, its KPI definition and the profiled reporting frequency,
hands a plain plan to the pure investigation engine, then persists everything it
produced. This is the only layer that touches the database or storage;
``app.analysis.investigation`` never imports either.

Unlike ``rca_service``, an investigation *is* persisted: it has an id, a status
and a history, so a link to one is a snapshot rather than an instruction to
recompute.

The lifecycle is committed in stages - PLANNED, then RUNNING, then a terminal
status - so the status is observable rather than merely asserted. A status that
only ever exists inside one transaction is not a persisted status.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.duckdb_session import source_relation
from app.analysis.investigation import investigate
from app.analysis.investigation.models import (
    InvestigationOutcome,
    InvestigationPlan,
    Provenance,
    derived_id,
)
from app.analysis.rca.constants import MAX_PRIMARY_DRIVERS, MAX_TREE_DEPTH
from app.analysis.trace import Probe
from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError, NotReadyError
from app.core.logging import get_logger
from app.db.models import (
    ENGINE_VERSION,
    Dataset,
    EvidenceRecord,
    Investigation,
    InvestigationAuditEvent,
    InvestigationQuery,
    KpiDefinition,
)
from app.db.models.enums import DatasetStatus, EvidenceType, FileFormat, InvestigationStatus
from app.schemas.common import Page
from app.schemas.rca import to_result
from app.services import dataset_service, kpi_service, rca_service
from app.services.dataset_source import open_dataset_relation

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _stored_relation(dataset: Dataset) -> str:
    """The relation expression in terms of the storage key, not a local path.

    Deliberately not what ``open_dataset_relation`` yields: for an xlsx dataset
    that is an absolute server temp path which differs on every request, so
    persisting it would leak the server layout and make the record
    irreproducible.
    """
    key = dataset.normalized_key or dataset.storage_key or ""
    fmt = FileFormat.PARQUET.value if dataset.normalized_key else dataset.file_format
    return source_relation(Path(key), fmt)


def _plan(
    investigation: Investigation,
    definition: KpiDefinition,
    dataset: Dataset,
    frequency: str | None,
    physical: tuple[str, ...],
) -> InvestigationPlan:
    spec = rca_service.build_spec(
        definition,
        frequency,
        max_drivers=investigation.max_drivers,
        max_tree_depth=investigation.max_tree_depth,
    )
    return InvestigationPlan(
        investigation_id=investigation.id,
        spec=spec,
        provenance=Provenance(
            dataset_id=dataset.id,
            dataset_name=dataset.name,
            source_relation=investigation.source_relation,
            measure_column=spec.measure_column,
            time_column=spec.time_column,
            filters=spec.filters,
            physical_columns=physical,
        ),
        reconciliation_tolerance=settings.investigation_reconciliation_tolerance,
        question=investigation.question,
    )


# --- creation -----------------------------------------------------------------


def create(
    db: Session,
    dataset_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    kpi_definition_id: uuid.UUID | None = None,
    max_drivers: int = MAX_PRIMARY_DRIVERS,
    max_tree_depth: int = MAX_TREE_DEPTH,
    question: str | None = None,
    refresh: bool = False,
) -> tuple[Investigation, bool]:
    """Run and persist an investigation. Returns ``(row, created)``.

    ``created`` is False when an existing completed investigation was reused, so
    the route can answer 200 rather than 201 without guessing.

    Everything is resolved and gated *before* any row is written: a request that
    fails the readiness check must not leave a FAILED investigation behind, or
    the history fills up with rejected requests.
    """
    dataset = dataset_service.get_dataset(db, dataset_id, company_id)

    if dataset.status != DatasetStatus.ANALYSIS_READY.value:
        raise NotReadyError(
            "This dataset is not ready for analysis yet. Configure a KPI to make it "
            "Analysis Ready.",
            code="DATASET_NOT_ANALYSIS_READY",
            details={"status": dataset.status},
        )

    definition = kpi_service.resolve_definition(db, dataset, kpi_definition_id)
    frequency = kpi_service.detected_frequency(db, dataset, definition.time_column)

    if not refresh:
        existing = _reusable(db, dataset, definition, max_drivers, max_tree_depth)
        if existing is not None:
            logger.info(
                "investigation_reused",
                extra={"investigation_id": str(existing.id), "dataset_id": str(dataset.id)},
            )
            return existing, False

    investigation = Investigation(
        company_id=company_id,
        dataset_id=dataset.id,
        kpi_definition_id=definition.id,
        created_by=user_id,
        question=question,
        status=InvestigationStatus.PLANNED.value,
        kpi_name=definition.name,
        measure_column=definition.column_name,
        aggregation=definition.aggregation,
        time_column=definition.time_column,
        comparison=definition.comparison,
        max_drivers=max_drivers,
        max_tree_depth=max_tree_depth,
        engine_version=ENGINE_VERSION,
        source_relation=_stored_relation(dataset),
        reconciliation_tolerance=settings.investigation_reconciliation_tolerance,
    )
    db.add(investigation)
    db.commit()
    db.refresh(investigation)

    investigation.status = InvestigationStatus.RUNNING.value
    investigation.started_at = _now()
    db.commit()

    try:
        outcome = _run(db, investigation, dataset, definition, frequency)
    except AppError as exc:
        # A failed investigation is a persisted state, not a vanished one: the
        # spec requires FAILED to exist and requires an audit trail, and both are
        # impossible if the row disappears. The error is still raised, so the
        # client gets the usual typed envelope.
        investigation.status = InvestigationStatus.FAILED.value
        investigation.completed_at = _now()
        investigation.error_code = exc.code
        investigation.error_message = exc.message
        db.commit()
        logger.warning(
            "investigation_failed",
            extra={
                "investigation_id": str(investigation.id),
                "dataset_id": str(dataset.id),
                "code": exc.code,
            },
        )
        raise

    _persist(db, investigation, outcome)
    logger.info(
        "investigation_completed",
        extra={
            "investigation_id": str(investigation.id),
            "dataset_id": str(dataset.id),
            "status": investigation.status,
            "state": investigation.analysis_state,
            "evidence": investigation.evidence_count,
            "statements": investigation.queries_executed,
            "duration_ms": investigation.execution_time_ms,
            "evidence_quality": investigation.evidence_quality,
        },
    )
    return investigation, True


def _reusable(
    db: Session,
    dataset: Dataset,
    definition: KpiDefinition,
    max_drivers: int,
    max_tree_depth: int,
) -> Investigation | None:
    """An existing investigation that would compute exactly this again.

    Guarded on the dataset's own modification time rather than on a checksum: if
    the file has been re-profiled or replaced since the run completed, the run is
    not reusable no matter what its inputs were.

    This is the honest reading of "reuse cached analytical results". A result
    *cache* is deliberately not built: a stale hit would attach a real query
    trace to numbers that trace did not produce, which is exactly the fabrication
    the evidence layer exists to prevent.
    """
    candidates = db.scalars(
        select(Investigation)
        .where(
            Investigation.dataset_id == dataset.id,
            Investigation.kpi_definition_id == definition.id,
            Investigation.max_drivers == max_drivers,
            Investigation.max_tree_depth == max_tree_depth,
            Investigation.engine_version == ENGINE_VERSION,
            Investigation.status.in_(
                [InvestigationStatus.COMPLETED.value, InvestigationStatus.PARTIAL.value]
            ),
            Investigation.completed_at.is_not(None),
        )
        .order_by(Investigation.completed_at.desc())
        .limit(5)
    ).all()

    for candidate in candidates:
        if _outlasts(candidate.completed_at, dataset.updated_at) and _outlasts(
            candidate.completed_at, definition.updated_at
        ):
            return candidate
    return None


def _outlasts(completed_at: datetime | None, changed_at: datetime | None) -> bool:
    """Whether the run finished after the thing it read was last changed."""
    if completed_at is None:
        return False
    if changed_at is None:
        return True
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=UTC)
    return completed_at >= changed_at


def _run(
    db: Session,
    investigation: Investigation,
    dataset: Dataset,
    definition: KpiDefinition,
    frequency: str | None,
) -> InvestigationOutcome:
    with open_dataset_relation(dataset) as (conn, relation):
        physical = tuple(
            row[0] for row in conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
        )
        plan = _plan(investigation, definition, dataset, frequency, physical)
        return investigate(conn, relation, plan, probe=Probe())


# --- persistence --------------------------------------------------------------


def _persist(db: Session, investigation: Investigation, outcome: InvestigationOutcome) -> None:
    """Write the outcome and its children in one commit."""
    result = outcome.result
    kpi = result.kpi
    periods = result.periods
    evidence = result.evidence
    started = investigation.started_at or _now()

    investigation.status = outcome.status.value
    investigation.analysis_state = result.state.value
    investigation.grain = kpi.grain
    investigation.completed_at = _now()

    if periods is not None:
        investigation.previous_period_start = periods.previous.start
        investigation.previous_period_end = periods.previous.end
        investigation.current_period_start = periods.current.start
        investigation.current_period_end = periods.current.end

    investigation.previous_value = kpi.previous_value
    investigation.current_value = kpi.current_value
    investigation.absolute_change = kpi.absolute_change
    investigation.percentage_change = kpi.percent_change
    investigation.change_direction = kpi.direction
    investigation.severity = kpi.severity

    investigation.rows_scanned = evidence.total_rows
    investigation.rows_in_previous_period = evidence.previous_rows
    investigation.rows_in_current_period = evidence.current_rows
    investigation.rows_outside_periods = evidence.rows_outside_periods
    investigation.queries_executed = len(outcome.queries)
    investigation.execution_time_ms = _execution_time(outcome)

    investigation.contribution_sum = outcome.reconciliation.contribution_sum
    investigation.reconciliation_status = outcome.reconciliation.status.value
    investigation.reconciliation_tolerance = outcome.reconciliation.tolerance
    investigation.tree_drift_status = outcome.reconciliation.tree_drift_status.value
    investigation.evidence_quality = outcome.quality.verdict.value
    investigation.evidence_count = len(outcome.evidence)

    investigation.result = to_result(
        dataset_id=investigation.dataset_id,
        dataset_name=investigation.dataset.name,
        kpi_definition_id=investigation.kpi_definition_id,
        generated_at=investigation.completed_at,
        result=result,
    ).model_dump(mode="json")
    investigation.tree = outcome.tree
    investigation.decisions = [_decision_payload(d) for d in outcome.decisions]
    investigation.quality_checks = {
        "verdict": outcome.quality.verdict.value,
        "caveats": list(outcome.quality.caveats),
        "checks": [
            {
                "check": c.check,
                "status": c.status.value,
                "detail": c.detail,
                "inputs": c.inputs,
            }
            for c in outcome.quality.checks
        ],
    }
    investigation.limitations = list(outcome.limitations)
    investigation.notices = [
        {
            "code": n.code,
            "severity": n.severity,
            "message": n.message,
            "details": n.details,
        }
        for n in outcome.notices
    ]

    for record in outcome.evidence:
        db.add(_evidence_row(investigation, record))
    for query in outcome.queries:
        db.add(
            InvestigationQuery(
                id=derived_id(investigation.id, "query", str(query.sequence)),
                investigation_id=investigation.id,
                sequence=query.sequence,
                purpose=query.purpose.value,
                sql=query.sql,
                parameter_count=query.parameter_count,
                rows_returned=query.rows_returned,
                duration_ms=query.duration_ms,
                status=query.status.value,
                error=query.error,
                depth=query.depth,
                node_id=query.node_id,
            )
        )
    for event in outcome.audit:
        db.add(
            InvestigationAuditEvent(
                id=derived_id(investigation.id, "audit", str(event.sequence)),
                investigation_id=investigation.id,
                sequence=event.sequence,
                event_type=event.event_type.value,
                message=event.message,
                elapsed_ms=event.elapsed_ms,
                # Derived from the reproducible offset rather than measured
                # separately, so the ordering can never contradict it.
                occurred_at=started + timedelta(milliseconds=event.elapsed_ms),
                details=event.details or None,
            )
        )

    db.commit()
    db.refresh(investigation)


def _execution_time(outcome: InvestigationOutcome) -> int:
    """The whole run's wall clock, taken from the execution evidence.

    The engine already recorded it there; reading it back beats measuring a
    second, slightly different number in this layer.
    """
    for record in outcome.evidence:
        if record.evidence_type is EvidenceType.EXECUTION:
            return int(record.details.get("execution_time_ms") or 0)
    return outcome.result.evidence.duration_ms


def _decision_payload(decision) -> dict:
    return {
        "sequence": decision.sequence,
        "kind": decision.kind.value,
        "subject": decision.subject,
        "outcome": decision.outcome,
        "reason_code": decision.reason_code,
        "why": decision.why,
        "dimension": decision.dimension,
        "depth": decision.depth,
        "node_id": decision.node_id,
        "inputs": decision.inputs,
        "evidence_ids": list(decision.evidence_ids),
    }


def _evidence_row(investigation: Investigation, record) -> EvidenceRecord:
    return EvidenceRecord(
        id=record.id,
        investigation_id=investigation.id,
        company_id=investigation.company_id,
        sequence=record.sequence,
        evidence_type=record.evidence_type.value,
        claim=record.claim,
        metric=record.metric,
        dimension=record.dimension,
        dimension_value=record.dimension_value,
        dimension_value_is_null=record.dimension_value_is_null,
        previous_period=record.previous_period,
        current_period=record.current_period,
        previous_value=record.previous_value,
        current_value=record.current_value,
        absolute_change=record.absolute_change,
        percentage_change=record.percentage_change,
        contribution_percentage=record.contribution_percentage,
        explanatory_power=record.explanatory_power,
        filters=list(record.filters) or None,
        source_dataset=record.source_dataset,
        source_relation=record.source_relation,
        source_columns=list(record.source_columns),
        query=record.query,
        query_sequence=record.query_sequence,
        analysis_tool=record.analysis_tool,
        validation_status=record.validation_status.value,
        confidence=record.confidence.value if record.confidence else None,
        node_id=record.node_id,
        depth=record.depth,
        classification=record.classification,
        rank=record.rank,
        details={**record.details, "derived": record.derived} if record.details else None,
    )


# --- reads --------------------------------------------------------------------


def get(db: Session, investigation_id: uuid.UUID, company_id: uuid.UUID) -> Investigation:
    """One investigation, scoped to its company.

    Cross-company access returns 404 rather than 403, matching
    ``dataset_service.get_dataset``: the API never confirms that another tenant's
    investigation exists.
    """
    investigation = db.scalar(
        select(Investigation).where(
            Investigation.id == investigation_id,
            Investigation.company_id == company_id,
        )
    )
    if investigation is None:
        raise NotFoundError("Investigation not found.", code="INVESTIGATION_NOT_FOUND")
    return investigation


def list_investigations(
    db: Session,
    company_id: uuid.UUID,
    *,
    dataset_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Investigation], int]:
    filters = [Investigation.company_id == company_id]
    if dataset_id is not None:
        filters.append(Investigation.dataset_id == dataset_id)
    if status is not None:
        filters.append(Investigation.status == status)

    total = db.scalar(select(func.count()).select_from(Investigation).where(*filters)) or 0
    rows = db.scalars(
        select(Investigation)
        .where(*filters)
        .order_by(Investigation.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def list_evidence(
    db: Session,
    investigation: Investigation,
    *,
    evidence_types: list[str] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[EvidenceRecord], int]:
    filters = [EvidenceRecord.investigation_id == investigation.id]
    if evidence_types:
        filters.append(EvidenceRecord.evidence_type.in_(evidence_types))

    total = db.scalar(select(func.count()).select_from(EvidenceRecord).where(*filters)) or 0
    rows = db.scalars(
        select(EvidenceRecord)
        .where(*filters)
        .order_by(EvidenceRecord.sequence)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def get_evidence(db: Session, evidence_id: uuid.UUID, company_id: uuid.UUID) -> EvidenceRecord:
    """One evidence record by its own id.

    The path carries no dataset, which is why ``company_id`` is denormalised onto
    the row: this stays a single indexed read on a lazy-loaded path.
    """
    record = db.scalar(
        select(EvidenceRecord).where(
            EvidenceRecord.id == evidence_id,
            EvidenceRecord.company_id == company_id,
        )
    )
    if record is None:
        raise NotFoundError("Evidence not found.", code="EVIDENCE_NOT_FOUND")
    return record


def list_queries(
    db: Session, investigation: Investigation, *, limit: int = 200, offset: int = 0
) -> tuple[list[InvestigationQuery], int]:
    total = (
        db.scalar(
            select(func.count())
            .select_from(InvestigationQuery)
            .where(InvestigationQuery.investigation_id == investigation.id)
        )
        or 0
    )
    rows = db.scalars(
        select(InvestigationQuery)
        .where(InvestigationQuery.investigation_id == investigation.id)
        .order_by(InvestigationQuery.sequence)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def list_audit(
    db: Session, investigation: Investigation, *, limit: int = 200, offset: int = 0
) -> tuple[list[InvestigationAuditEvent], int]:
    total = (
        db.scalar(
            select(func.count())
            .select_from(InvestigationAuditEvent)
            .where(InvestigationAuditEvent.investigation_id == investigation.id)
        )
        or 0
    )
    rows = db.scalars(
        select(InvestigationAuditEvent)
        .where(InvestigationAuditEvent.investigation_id == investigation.id)
        .order_by(InvestigationAuditEvent.sequence)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def paged(items: list, total: int, limit: int, offset: int) -> Page:
    return Page(items=items, total=total, limit=limit, offset=offset)


# --- housekeeping -------------------------------------------------------------


def reconcile_stale_investigations(db: Session) -> int:
    """Mark investigations abandoned by a restart as failed.

    An investigation runs synchronously inside one request, so a row still
    RUNNING after the cutoff means the process died mid-flight. Cheap insurance,
    mirroring ``profiling_service.reconcile_stale_jobs``.
    """
    cutoff = _now() - timedelta(minutes=settings.investigation_stale_minutes)
    stale = db.scalars(
        select(Investigation).where(
            Investigation.status.in_(
                [InvestigationStatus.PLANNED.value, InvestigationStatus.RUNNING.value]
            ),
            Investigation.created_at < cutoff,
        )
    ).all()
    for investigation in stale:
        investigation.status = InvestigationStatus.FAILED.value
        investigation.completed_at = _now()
        investigation.error_code = "INVESTIGATION_ABANDONED"
        investigation.error_message = (
            "The investigation was interrupted before it finished. Run it again."
        )
    if stale:
        db.commit()
    return len(stale)
