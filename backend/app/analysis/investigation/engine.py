"""The InvestigationEngine: orchestrates the existing engines, then evidences them.

Runs root cause analysis, optionally anomaly detection, then reconciles, builds
evidence, validates it, assembles the decision trace and the audit trail, and
links the tree to the evidence. It computes nothing analytical of its own - every
number comes from an engine that already existed.

Pure in the same sense as ``app.analysis.rca.engine``: it takes an open DuckDB
connection and imports no SQLAlchemy, no Pydantic and no storage. The service
above it is the only layer that can read the database.
"""

import time

from app.analysis.anomaly import detect_anomalies
from app.analysis.anomaly.models import AnomalyReport, AnomalySpec
from app.analysis.anomaly.series import grain_for
from app.analysis.investigation import audit as audit_module
from app.analysis.investigation import decisions as decisions_module
from app.analysis.investigation import evidence as evidence_module
from app.analysis.investigation import graph as graph_module
from app.analysis.investigation import validation as validation_module
from app.analysis.investigation.models import (
    InvestigationOutcome,
    InvestigationPlan,
)
from app.analysis.rca.engine import run_investigation
from app.analysis.rca.models import AnalysisState, RcaResult
from app.analysis.trace import Probe
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.models.enums import (
    AuditEventType,
    EvidenceQuality,
    InvestigationStatus,
    TreeDriftStatus,
)

logger = get_logger(__name__)


def investigate(
    conn,
    relation: str,
    plan: InvestigationPlan,
    *,
    probe: Probe | None = None,
) -> InvestigationOutcome:
    """Run one investigation end to end and return everything it produced."""
    started = time.perf_counter()
    probe = probe or Probe()
    log = audit_module.AuditLog()
    limitations: list[str] = []

    log.add(
        AuditEventType.INVESTIGATION_STARTED,
        f"Investigation started for {plan.spec.kpi_name}.",
        0,
        question=plan.question,
        kpi=plan.spec.kpi_name,
        comparison=plan.spec.comparison.value,
        max_tree_depth=plan.spec.max_tree_depth,
    )

    result = run_investigation(conn, relation, plan.spec, probe=probe)
    rca_elapsed = _elapsed(started)
    # Where the root-cause pass ended in the shared trace. Anomaly detection
    # appends to the same tracer, so this is the only way to tell which
    # statements RcaResult's own count refers to.
    rca_statements = probe.queries.count
    audit_module.record_analysis(log, result, rca_elapsed)

    anomaly_report = _detect(
        conn, relation, plan, probe, log, limitations, result, started=started
    )

    # --- reconcile, then evidence, then validate. In that order, because the
    # reconciliation verdict is quoted by the execution record, and the six
    # validation records report on the set the builder produced.
    reconciliation = validation_module.reconcile(
        result, tolerance=plan.reconciliation_tolerance
    )
    audit_module.record_reconciliation(log, reconciliation, _elapsed(started))

    records = evidence_module.build(
        plan,
        result,
        probe.queries,
        reconciliation,
        anomaly=anomaly_report,
        execution_time_ms=_elapsed(started),
    )
    audit_module.record_evidence(log, len(records), _elapsed(started))

    quality = validation_module.assess(
        plan,
        result,
        records,
        probe.queries,
        reconciliation,
        rca_statements=rca_statements,
    )
    records = validation_module.apply_verdict(records, quality)
    records += evidence_module.build_validation_records(
        plan, result, quality, start_sequence=len(records) + 1
    )
    audit_module.record_validation(
        log,
        quality.verdict,
        _elapsed(started),
        evidence_count=len(records),
        checks_passed=sum(1 for c in quality.checks if c.status.value == "passed"),
    )

    decisions_module.record_all(probe, result, max_tree_depth=plan.spec.max_tree_depth)
    decisions = graph_module.stamp_decisions(list(probe.decisions), records)
    tree = graph_module.build(result.tree, records, decisions)

    limitations.extend(_analytical_limitations(result, reconciliation, quality))
    status = _status(result, quality, reconciliation, limitations)

    log.add(
        (
            AuditEventType.INVESTIGATION_PARTIAL
            if status is InvestigationStatus.PARTIAL
            else AuditEventType.INVESTIGATION_COMPLETED
        ),
        (
            f"Investigation {status.value} with {len(records)} evidence record(s); "
            f"evidence quality {quality.verdict.value.upper()}."
        ),
        _elapsed(started),
        status=status.value,
        evidence_quality=quality.verdict.value,
        limitations=list(limitations),
    )

    return InvestigationOutcome(
        status=status,
        result=result,
        evidence=tuple(records),
        decisions=tuple(decisions),
        audit=log.as_tuple(),
        queries=tuple(probe.queries.records),
        quality=quality,
        reconciliation=reconciliation,
        tree=tree,
        limitations=tuple(limitations),
        notices=result.notices,
        anomaly_summary=anomaly_report.summary if anomaly_report else None,
    )


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _detect(
    conn,
    relation: str,
    plan: InvestigationPlan,
    probe: Probe,
    log: audit_module.AuditLog,
    limitations: list[str],
    result: RcaResult,
    *,
    started: float,
) -> AnomalyReport | None:
    """Anomaly detection as a bounded, non-fatal extra step.

    It fills the anomaly and trend evidence types, and it runs on the same
    connection and relation as the RCA: the two engines project into differently
    named temp tables, so there is no collision.

    A failure here is a limitation, never an error. The root cause answer is
    complete without it, and turning a usable investigation into a 500 because an
    optional step could not run would be the wrong trade.
    """
    if not plan.run_anomaly_detection or not plan.spec.time_column:
        return None
    if result.state in {AnalysisState.NO_DATA, AnalysisState.NO_TIME_COLUMN}:
        return None

    grain = grain_for(plan.spec.detected_frequency, None)
    if grain is None:
        log.add(
            AuditEventType.ANOMALY_DETECTION_SKIPPED,
            "Anomaly detection skipped: no reporting grain a continuous series can be built on.",
            _elapsed(started),
            reason="grain_unavailable",
        )
        limitations.append(
            "Anomaly detection was skipped because this dataset has no detectable reporting "
            "frequency, so no anomaly or trend evidence is included."
        )
        return None

    spec = AnomalySpec(
        kpi_name=plan.spec.kpi_name,
        measure_column=plan.spec.measure_column,
        aggregation=plan.spec.aggregation,
        time_column=plan.spec.time_column,
        grain=grain,
        filters=plan.spec.filters,
        detected_frequency=plan.spec.detected_frequency,
    )
    try:
        report = detect_anomalies(conn, relation, spec, probe=probe)
    except AppError as exc:
        # A realistic outcome, not a bug: an unsupported grain or a
        # non-decomposable measure is a property of the data.
        log.add(
            AuditEventType.ANOMALY_DETECTION_SKIPPED,
            f"Anomaly detection skipped: {exc.message}",
            _elapsed(started),
            reason=exc.code,
        )
        limitations.append(
            f"Anomaly detection could not run ({exc.code}), so no anomaly or trend evidence is "
            "included. The contribution analysis is unaffected."
        )
        logger.info(
            "investigation_anomaly_skipped",
            extra={"code": exc.code, "kpi": plan.spec.kpi_name},
        )
        return None

    log.add(
        AuditEventType.ANOMALY_DETECTION_EXECUTED,
        (
            f"Anomaly detection executed at {report.grain.value} grain over "
            f"{len(report.series)} period(s); {len(report.anomalies)} anomalous."
        ),
        _elapsed(started),
        grain=report.grain.value,
        method=report.method,
        periods=len(report.series),
        anomalies=len(report.anomalies),
    )
    return report


def _analytical_limitations(
    result: RcaResult,
    reconciliation,
    quality,
) -> list[str]:
    """What this investigation could not do, in the reader's terms."""
    limitations: list[str] = []

    for summary in result.dimensions_analysed:
        if summary.excluded_reason:
            limitations.append(
                f"Dimension {summary.dimension!r} was excluded ({summary.excluded_reason}), so it "
                "contributed no drivers."
            )
        elif summary.truncated:
            limitations.append(
                f"Dimension {summary.dimension!r} has more distinct values than can be listed, so "
                "the remainder is grouped and its individual segments are not reported."
            )

    if reconciliation.tree_drift_status is TreeDriftStatus.DRIFT_UNEXPLAINED:
        limitations.append(
            "Some drill-down nodes do not sum to their parent for any legitimate reason, so the "
            "hierarchy should be treated as provisional."
        )
    if quality.verdict is EvidenceQuality.FAILED:
        limitations.append(
            "Evidence quality checks failed, so the findings are reported but not validated."
        )
    limitations.extend(quality.caveats)
    return limitations


def _status(
    result: RcaResult,
    quality,
    reconciliation,
    limitations: list[str],
) -> InvestigationStatus:
    """COMPLETED unless a planned step was skipped or degraded.

    A terminal analysis state is not a failure: no data, no previous period, no
    change and unattributable are all *results*, correctly reached. PARTIAL means
    "trust what is here, and here is what is missing".
    """
    degraded = (
        bool(limitations)
        or quality.verdict is EvidenceQuality.FAILED
        or reconciliation.tree_drift_status is TreeDriftStatus.DRIFT_UNEXPLAINED
    )
    return InvestigationStatus.PARTIAL if degraded else InvestigationStatus.COMPLETED
