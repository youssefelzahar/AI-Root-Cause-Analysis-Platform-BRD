"""The Evidence Builder: one structured, checkable claim per important finding.

Reads what the engines already computed and gives each finding an id, a sentence,
and the provenance to verify it. It computes no new numbers - if a value is not
already on an engine result, it does not belong in evidence.

Two rules run through the whole module:

* **Never fabricate SQL.** A record whose numbers came from a statement carries
  that statement verbatim; a derived record carries ``None``. There is no third
  option, and no "representative" query.
* **A contribution is not a cause.** Claims say contributed, moved, accounts for.
  Nothing here says caused.
"""

from typing import Any

from app.analysis.anomaly.models import AnomalyReport, Observation, ScaleBasis
from app.analysis.anomaly.models import Severity as AnomalySeverity
from app.analysis.investigation.constants import (
    CLASSIFICATION_LABELS,
    DIRECTION_VERBS,
    MAX_UNEXPLAINED_FOR_HIGH_CONFIDENCE,
    NULL_VALUE_LABEL,
    STOP_REASON_CATEGORIES,
    STOP_REASON_SENTENCES,
    TOOL_ANOMALY,
    TOOL_CONTRIBUTION,
    TOOL_EXECUTION,
    TOOL_PERIODS,
    TOOL_TREE,
    TOOL_VALIDATION,
)
from app.analysis.investigation.models import (
    EvidenceQualitySummary,
    EvidenceRecord,
    InvestigationPlan,
    Reconciliation,
    evidence_id,
)
from app.analysis.rca.models import (
    AttributionBasis,
    ChangePattern,
    Classification,
    DimensionSummary,
    DriverNode,
    Period,
    RcaResult,
)
from app.analysis.trace import Purpose, QueryRecord, QueryTracer
from app.db.models.enums import (
    EvidenceConfidence,
    EvidenceType,
    EvidenceValidationStatus,
    QualityCheckStatus,
)

# --- formatting ---------------------------------------------------------------


def trim_zeros(text: str) -> str:
    """Drop trailing zeros, but only after a decimal point.

    Stripping unconditionally turns "80" into "8" and "100" into "1", which is
    how a percentage silently becomes an order of magnitude wrong.
    """
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


def number(value: float | None) -> str:
    """A number as a reader would write it, not as a float repr.

    65.0 is "65", not "65.0"; 23.0769 is "23.08". Claims are read by people, and
    a spurious decimal makes an exact figure look approximate.
    """
    if value is None:
        return "unknown"
    if abs(value - round(value)) < 1e-9:
        return f"{round(value):,}"
    return trim_zeros(f"{value:,.2f}")


def percent(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return "unknown"
    return f"{trim_zeros(f'{value:,.{digits}f}')}%"


def signed(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"+{number(value)}" if value > 0 else number(value)


def period_label(period: Period | None) -> str | None:
    """A half-open window, written so the exclusive end is visible."""
    if period is None:
        return None
    return f"{period.start:%Y-%m-%d} to {period.end:%Y-%m-%d} (exclusive)"


def segment_label(node: DriverNode) -> str:
    if node.value_is_null:
        return NULL_VALUE_LABEL
    return node.value if node.value is not None else NULL_VALUE_LABEL


# --- confidence ---------------------------------------------------------------


def confidence_for(
    node: DriverNode,
    *,
    basis: AttributionBasis,
    truncated: bool,
    pattern: ChangePattern,
    parent_unexplained: float | None = None,
) -> EvidenceConfidence:
    """How much weight a node's number deserves.

    A deterministic ladder rather than a score: every rung is a condition the
    engine already recorded, so the rating is explainable and reproducible.
    """
    if (
        node.low_support
        or node.is_other_bucket
        or node.contribution is None
        or basis is AttributionBasis.GROSS_MOVEMENT
    ):
        return EvidenceConfidence.LOW
    if (
        truncated
        or pattern is ChangePattern.BROAD_BASED
        or (
            parent_unexplained is not None
            and abs(parent_unexplained) > MAX_UNEXPLAINED_FOR_HIGH_CONFIDENCE
        )
    ):
        return EvidenceConfidence.MEDIUM
    return EvidenceConfidence.HIGH


# --- the builder --------------------------------------------------------------


class _Builder:
    """Accumulates records in a stable order, numbering as it goes."""

    def __init__(self, plan: InvestigationPlan, result: RcaResult, queries: QueryTracer) -> None:
        self.plan = plan
        self.result = result
        self.queries = queries
        self.records: list[EvidenceRecord] = []
        self._summaries = {s.dimension: s for s in result.dimensions_analysed}

    # -- provenance stamped on every record
    def _source_columns(self, node: DriverNode | None = None) -> tuple[str, ...]:
        prov = self.plan.provenance
        columns = [prov.measure_column]
        if prov.time_column:
            columns.append(prov.time_column)
        if node is not None:
            for dimension, _ in node.path:
                if dimension not in columns:
                    columns.append(dimension)
            if node.dimension and node.dimension not in columns:
                columns.append(node.dimension)
        return tuple(columns)

    def _query(self, record: QueryRecord | None) -> tuple[str | None, int | None]:
        if record is None:
            return None, None
        return record.sql, record.sequence

    def _node_query(self, node: DriverNode) -> QueryRecord | None:
        """The statement that measured this node.

        Depth 1 came out of the single all-dimensions breakdown. Deeper nodes came
        out of the drill statement that expanded their *parent*, which is the one
        whose ``node_id`` is the parent's.
        """
        if node.depth <= 1:
            return self.queries.find(Purpose.DIMENSION_BREAKDOWN)
        parent_id = _parent_node_id(node)
        return self.queries.find(Purpose.DRILLDOWN_BREAKDOWN, node_id=parent_id)

    def add(
        self,
        evidence_type: EvidenceType,
        key: str,
        claim: str,
        analysis_tool: str,
        *,
        node: DriverNode | None = None,
        query: QueryRecord | None = None,
        **fields: Any,
    ) -> EvidenceRecord:
        prov = self.plan.provenance
        sql, sequence = self._query(query)
        record = EvidenceRecord(
            id=evidence_id(self.plan.investigation_id, evidence_type, key),
            sequence=len(self.records) + 1,
            evidence_type=evidence_type,
            claim=claim,
            analysis_tool=analysis_tool,
            metric=fields.pop("metric", self.result.kpi.name),
            source_dataset=prov.dataset_name,
            source_relation=prov.source_relation,
            source_columns=fields.pop("source_columns", self._source_columns(node)),
            filters=prov.filters,
            query=sql,
            query_sequence=sequence,
            node_id=node.node_id if node is not None else fields.pop("node_id", None),
            depth=node.depth if node is not None else fields.pop("depth", None),
            **fields,
        )
        self.records.append(record)
        return record

    # -- per-type builders

    def kpi_change(self) -> None:
        kpi = self.result.kpi
        periods = self.result.periods
        verb = DIRECTION_VERBS.get(kpi.direction, "changed")
        previous = period_label(periods.previous) if periods else None
        current = period_label(periods.current) if periods else None

        if kpi.absolute_change is None:
            claim = f"{kpi.name} could not be compared across the two periods."
        elif kpi.direction == "flat":
            claim = f"{kpi.name} did not change between the two periods."
        else:
            change = f"{signed(kpi.absolute_change)}"
            if kpi.percent_change is not None:
                change += f" ({percent(kpi.percent_change)})"
            claim = (
                f"{kpi.name} {verb} from {number(kpi.previous_value)} to "
                f"{number(kpi.current_value)}: {change}."
            )

        self.add(
            EvidenceType.KPI_CHANGE,
            "root",
            claim,
            TOOL_PERIODS,
            query=self.queries.find(Purpose.KPI_PERIOD_TOTALS),
            previous_period=previous,
            current_period=current,
            previous_value=kpi.previous_value,
            current_value=kpi.current_value,
            absolute_change=kpi.absolute_change,
            percentage_change=kpi.percent_change,
            # No contribution: the KPI *is* the whole movement, and 100% here
            # would be a circular claim rather than a finding.
            confidence=EvidenceConfidence.HIGH,
            details={
                "direction": kpi.direction,
                "severity": kpi.severity,
                "aggregation": kpi.aggregation,
                "comparison": kpi.comparison,
                "grain": kpi.grain,
                "percent_change_undefined_reason": kpi.percent_change_undefined_reason,
            },
        )

    def comparison(self) -> None:
        periods = self.result.periods
        if periods is None:
            return
        evidence = self.result.evidence
        claim = (
            f"Compared {period_label(periods.current)} ({number(periods.current.row_count)} rows) "
            f"against {period_label(periods.previous)} "
            f"({number(periods.previous.row_count)} rows) at {periods.grain.value} grain, "
            f"anchored on the dataset's latest timestamp {periods.anchor:%Y-%m-%d} rather than the "
            f"wall clock. {number(evidence.rows_outside_periods)} rows fall outside both windows."
        )
        if periods.excluded_partial_period is not None:
            claim += (
                f" The most recent period "
                f"({period_label(periods.excluded_partial_period)}) was incomplete and excluded."
            )
        self.add(
            EvidenceType.COMPARISON,
            "periods",
            claim,
            TOOL_PERIODS,
            query=self.queries.find(Purpose.RESOLVE_PERIOD_BOUNDS),
            previous_period=period_label(periods.previous),
            current_period=period_label(periods.current),
            confidence=EvidenceConfidence.HIGH,
            details={
                "grain": periods.grain.value,
                "strategy": periods.strategy,
                "anchor": periods.anchor.isoformat(),
                "previous_rows": periods.previous.row_count,
                "current_rows": periods.current.row_count,
                "rows_outside_periods": evidence.rows_outside_periods,
                "excluded_partial_period": (
                    period_label(periods.excluded_partial_period)
                    if periods.excluded_partial_period
                    else None
                ),
            },
        )

    def dimension_changes(self) -> None:
        """Movement per segment, at depth 1.

        Only material segments and the residual bucket: ``ranking.classify`` has
        already decided what is worth naming, and a dimension with fifty values
        would otherwise produce fifty records saying nothing. The suppressed count
        is reported in the coverage record, so nothing goes missing quietly.
        """
        basis = self.result.attribution.basis
        pattern = self.result.attribution.change_pattern
        for dimension, nodes in self.result.dimension_results:
            summary = self._summaries.get(dimension)
            truncated = bool(summary and summary.truncated)
            for node in nodes:
                if not _is_material(node):
                    continue
                query = self._node_query(node)
                label = segment_label(node)
                if node.absolute_change is None:
                    claim = f"{dimension} {label} could not be compared across the two periods."
                else:
                    tail = (
                        f" ({percent(node.percent_change)})"
                        if node.percent_change is not None
                        else ""
                    )
                    claim = (
                        f"{dimension} {label} moved from {number(node.previous_value)} to "
                        f"{number(node.current_value)}: {signed(node.absolute_change)}{tail}."
                    )
                self.add(
                    EvidenceType.DIMENSION_CHANGE,
                    node.node_id,
                    claim,
                    TOOL_CONTRIBUTION,
                    node=node,
                    query=query,
                    dimension=dimension,
                    dimension_value=node.value,
                    dimension_value_is_null=node.value_is_null,
                    previous_value=node.previous_value,
                    current_value=node.current_value,
                    absolute_change=node.absolute_change,
                    percentage_change=node.percent_change,
                    # Deliberately absent: this record states the movement.
                    # The share of the total is a separate contribution record,
                    # so the two can never be read as one number.
                    classification=node.classification.value,
                    rank=node.rank,
                    confidence=confidence_for(
                        node, basis=basis, truncated=truncated, pattern=pattern
                    ),
                    details={
                        "is_new_segment": node.is_new_segment,
                        "is_lost_segment": node.is_lost_segment,
                        "low_support": node.low_support,
                        "support_reason": node.support_reason,
                        "is_other_bucket": node.is_other_bucket,
                        "current_rows": node.current_rows,
                        "previous_rows": node.previous_rows,
                        "percent_change_undefined_reason": node.percent_change_undefined_reason,
                    },
                )

    def contributions(self) -> None:
        basis = self.result.attribution.basis
        pattern = self.result.attribution.change_pattern
        total = self.result.kpi.absolute_change
        for dimension, nodes in self.result.dimension_results:
            summary = self._summaries.get(dimension)
            truncated = bool(summary and summary.truncated)
            for node in nodes:
                if node.contribution is None or not _is_material(node):
                    continue
                share = node.contribution * 100.0
                role = CLASSIFICATION_LABELS.get(node.classification.value, "contributor")
                rank = f", rank #{node.rank}" if node.rank else ""
                claim = (
                    f"{dimension} {segment_label(node)} contributed {percent(share, digits=0)} "
                    f"of the {signed(total)} movement in {self.result.kpi.name} "
                    f"({role}{rank}, {basis.value} basis)."
                )
                self.add(
                    EvidenceType.CONTRIBUTION,
                    node.node_id,
                    claim,
                    TOOL_CONTRIBUTION,
                    node=node,
                    query=self._node_query(node),
                    dimension=dimension,
                    dimension_value=node.value,
                    dimension_value_is_null=node.value_is_null,
                    previous_value=node.previous_value,
                    current_value=node.current_value,
                    absolute_change=node.absolute_change,
                    contribution_percentage=share,
                    classification=node.classification.value,
                    rank=node.rank,
                    confidence=confidence_for(
                        node, basis=basis, truncated=truncated, pattern=pattern
                    ),
                    details={
                        "basis": node.contribution_basis.value,
                        "expected_change": node.expected_change,
                        "excess_change": node.excess_change,
                        "share_of_parent_change": node.share_of_parent_change,
                        "rate_effect": node.rate_effect,
                        "mix_effect": node.mix_effect,
                        "previous_share": node.previous_share,
                        "current_share": node.current_share,
                    },
                )

    def drill_downs(self) -> None:
        tree = self.result.tree
        if tree is None:
            return
        basis = self.result.attribution.basis
        pattern = self.result.attribution.change_pattern
        total = self.result.kpi.absolute_change

        def walk(node: DriverNode, parent: DriverNode | None) -> None:
            if node.depth >= 2 and parent is not None:
                share = node.contribution * 100.0 if node.contribution is not None else None
                local = (
                    node.share_of_parent_change * 100.0
                    if node.share_of_parent_change is not None
                    else None
                )
                within = (
                    f"Within {parent.dimension} = {segment_label(parent)}, "
                    if parent.dimension
                    else ""
                )
                pieces = []
                if share is not None:
                    pieces.append(f"{percent(share, digits=0)} of the total movement")
                if local is not None:
                    pieces.append(f"{percent(local, digits=0)} of its parent's")
                tail = f" ({', '.join(pieces)})" if pieces else ""
                claim = (
                    f"{within}{node.dimension} = {segment_label(node)} accounts for "
                    f"{signed(node.absolute_change)}{tail}."
                )
                self.add(
                    EvidenceType.DRILL_DOWN,
                    node.node_id,
                    claim,
                    TOOL_TREE,
                    node=node,
                    query=self._node_query(node),
                    dimension=node.dimension,
                    dimension_value=node.value,
                    dimension_value_is_null=node.value_is_null,
                    previous_value=node.previous_value,
                    current_value=node.current_value,
                    absolute_change=node.absolute_change,
                    percentage_change=node.percent_change,
                    contribution_percentage=share,
                    explanatory_power=node.child_explanatory_power,
                    classification=node.classification.value,
                    rank=node.rank,
                    confidence=confidence_for(
                        node,
                        basis=basis,
                        truncated=False,
                        pattern=pattern,
                        parent_unexplained=parent.unexplained_share,
                    ),
                    details={
                        "path": [{"dimension": d, "value": v} for d, v in node.path],
                        "share_of_parent_change": node.share_of_parent_change,
                        "is_pure_split": node.is_pure_split,
                        "child_dimension": node.child_dimension,
                        "child_split_type": node.child_split_type,
                        "child_explanatory_power": node.child_explanatory_power,
                        "unexplained_share": node.unexplained_share,
                        "total_change": total,
                    },
                )

            if node.stop_reason and node.depth >= 1:
                sentence = STOP_REASON_SENTENCES.get(
                    node.stop_reason, node.stop_reason.replace("_", " ")
                )
                claim = (
                    f"Drill-down stopped at {node.dimension} = {segment_label(node)}: "
                    f"{sentence}."
                )
                self.add(
                    EvidenceType.DRILL_DOWN,
                    f"{node.node_id}#stop",
                    claim,
                    TOOL_TREE,
                    node=node,
                    # Derived from the tree's own state, not measured by a
                    # statement, so no query is claimed and none is expected.
                    derived=True,
                    dimension=node.dimension,
                    dimension_value=node.value,
                    dimension_value_is_null=node.value_is_null,
                    contribution_percentage=(
                        node.contribution * 100.0 if node.contribution is not None else None
                    ),
                    classification=node.classification.value,
                    rank=node.rank,
                    confidence=EvidenceConfidence.HIGH,
                    details={
                        "stop_reason": node.stop_reason,
                        "stop_reason_category": STOP_REASON_CATEGORIES.get(node.stop_reason),
                        "depth": node.depth,
                        "max_tree_depth": self.plan.spec.max_tree_depth,
                    },
                )

            for child in node.children:
                walk(child, node)

        walk(tree, None)

    def segment_lifecycle(self) -> None:
        """NEW and GONE as first-class records.

        Phrased on rows, because that is how the engine defines them: a segment
        present but measuring zero is a different finding from one with no rows at
        all. And deliberately carrying no percentage change - the node itself has
        a mechanical -100%, which would read as a real collapse rather than an
        absence.
        """
        seen: set[str] = set()
        for dimension, nodes in self.result.dimension_results:
            for node in nodes:
                if node.node_id in seen:
                    continue
                if not (node.is_new_segment or node.is_lost_segment):
                    continue
                seen.add(node.node_id)
                gone = node.is_lost_segment
                evidence_type = (
                    EvidenceType.GONE_SEGMENT if gone else EvidenceType.NEW_SEGMENT
                )
                label = segment_label(node)
                if gone:
                    claim = (
                        f"{dimension} {label} is GONE: {number(node.previous_rows)} rows and a "
                        f"value of {number(node.previous_value)} in the previous period, no rows "
                        f"at all in the current period."
                    )
                else:
                    claim = (
                        f"{dimension} {label} is NEW: no rows at all in the previous period, "
                        f"{number(node.current_rows)} rows and a value of "
                        f"{number(node.current_value)} in the current period."
                    )
                self.add(
                    evidence_type,
                    node.node_id,
                    claim,
                    TOOL_CONTRIBUTION,
                    node=node,
                    query=self._node_query(node),
                    dimension=dimension,
                    dimension_value=node.value,
                    dimension_value_is_null=node.value_is_null,
                    previous_value=node.previous_value,
                    current_value=node.current_value,
                    absolute_change=node.absolute_change,
                    # No percentage_change on purpose - see the docstring.
                    percentage_change=None,
                    contribution_percentage=(
                        node.contribution * 100.0 if node.contribution is not None else None
                    ),
                    classification=node.classification.value,
                    rank=node.rank,
                    confidence=EvidenceConfidence.HIGH,
                    details={
                        "percent_change_undefined_reason": "segment_absent",
                        "previous_rows": node.previous_rows,
                        "current_rows": node.current_rows,
                    },
                )

    def offsetting(self) -> None:
        total = self.result.kpi.absolute_change
        direction = "decrease" if (total or 0) < 0 else "increase"
        for node in self.result.offsetting_factors:
            share = node.contribution * 100.0 if node.contribution is not None else None
            magnitude = percent(abs(share), digits=0) if share is not None else "an unknown share"
            claim = (
                f"{node.dimension} {segment_label(node)} moved against the KPI, offsetting "
                f"{magnitude} of the {direction} ({signed(node.absolute_change)} while "
                f"{self.result.kpi.name} moved {signed(total)})."
            )
            self.add(
                EvidenceType.OFFSETTING_FACTOR,
                node.node_id,
                claim,
                TOOL_CONTRIBUTION,
                node=node,
                query=self._node_query(node),
                dimension=node.dimension,
                dimension_value=node.value,
                dimension_value_is_null=node.value_is_null,
                previous_value=node.previous_value,
                current_value=node.current_value,
                absolute_change=node.absolute_change,
                percentage_change=node.percent_change,
                contribution_percentage=share,
                classification=node.classification.value,
                rank=node.rank,
                confidence=confidence_for(
                    node,
                    basis=self.result.attribution.basis,
                    truncated=False,
                    pattern=self.result.attribution.change_pattern,
                ),
                details={"offset_direction": direction},
            )

    def execution(self, reconciliation: Reconciliation, *, execution_time_ms: int) -> None:
        """The run's own cost, promoted from a UI stat into formal evidence.

        ``execution_time_ms`` is the whole investigation's wall clock, passed in
        rather than read off ``RcaResult``: the result only knows how long the
        root-cause pass took, and an investigation that also ran anomaly
        detection cost more than that.
        """
        evidence = self.result.evidence
        payload = {
            "rows_scanned": evidence.total_rows,
            "rows_in_previous_period": evidence.previous_rows,
            "rows_in_current_period": evidence.current_rows,
            "rows_outside_periods": evidence.rows_outside_periods,
            "queries_executed": self.queries.count,
            "execution_time_ms": execution_time_ms,
            "contribution_reconciliation": reconciliation.status.value.upper(),
        }
        claim = (
            f"{number(evidence.total_rows)} rows scanned across "
            f"{number(self.queries.count)} queries in {number(execution_time_ms)} ms; "
            f"contribution reconciliation {reconciliation.status.value.upper()}."
        )
        self.add(
            EvidenceType.EXECUTION,
            "run",
            claim,
            TOOL_EXECUTION,
            confidence=EvidenceConfidence.HIGH,
            details={
                **payload,
                "query_sequences": [r.sequence for r in self.queries.records],
                "duckdb_time_ms": self.queries.duration_ms,
                "root_cause_time_ms": evidence.duration_ms,
            },
        )

    def coverage(self, suppressed: int, measured: int) -> None:
        evidence = self.result.evidence
        compared = evidence.previous_rows + evidence.current_rows
        share = (compared / evidence.total_rows * 100.0) if evidence.total_rows else None
        summaries: list[DimensionSummary] = list(self.result.dimensions_analysed)
        truncated = [s.dimension for s in summaries if s.truncated]
        excluded = {s.dimension: s.excluded_reason for s in summaries if s.excluded_reason}

        claim = (
            f"{number(compared)} of {number(evidence.total_rows)} rows fall inside the two "
            f"compared windows"
        )
        if share is not None:
            claim += f" ({percent(share, digits=2)})"
        claim += (
            f". {number(evidence.unparsed_time_rows)} rows have an unreadable date and "
            f"{number(evidence.unparsed_measure_rows)} an unreadable measure. "
            f"{len(summaries)} dimensions analysed, {len(truncated)} truncated, "
            f"{len(excluded)} excluded. {number(measured)} segments were measured; "
            f"{number(measured - suppressed)} are reported as evidence and "
            f"{number(suppressed)} fall below the materiality floor."
        )
        self.add(
            EvidenceType.COVERAGE,
            "run",
            claim,
            TOOL_EXECUTION,
            confidence=EvidenceConfidence.HIGH,
            details={
                "rows_scanned": evidence.total_rows,
                "rows_compared": compared,
                "rows_outside_periods": evidence.rows_outside_periods,
                "unparsed_time_rows": evidence.unparsed_time_rows,
                "unparsed_measure_rows": evidence.unparsed_measure_rows,
                "segments_measured": measured,
                "segments_suppressed": suppressed,
                "truncated_dimensions": truncated,
                "excluded_dimensions": excluded,
                # Explainability per dimension: kept here, apart from any
                # contribution number, because it is not a share of anything.
                "dimension_explainability": {
                    s.dimension: s.explanatory_power
                    for s in summaries
                    if s.explanatory_power is not None
                },
            },
        )

    def reconciliation(self, reconciliation: Reconciliation) -> None:
        self.add(
            EvidenceType.RECONCILIATION,
            "run",
            reconciliation.detail,
            TOOL_VALIDATION,
            confidence=EvidenceConfidence.HIGH,
            contribution_percentage=(
                reconciliation.contribution_sum * 100.0
                if reconciliation.contribution_sum is not None
                else None
            ),
            details={
                "status": reconciliation.status.value,
                "contribution_sum": reconciliation.contribution_sum,
                "tolerance": reconciliation.tolerance,
                "basis": reconciliation.basis,
                "tree_drift_status": reconciliation.tree_drift_status.value,
                "drifting_nodes": list(reconciliation.drifting_nodes),
            },
        )

    def anomalies(self, report: AnomalyReport) -> None:
        """Anomalous periods that bear on this change, plus any trend notice.

        Filtered to observations inside the two compared windows: an anomaly two
        years ago is real but is not evidence about *this* movement.
        """
        periods = self.result.periods
        for observation in report.anomalies:
            if periods is not None and not _within(observation, periods):
                continue
            baseline = observation.baseline
            expected = baseline.expected_value if baseline else None
            claim = (
                f"The {observation.period_start:%Y-%m-%d} period value of "
                f"{number(observation.value)} is {number(observation.anomaly_score)} "
                f"deviations {observation.direction.value.lower()} its trailing baseline of "
                f"{number(expected)} (severity {observation.severity.value.upper()}, "
                f"{report.method} over {baseline.observations_used if baseline else 0} periods)."
            )
            self.add(
                EvidenceType.ANOMALY,
                observation.period_start.isoformat(),
                claim,
                TOOL_ANOMALY,
                query=self.queries.find(Purpose.SERIES_AGGREGATE),
                previous_value=expected,
                current_value=observation.value,
                absolute_change=observation.absolute_deviation,
                percentage_change=observation.percentage_deviation,
                current_period=f"{observation.period_start:%Y-%m-%d}",
                confidence=_anomaly_confidence(observation),
                source_columns=self._source_columns(),
                details={
                    "anomaly_score": observation.anomaly_score,
                    "severity": observation.severity.value,
                    "direction": observation.direction.value,
                    "method": report.method,
                    "grain": report.grain.value,
                    "scale_basis": baseline.scale_basis.value if baseline else None,
                    "observations_used": baseline.observations_used if baseline else None,
                    "row_count": observation.row_count,
                },
            )

        # A trend record only when the detector actually said the baseline is
        # drifting. An absent finding is not a finding, so nothing is emitted to
        # fill the type in.
        for notice in report.notices:
            if notice.code != "TRENDING_BASELINE":
                continue
            drift = (notice.details or {}).get("drift")
            claim = (
                f"The baseline for {self.result.kpi.name} is trending rather than stable"
                + (f" (drift ratio {number(drift)})" if drift is not None else "")
                + ", so each period is judged against a baseline that lags the series."
            )
            self.add(
                EvidenceType.TREND,
                "baseline",
                claim,
                TOOL_ANOMALY,
                confidence=EvidenceConfidence.MEDIUM,
                source_columns=self._source_columns(),
                details={"drift": drift, "notice": notice.code, "message": notice.message},
            )


# --- helpers ------------------------------------------------------------------


def _is_material(node: DriverNode) -> bool:
    """Whether a segment is worth a record of its own.

    ``ranking.classify`` has already made this judgement; reusing it keeps the
    evidence set and the driver tables in agreement.
    """
    return node.rank >= 1 or node.classification is not Classification.IMMATERIAL


def _parent_node_id(node: DriverNode) -> str:
    """The node_id of this node's parent, from its own path.

    ``node_id`` is built by joining the path, so the parent's is the same join
    with the last step dropped. Derived rather than threaded through, because the
    tree does not carry parent pointers.
    """
    return "|".join(f"{dim}={value}" for dim, value in node.path[:-1])


def _within(observation: Observation, periods: Any) -> bool:
    start = observation.period_start
    return (
        periods.previous.start <= start < periods.previous.end
        or periods.current.start <= start < periods.current.end
    )


def _anomaly_confidence(observation: Observation) -> EvidenceConfidence:
    baseline = observation.baseline
    if baseline is not None and baseline.scale_basis is ScaleBasis.DEGENERATE:
        # A degenerate scale means the baseline barely varied, so the score is
        # arithmetically large but says little.
        return EvidenceConfidence.LOW
    if observation.severity in {AnomalySeverity.CRITICAL, AnomalySeverity.HIGH}:
        return EvidenceConfidence.HIGH
    if observation.severity is AnomalySeverity.MEDIUM:
        return EvidenceConfidence.MEDIUM
    return EvidenceConfidence.LOW


def count_segments(result: RcaResult) -> tuple[int, int]:
    """``(measured, suppressed)`` segment counts, for the coverage record."""
    measured = sum(len(nodes) for _, nodes in result.dimension_results)
    suppressed = sum(
        1 for _, nodes in result.dimension_results for node in nodes if not _is_material(node)
    )
    return measured, suppressed


# --- entry points -------------------------------------------------------------


def build(
    plan: InvestigationPlan,
    result: RcaResult,
    queries: QueryTracer,
    reconciliation: Reconciliation,
    *,
    anomaly: AnomalyReport | None = None,
    execution_time_ms: int | None = None,
) -> list[EvidenceRecord]:
    """Every record except the six validation ones.

    Validation records come last, from ``build_validation_records``, because they
    report on the set built here - which cannot include them without becoming
    self-referential.
    """
    builder = _Builder(plan, result, queries)

    builder.kpi_change()
    builder.comparison()
    builder.dimension_changes()
    builder.contributions()
    builder.segment_lifecycle()
    builder.offsetting()
    builder.drill_downs()
    if anomaly is not None:
        builder.anomalies(anomaly)

    measured, suppressed = count_segments(result)
    builder.execution(
        reconciliation,
        execution_time_ms=(
            result.evidence.duration_ms if execution_time_ms is None else execution_time_ms
        ),
    )
    builder.coverage(suppressed, measured)
    builder.reconciliation(reconciliation)

    return builder.records


def build_validation_records(
    plan: InvestigationPlan,
    result: RcaResult,
    quality: EvidenceQualitySummary,
    *,
    start_sequence: int,
) -> list[EvidenceRecord]:
    """One record per quality check, so the checklist is itself evidence."""
    prov = plan.provenance
    records: list[EvidenceRecord] = []
    for offset, check in enumerate(quality.checks):
        records.append(
            EvidenceRecord(
                id=evidence_id(plan.investigation_id, EvidenceType.VALIDATION, check.check),
                sequence=start_sequence + offset,
                evidence_type=EvidenceType.VALIDATION,
                claim=check.detail,
                analysis_tool=TOOL_VALIDATION,
                metric=result.kpi.name,
                source_dataset=prov.dataset_name,
                source_relation=prov.source_relation,
                source_columns=tuple(
                    c for c in (prov.measure_column, prov.time_column) if c
                ),
                filters=prov.filters,
                # A check is a judgement about other records, not a measurement,
                # so it claims no statement.
                query=None,
                validation_status=_status_for(check.status),
                confidence=EvidenceConfidence.HIGH,
                details={"check": check.check, "status": check.status.value, **check.inputs},
            )
        )
    return records


def _status_for(status: QualityCheckStatus) -> EvidenceValidationStatus:
    if status is QualityCheckStatus.FAILED:
        return EvidenceValidationStatus.FAILED
    if status is QualityCheckStatus.NOT_APPLICABLE:
        return EvidenceValidationStatus.NOT_APPLICABLE
    return EvidenceValidationStatus.VALIDATED
