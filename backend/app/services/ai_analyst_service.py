"""Orchestration for the AI analyst.

The only layer here that touches the database. It resolves the dataset and its KPI
definitions, asks ``app.ai`` what the question means, runs or reuses one
investigation through ``investigation_service``, and hands the persisted rows back
to the pure layer to be grounded and explained.

The shape mirrors ``investigation_service``, which mirrors ``rca_service``: the
analysis is pure and lives elsewhere, the session lives here. What is different is
that nothing new is persisted. §24 asks for short-term conversational context and
§38 forbids long-term memory, and the platform already has the durable artifact - an
``Investigation`` with an id, a status, a query trace and an audit trail. A parallel
table of chat turns would duplicate it and immediately disagree with it.

One happy accident makes the integration tidy: ``Investigation.question`` has existed
since the evidence layer, is plumbed end to end, and nothing set it until now. So the
question that prompted an investigation is recorded on the investigation itself.
"""

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import executor, grounding, planner, resolve
from app.ai import explain as explain_module
from app.ai import intent as intent_module
from app.ai.constants import ANALYST_RULES_VERSION, TOOL_DETECT_ANOMALY
from app.ai.models import (
    AnalystOutcome,
    AnalystStatus,
    IntentKind,
    KpiChoice,
)
from app.ai.providers import get_llm_provider
from app.ai.tools import ToolContext
from app.analysis.anomaly.constants import BASELINE_WINDOW
from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError, NotReadyError
from app.core.logging import get_logger
from app.db.models import Investigation, KpiDefinition
from app.db.models.enums import DatasetStatus
from app.schemas.anomaly import to_report
from app.services import anomaly_service, dataset_service, investigation_service

logger = get_logger(__name__)

# Types worth handing to the explanation. The six validation records and the query
# trace are what make an investigation checkable, and they belong in the evidence
# panel - not in a prompt, where they would crowd out the finding.
GROUNDING_EVIDENCE_TYPES = (
    "kpi_change",
    "comparison",
    "contribution",
    "dimension_change",
    "drill_down",
    "gone_segment",
    "new_segment",
    "offsetting_factor",
    "anomaly",
)


def _require_enabled() -> None:
    if not settings.ai_enabled:
        raise NotReadyError(
            "The AI analyst is turned off on this deployment.",
            code="AI_DISABLED",
        )


def _kpi_choices(db: Session, dataset_id: uuid.UUID) -> tuple[KpiChoice, ...]:
    """Every KPI definition on this dataset, active one first.

    Superseded definitions are included deliberately: a reader who asks about a
    metric that was configured last week should be told it exists and has been
    replaced, rather than silently getting the current one.
    """
    rows = db.scalars(
        select(KpiDefinition)
        .where(KpiDefinition.dataset_id == dataset_id)
        .order_by(KpiDefinition.is_active.desc(), KpiDefinition.updated_at.desc())
    ).all()
    return tuple(
        KpiChoice(
            kpi_definition_id=row.id,
            name=row.name,
            measure_column=row.column_name,
            aggregation=row.aggregation,
            is_active=bool(row.is_active),
        )
        for row in rows
    )


def _dimensions(choices: tuple[KpiChoice, ...], db: Session, dataset_id: uuid.UUID) -> tuple[str, ...]:
    """The dimension names configured across this dataset's definitions.

    Used to tell the model what exists and to validate what it returns. Union
    rather than the active definition's list alone, so a question naming a
    dimension from a superseded definition is recognised rather than dropped as a
    hallucination.
    """
    rows = db.scalars(
        select(KpiDefinition).where(KpiDefinition.dataset_id == dataset_id)
    ).all()
    names: list[str] = []
    for row in rows:
        for dimension in row.dimensions or []:
            if dimension not in names:
                names.append(dimension)
    return tuple(names)


def _investigation_payload(investigation: Investigation) -> dict:
    """The persisted row as plain data, for the pure tool layer.

    Explicit rather than a generic ORM dump, for the reason
    ``schemas.investigation`` gives about ``from_row``: an explicit mapping is what
    stops a new column silently appearing where the AI layer can read it.
    """
    return {
        "id": investigation.id,
        "status": investigation.status,
        "analysis_state": investigation.analysis_state,
        "question": investigation.question,
        "result": investigation.result,
        "tree": investigation.tree,
        "quality_checks": investigation.quality_checks,
        "limitations": investigation.limitations or [],
        "notices": investigation.notices or [],
        "evidence_quality": investigation.evidence_quality,
        "reconciliation_status": investigation.reconciliation_status,
        "tree_drift_status": investigation.tree_drift_status,
        "contribution_sum": investigation.contribution_sum,
        "rows_scanned": investigation.rows_scanned,
        "rows_in_previous_period": investigation.rows_in_previous_period,
        "rows_in_current_period": investigation.rows_in_current_period,
        "queries_executed": investigation.queries_executed,
        "execution_time_ms": investigation.execution_time_ms,
        "evidence_count": investigation.evidence_count,
    }


def _evidence_payload(db: Session, investigation: Investigation) -> list[dict]:
    rows, _ = investigation_service.list_evidence(
        db,
        investigation,
        evidence_types=list(GROUNDING_EVIDENCE_TYPES),
        limit=200,
    )
    return [
        {
            "id": row.id,
            "evidence_type": row.evidence_type,
            "claim": row.claim,
            "dimension": row.dimension,
            "dimension_value": row.dimension_value,
            "absolute_change": row.absolute_change,
            "contribution_percentage": row.contribution_percentage,
            "confidence": row.confidence,
            "validation_status": row.validation_status,
        }
        for row in rows
    ]


def _anomaly_payload(
    db: Session,
    dataset_id: uuid.UUID,
    company_id: uuid.UUID,
    kpi_definition_id: uuid.UUID | None,
) -> dict | None:
    """The full anomaly report, or None when it could not be produced.

    The one genuinely separate computation in the layer. A failure is a limitation,
    never an error: an investigation is complete without it, and turning a usable
    answer into a 502 because an optional step could not run would be the wrong
    trade - the same judgement the investigation engine already makes.
    """
    try:
        dataset, definition, report = anomaly_service.run(
            db, dataset_id, company_id, kpi_definition_id=kpi_definition_id
        )
    except AppError as exc:
        logger.info("ai_anomaly_skipped", extra={"code": exc.code})
        return None

    detection = to_report(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        kpi_definition_id=definition.id,
        generated_at=datetime.now(UTC),
        measure_column=definition.column_name,
        time_column=definition.time_column,
        # The report does not carry its own spec back, and this layer never
        # overrides the window, so the engine's default is the value that was used.
        baseline_window=BASELINE_WINDOW,
        report=report,
    )
    payload = detection.model_dump(mode="json")
    # The engine reports the method as a nested object; the tool reads a flat name
    # too, so both are available without the tool knowing the wire shape.
    method = payload.get("method") or {}
    payload["method_name"] = method.get("name")
    return payload


def analyze(
    db: Session,
    dataset_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    question: str,
    user_id: uuid.UUID | None = None,
    kpi_definition_id: uuid.UUID | None = None,
    investigation_id: uuid.UUID | None = None,
    refresh: bool = False,
) -> AnalystOutcome:
    """Answer one question, grounded in one investigation.

    Everything is resolved and gated before any analysis runs, matching
    ``investigation_service.create``: a question that cannot be answered must not
    leave an investigation behind.
    """
    _require_enabled()
    started = time.perf_counter()

    dataset = dataset_service.get_dataset(db, dataset_id, company_id)
    if dataset.status != DatasetStatus.ANALYSIS_READY.value:
        raise NotReadyError(
            "This dataset is not ready for analysis yet. Configure a KPI to make it "
            "Analysis Ready.",
            code="DATASET_NOT_ANALYSIS_READY",
            details={"status": dataset.status},
        )

    provider = get_llm_provider()
    choices = _kpi_choices(db, dataset.id)
    dimensions = _dimensions(choices, db, dataset.id)

    previous: Investigation | None = None
    if investigation_id is not None:
        previous = investigation_service.get(db, investigation_id, company_id)
        if previous.dataset_id != dataset.id:
            # Scoped by company already, so this is a client mixing two datasets
            # rather than a tenancy breach. 404 keeps the two indistinguishable.
            raise NotFoundError(
                "That investigation does not belong to this dataset.",
                code="INVESTIGATION_NOT_FOUND",
            )

    # --- 1. understand
    intent = intent_module.understand(
        question,
        provider=provider,
        kpi_names=tuple(c.name for c in choices),
        dimensions=dimensions,
        has_previous=previous is not None,
    )

    # --- 2. resolve
    context = resolve.resolve(
        intent,
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        choices=choices,
        dimensions=dimensions,
        investigation_id=previous.id if previous else None,
        carried_kpi_definition_id=(
            kpi_definition_id or (previous.kpi_definition_id if previous else None)
        ),
    )
    if context.blocked:
        logger.info(
            "ai_clarification_requested",
            extra={
                "dataset_id": str(dataset.id),
                "code": context.clarification.code,
                "intent": intent.kind.value,
            },
        )
        return AnalystOutcome(
            status=AnalystStatus.CLARIFICATION,
            question=question,
            intent=intent.kind,
            clarification=context.clarification,
            assumptions=context.assumptions,
            model=provider.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # --- 3. plan
    plan = planner.plan(intent, context)

    # --- 4. execute: one investigation, reused where the platform allows it
    if previous is not None and intent.kind in {
        IntentKind.INVESTIGATION_SUMMARY,
        IntentKind.FOLLOW_UP_ANALYSIS,
        IntentKind.DRILL_DOWN,
    }:
        # A follow-up about an investigation already in hand is a read. The tree
        # and every evidence record are persisted, so re-running the engine would
        # spend a DuckDB pass to arrive at identical numbers.
        investigation = previous
        created = False
    else:
        investigation, created = investigation_service.create(
            db,
            dataset.id,
            company_id,
            user_id=user_id,
            kpi_definition_id=context.kpi_definition_id,
            question=question,
            refresh=refresh,
        )

    anomaly = None
    if any(call.tool == TOOL_DETECT_ANOMALY for call in plan.calls):
        anomaly = _anomaly_payload(db, dataset.id, company_id, context.kpi_definition_id)

    tool_context = ToolContext(
        investigation=_investigation_payload(investigation),
        evidence=_evidence_payload(db, investigation),
        anomaly=anomaly,
        dimension=context.dimension,
        segment=context.segment,
    )
    steps, cap_limitations = executor.execute(plan, tool_context)

    # --- 5. ground
    bundle = grounding.build(
        question=question,
        context=context,
        investigation=tool_context.investigation,
        evidence=tool_context.evidence,
        results=steps,
        extra_limitations=cap_limitations,
    )

    # --- 6 and 7. explain, then check the prose invented nothing
    explanation = explain_module.explain(bundle, provider=provider)

    limitations = list(bundle.limitations) + list(explanation.limitations)
    failed_steps = [step for step in steps if not step.ok]
    # A step that ran and reported it could not answer what was asked - a segment
    # that is not in the tree, an anomaly detector that could not run. The analysis
    # is sound but the question is only partly answered, and saying COMPLETED here
    # would overstate it.
    unanswered_steps = [step for step in steps if step.ok and step.payload.get("found") is False]
    status = (
        AnalystStatus.PARTIAL
        if explanation.is_template or failed_steps or unanswered_steps or cap_limitations
        else AnalystStatus.COMPLETED
    )

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "ai_analyze_completed",
        extra={
            "dataset_id": str(dataset.id),
            "investigation_id": str(investigation.id),
            "investigation_created": created,
            "intent": intent.kind.value,
            "status": status.value,
            "steps": len(steps),
            "steps_failed": len(failed_steps),
            "answer_is_template": explanation.is_template,
            "model": provider.model,
            "duration_ms": duration_ms,
            "rules_version": ANALYST_RULES_VERSION,
        },
    )

    return AnalystOutcome(
        status=status,
        question=question,
        intent=intent.kind,
        answer=explanation.answer,
        answer_is_template=explanation.is_template,
        investigation_id=investigation.id,
        bundle=bundle,
        steps=steps,
        assumptions=bundle.assumptions,
        limitations=tuple(dict.fromkeys(limitations)),
        model=provider.model,
        duration_ms=duration_ms,
    )


def health() -> dict:
    """Whether the configured provider is reachable.

    A dict rather than an exception, following ``connectors.sqlserver`` - the AI
    page is meant to render "the model is down" and still offer the analysis.
    """
    if not settings.ai_enabled:
        return {
            "ok": False,
            "enabled": False,
            "provider": settings.ai_provider,
            "message": "The AI analyst is turned off on this deployment.",
            "error_code": "AI_DISABLED",
            "latency_ms": 0,
        }
    try:
        provider = get_llm_provider()
    except AppError as exc:
        return {
            "ok": False,
            "enabled": True,
            "provider": settings.ai_provider,
            "message": exc.message,
            "error_code": exc.code,
            "latency_ms": 0,
        }
    return {"enabled": True, "provider": settings.ai_provider, **provider.health()}
