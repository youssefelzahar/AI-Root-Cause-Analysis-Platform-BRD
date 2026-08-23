"""The Investigation Audit Trail: what happened, in order.

Events carry ``elapsed_ms`` - a monotonic offset from the run's start - rather
than a wall-clock timestamp. Two runs over the same data then produce the same
trail, which is what "reproducible" has to mean for an audit log. The service
turns the offset into a timestamp for display.
"""

from app.analysis.investigation.constants import STOP_REASON_SENTENCES
from app.analysis.investigation.evidence import number, percent, segment_label
from app.analysis.investigation.models import AuditEvent, Reconciliation
from app.analysis.rca.models import RcaResult
from app.db.models.enums import AuditEventType, EvidenceQuality, ReconciliationStatus


class AuditLog:
    """Accumulates events in order, numbering as it goes."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def add(
        self,
        event_type: AuditEventType,
        message: str,
        elapsed_ms: int,
        **details: object,
    ) -> AuditEvent:
        event = AuditEvent(
            sequence=len(self.events) + 1,
            event_type=event_type,
            message=message,
            elapsed_ms=elapsed_ms,
            details=dict(details),
        )
        self.events.append(event)
        return event

    def as_tuple(self) -> tuple[AuditEvent, ...]:
        return tuple(self.events)


def record_analysis(log: AuditLog, result: RcaResult, elapsed_ms: int) -> None:
    """The events derivable from a finished RCA result.

    Elapsed times are the run's total rather than per-step: the engine measures
    itself as a whole, and inventing plausible per-step splits would be exactly
    the kind of fabrication the evidence layer exists to prevent.
    """
    kpi = result.kpi
    if result.periods is not None:
        log.add(
            AuditEventType.PERIODS_RESOLVED,
            (
                f"Resolved {result.periods.previous.start:%Y-%m-%d} to "
                f"{result.periods.current.end:%Y-%m-%d} at {result.periods.grain.value} grain "
                f"using {result.periods.strategy}."
            ),
            elapsed_ms,
            grain=result.periods.grain.value,
            strategy=result.periods.strategy,
            anchor=result.periods.anchor.isoformat(),
        )

    log.add(
        AuditEventType.KPI_CALCULATED,
        (
            f"{kpi.name} calculated: {number(kpi.previous_value)} to "
            f"{number(kpi.current_value)} ({kpi.direction})."
        ),
        elapsed_ms,
        previous_value=kpi.previous_value,
        current_value=kpi.current_value,
        absolute_change=kpi.absolute_change,
        percent_change=kpi.percent_change,
        severity=kpi.severity,
    )

    if result.dimensions_analysed:
        names = [s.dimension for s in result.dimensions_analysed if not s.excluded_reason]
        log.add(
            AuditEventType.DIMENSION_ANALYSIS_EXECUTED,
            (
                f"Dimension analysis executed over {len(names)} dimension(s): "
                f"{', '.join(names) or 'none'}."
            ),
            elapsed_ms,
            dimensions=names,
            excluded={
                s.dimension: s.excluded_reason
                for s in result.dimensions_analysed
                if s.excluded_reason
            },
            explanatory_power={
                s.dimension: s.explanatory_power
                for s in result.dimensions_analysed
                if s.explanatory_power is not None
            },
        )

    for node in result.primary_drivers:
        share = (
            percent(node.contribution * 100, digits=0)
            if node.contribution is not None
            else "an unknown share"
        )
        log.add(
            AuditEventType.CONTRIBUTOR_SELECTED,
            (
                f"Selected {node.dimension} {segment_label(node)} as primary driver "
                f"(rank #{node.rank}, {share} of the movement)."
            ),
            elapsed_ms,
            node_id=node.node_id,
            dimension=node.dimension,
            contribution=node.contribution,
            rank=node.rank,
        )

    if result.tree is not None:
        depth, expanded, stopped = _tree_shape(result.tree)
        log.add(
            AuditEventType.DRILLDOWN_EXECUTED,
            (
                f"Drill-down executed to depth {depth}, expanding {expanded} node(s) "
                f"over {result.tree.child_dimension}."
            ),
            elapsed_ms,
            max_depth_reached=depth,
            nodes_expanded=expanded,
            root_dimension=result.tree.child_dimension,
        )
        for node_id, reason in stopped:
            log.add(
                AuditEventType.DRILLDOWN_STOPPED,
                (
                    f"Drill-down stopped at {node_id or 'the root'}: "
                    f"{STOP_REASON_SENTENCES.get(reason, reason.replace('_', ' '))}."
                ),
                elapsed_ms,
                node_id=node_id,
                stop_reason=reason,
            )


def record_evidence(log: AuditLog, count: int, elapsed_ms: int) -> None:
    log.add(
        AuditEventType.EVIDENCE_BUILT,
        f"Built {count} structured evidence record(s).",
        elapsed_ms,
        evidence_count=count,
    )


def record_reconciliation(log: AuditLog, reconciliation: Reconciliation, elapsed_ms: int) -> None:
    if reconciliation.status is ReconciliationStatus.NOT_APPLICABLE:
        # Not a pass and not a failure: there was no decomposition to reconcile.
        log.add(
            AuditEventType.RECONCILIATION_PASSED,
            "Contribution reconciliation not applicable: no decomposition was produced.",
            elapsed_ms,
            status=reconciliation.status.value,
        )
        return

    event = (
        AuditEventType.RECONCILIATION_PASSED
        if reconciliation.passed
        else AuditEventType.RECONCILIATION_FAILED
    )
    log.add(
        event,
        reconciliation.detail,
        elapsed_ms,
        status=reconciliation.status.value,
        contribution_sum=reconciliation.contribution_sum,
        tolerance=reconciliation.tolerance,
        tree_drift_status=reconciliation.tree_drift_status.value,
    )


def record_validation(log: AuditLog, verdict: EvidenceQuality, elapsed_ms: int, **counts) -> None:
    log.add(
        AuditEventType.EVIDENCE_VALIDATED,
        f"Evidence validated: {verdict.value.upper()}.",
        elapsed_ms,
        verdict=verdict.value,
        **counts,
    )


def _tree_shape(root) -> tuple[int, int, list[tuple[str, str]]]:
    """``(deepest depth, nodes expanded, [(node_id, stop_reason)])``."""
    deepest = 0
    expanded = 0
    stopped: list[tuple[str, str]] = []

    def walk(node) -> None:
        nonlocal deepest, expanded
        deepest = max(deepest, node.depth)
        if node.children:
            expanded += 1
        if node.stop_reason:
            stopped.append((node.node_id, node.stop_reason))
        for child in node.children:
            walk(child)

    walk(root)
    return deepest, expanded, stopped
