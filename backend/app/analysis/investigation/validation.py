"""The Evidence Validator: does this analysis hold together, and can it be checked?

Two separate judgements, kept separate on purpose:

* **Reconciliation** asks whether the complete decomposition accounts for the
  whole movement. It is computed over *every* segment - including the residual
  bucket and the immaterial ones - never over the primary/secondary/offsetting
  subsets shown in the UI, which are a selection and are not expected to sum to
  anything.
* **Evidence quality** asks whether the analysis is well-formed and traceable.
  It fails on a broken identity or missing provenance, never on "the data is
  thin". Thin data is a finding *about the data*: it surfaces as low confidence
  on the affected records and in the coverage evidence, and it caveats the
  verdict rather than degrading it.

Nothing here reimplements the maths. Reconciliation reuses
``contribution.contribution_sum`` and ``tree.tree_drift``, which is what stops the
API verdict and the engine's own warning log ever disagreeing.
"""

from dataclasses import replace
from typing import Any

from app.analysis.investigation.constants import (
    CHECK_CONTRIBUTION_RECONCILIATION,
    CHECK_DATA_COVERAGE,
    CHECK_NUMERICAL_CONSISTENCY,
    CHECK_QUERY_PROVENANCE,
    CHECK_REQUIRED_METADATA,
    CHECK_SOURCE_TRACEABILITY,
    COVERAGE_ONLY_CHECKS,
    IDENTITY_ABSOLUTE_TOLERANCE,
    IDENTITY_RELATIVE_TOLERANCE,
    MAX_ROWS_OUTSIDE_RATIO,
    MEASURED_TYPES,
    PERIOD_TYPES,
    QUALITY_CHECK_ORDER,
    SEGMENT_TYPES,
)
from app.analysis.investigation.evidence import trim_zeros
from app.analysis.investigation.models import (
    EvidenceQualitySummary,
    EvidenceRecord,
    InvestigationPlan,
    QualityCheck,
    Reconciliation,
)
from app.analysis.rca import contribution as contrib
from app.analysis.rca import tree as tree_module
from app.analysis.rca.models import (
    AnalysisState,
    AttributionBasis,
    DriverNode,
    RcaResult,
)
from app.analysis.trace import QueryTracer
from app.db.models.enums import (
    EvidenceQuality,
    EvidenceType,
    EvidenceValidationStatus,
    QualityCheckStatus,
    ReconciliationStatus,
    TreeDriftStatus,
)

# States that produced no decomposition to validate. Not failures - a KPI that
# did not change, or has no earlier period, has been analysed correctly.
NO_ANALYSIS_STATES = frozenset({AnalysisState.NO_DATA, AnalysisState.NO_TIME_COLUMN})


def close(left: float | None, right: float | None) -> bool:
    """Whether two computed floats agree, allowing for aggregation noise."""
    if left is None or right is None:
        return left is None and right is None
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= max(
        IDENTITY_ABSOLUTE_TOLERANCE, IDENTITY_RELATIVE_TOLERANCE * scale
    )


# --- reconciliation -----------------------------------------------------------


def reconcile(result: RcaResult, *, tolerance: float) -> Reconciliation:
    """Does the complete decomposition account for the whole movement?"""
    basis = result.attribution.basis
    chosen = result.tree.child_dimension if result.tree is not None else None
    nodes = _nodes_for(result, chosen)

    if nodes:
        total = contrib.contribution_sum(nodes, basis=basis)
    else:
        total = result.evidence.contribution_sum

    drifts = tree_module.tree_drift(result.tree) if result.tree is not None else []
    truncated = {s.dimension for s in result.dimensions_analysed if s.truncated}
    drift_status, drift_payload = _classify_drift(drifts, result.tree, truncated)

    if total is None:
        # An unattributable basis, no previous period or no change. A missing
        # decomposition is not a failed one - reporting FAILED here would mark
        # every MEDIAN KPI as broken.
        reason = (
            result.attribution.unattributable_reason.value
            if result.attribution.unattributable_reason
            else result.state.value
        )
        return Reconciliation(
            status=ReconciliationStatus.NOT_APPLICABLE,
            contribution_sum=None,
            tolerance=tolerance,
            basis=basis.value,
            tree_drift_status=drift_status,
            drifting_nodes=drift_payload,
            detail=(
                f"No contribution decomposition exists to reconcile ({reason}), so there is "
                "nothing to check rather than something that failed."
            ),
        )

    within = abs(total - 1.0) <= tolerance
    status = ReconciliationStatus.PASSED if within else ReconciliationStatus.FAILED
    share = trim_zeros(f"{total * 100:.4f}")
    if within:
        detail = (
            f"The complete decomposition over {chosen or 'the analysed dimension'} accounts for "
            f"{share}% of the movement, within the {tolerance:g} tolerance."
        )
        if basis is AttributionBasis.GROSS_MOVEMENT:
            detail += (
                " Under the gross-movement basis this is the sum of magnitudes, not of signed "
                "shares."
            )
    else:
        detail = (
            f"The complete decomposition accounts for {share}% of the movement, outside the "
            f"{tolerance:g} tolerance. This is reported rather than normalised away, because "
            "re-normalising would hide a lost-rows bug permanently."
        )
    if drift_status is TreeDriftStatus.DRIFT_UNEXPLAINED:
        detail += (
            f" {len(drift_payload)} tree node(s) do not sum to their parent for any legitimate "
            "reason, which is the case worth investigating."
        )
    elif drift_status is TreeDriftStatus.DRIFT_EXPLAINED:
        detail += (
            f" {len(drift_payload)} tree node(s) drift from their parent, each explained by a "
            "pure split or a truncated level."
        )

    return Reconciliation(
        status=status,
        contribution_sum=total,
        tolerance=tolerance,
        basis=basis.value,
        tree_drift_status=drift_status,
        drifting_nodes=drift_payload,
        detail=detail,
    )


def _nodes_for(result: RcaResult, dimension: str | None) -> list[DriverNode]:
    """Every depth-1 node of the chosen dimension, materiality irrelevant.

    The complete set is the point: section 9's caveat is that the displayed
    primary/secondary/offsetting subsets need not sum to 100% while the whole
    decomposition does.
    """
    lookup = dict(result.dimension_results)
    if dimension is not None and dimension in lookup:
        return list(lookup[dimension])
    for _, nodes in result.dimension_results:
        return list(nodes)
    return []


def _classify_drift(
    drifts: list[tree_module.TreeDrift],
    root: DriverNode | None,
    truncated: set[str],
) -> tuple[TreeDriftStatus, tuple[dict[str, Any], ...]]:
    """Three states, because two causes of drift are legitimate.

    A pure split carries a child that cannot be scored by deviation-from-
    proportional, and a truncated level loses its remainder with no residual
    bucket to hold it. Anything else is the lost-rows case the engine's warning
    log exists for, and it is worth a verdict rather than only a log line.
    """
    if not drifts:
        return TreeDriftStatus.PASSED, ()

    under_truncated = _dimensions_under_truncation(root, truncated)
    payload: list[dict[str, Any]] = []
    unexplained = 0
    for drift in drifts:
        explained_by_truncation = drift.node_id in under_truncated
        explained = drift.is_pure_split or explained_by_truncation
        if not explained:
            unexplained += 1
        payload.append(
            {
                "node_id": drift.node_id,
                "depth": drift.depth,
                "parent_contribution": drift.parent_contribution,
                "children_sum": drift.children_sum,
                "is_pure_split": drift.is_pure_split,
                "under_truncated_level": explained_by_truncation,
                "explained": explained,
            }
        )

    status = (
        TreeDriftStatus.DRIFT_UNEXPLAINED if unexplained else TreeDriftStatus.DRIFT_EXPLAINED
    )
    return status, tuple(payload)


def _dimensions_under_truncation(root: DriverNode | None, truncated: set[str]) -> set[str]:
    """Node ids whose split was over a dimension that got truncated."""
    if root is None or not truncated:
        return set()
    found: set[str] = set()

    def walk(node: DriverNode) -> None:
        if node.child_dimension in truncated:
            found.add(node.node_id)
        for child in node.children:
            walk(child)

    walk(root)
    return found


# --- the six quality checks ---------------------------------------------------


def assess(
    plan: InvestigationPlan,
    result: RcaResult,
    records: list[EvidenceRecord],
    queries: QueryTracer,
    reconciliation: Reconciliation,
    *,
    rca_statements: int | None = None,
) -> EvidenceQualitySummary:
    """Run all six checks and reach an overall verdict.

    ``rca_statements`` is how many of the traced statements the RCA engine itself
    ran. It has to be passed in because the trace may also hold the anomaly
    engine's statements, and only the RCA's count is what ``RcaResult`` reports.
    """
    checks = {
        CHECK_DATA_COVERAGE: _check_coverage(result),
        CHECK_NUMERICAL_CONSISTENCY: _check_consistency(result),
        CHECK_CONTRIBUTION_RECONCILIATION: _check_reconciliation(reconciliation),
        CHECK_QUERY_PROVENANCE: _check_provenance(
            result, records, queries, rca_statements=rca_statements
        ),
        CHECK_SOURCE_TRACEABILITY: _check_traceability(plan, records),
        CHECK_REQUIRED_METADATA: _check_metadata(records),
    }
    ordered = tuple(checks[name] for name in QUALITY_CHECK_ORDER)

    if result.state in NO_ANALYSIS_STATES:
        return EvidenceQualitySummary(
            verdict=EvidenceQuality.NOT_APPLICABLE,
            checks=ordered,
            caveats=(f"No analysis was produced ({result.state.value}), so there is nothing to "
                     "validate.",),
        )

    failed = [c for c in ordered if c.status is QualityCheckStatus.FAILED]
    warnings = [c for c in ordered if c.status is QualityCheckStatus.WARNING]
    caveats = tuple(c.detail for c in warnings)

    if failed:
        verdict = EvidenceQuality.FAILED
    elif any(c.check not in COVERAGE_ONLY_CHECKS for c in warnings):
        verdict = EvidenceQuality.WARNING
    else:
        # Coverage-only warnings caveat the verdict without degrading it.
        verdict = EvidenceQuality.VALIDATED

    return EvidenceQualitySummary(verdict=verdict, checks=ordered, caveats=caveats)


def _check_coverage(result: RcaResult) -> QualityCheck:
    evidence = result.evidence
    periods = result.periods
    inputs: dict[str, Any] = {
        "rows_scanned": evidence.total_rows,
        "rows_in_previous_period": evidence.previous_rows,
        "rows_in_current_period": evidence.current_rows,
        "rows_outside_periods": evidence.rows_outside_periods,
        "unparsed_time_rows": evidence.unparsed_time_rows,
        "unparsed_measure_rows": evidence.unparsed_measure_rows,
    }

    if result.state in NO_ANALYSIS_STATES or periods is None:
        return QualityCheck(
            CHECK_DATA_COVERAGE,
            QualityCheckStatus.NOT_APPLICABLE,
            f"No periods were resolved ({result.state.value}), so there is no coverage to check.",
            inputs,
        )

    # The half-open contract: the previous window must end exactly where the
    # current one starts, or rows fall between them unnoticed.
    contiguous = periods.previous.end == periods.current.start
    inputs["periods_contiguous"] = contiguous
    ordered = (
        periods.current.start < periods.current.end
        and periods.previous.start < periods.previous.end
    )
    inputs["periods_ordered"] = ordered

    if not ordered:
        return QualityCheck(
            CHECK_DATA_COVERAGE,
            QualityCheckStatus.FAILED,
            "A compared window starts at or after it ends, so it describes no rows.",
            inputs,
        )
    if result.state is not AnalysisState.NO_PREVIOUS_PERIOD and evidence.previous_rows == 0:
        return QualityCheck(
            CHECK_DATA_COVERAGE,
            QualityCheckStatus.FAILED,
            "The analysis reports a period-over-period comparison, but the previous window "
            "contains no rows.",
            inputs,
        )

    problems: list[str] = []
    if not contiguous:
        problems.append("the two windows are not contiguous, so rows can fall between them")
    if evidence.total_rows:
        outside = evidence.rows_outside_periods / evidence.total_rows
        inputs["rows_outside_ratio"] = outside
        if outside > MAX_ROWS_OUTSIDE_RATIO:
            problems.append(
                f"{outside * 100:.2f}% of rows fall outside both compared windows, so the "
                "comparison describes a small slice of this dataset"
            )
    if evidence.unparsed_time_rows:
        problems.append(
            f"{evidence.unparsed_time_rows:,} rows have a date that could not be read and belong "
            "to neither window"
        )
    if evidence.unparsed_measure_rows:
        problems.append(
            f"{evidence.unparsed_measure_rows:,} rows have a measure that could not be read"
        )

    if problems:
        return QualityCheck(
            CHECK_DATA_COVERAGE,
            QualityCheckStatus.WARNING,
            "Coverage is usable with caveats: " + "; ".join(problems) + ".",
            inputs,
        )
    return QualityCheck(
        CHECK_DATA_COVERAGE,
        QualityCheckStatus.PASSED,
        f"Both windows hold rows ({evidence.previous_rows:,} previous, "
        f"{evidence.current_rows:,} current), they are contiguous, and every row's date and "
        "measure could be read.",
        inputs,
    )


def _check_consistency(result: RcaResult) -> QualityCheck:
    """The identities the engine claims. These are exact, not estimates."""
    kpi = result.kpi
    failures: list[str] = []
    checked = 0

    if kpi.current_value is not None and kpi.previous_value is not None:
        checked += 1
        if not close(kpi.absolute_change, kpi.current_value - kpi.previous_value):
            failures.append(
                f"the KPI change {kpi.absolute_change} is not "
                f"{kpi.current_value} - {kpi.previous_value}"
            )
    if kpi.percent_change is not None and kpi.previous_value:
        checked += 1
        expected = (kpi.absolute_change or 0.0) / abs(kpi.previous_value) * 100.0
        if not close(kpi.percent_change, expected):
            failures.append(
                f"the KPI percent change {kpi.percent_change} does not follow from its values"
            )

    for dimension, nodes in result.dimension_results:
        for node in nodes:
            checked += 1
            if not close(
                node.absolute_change,
                (node.current_value or 0.0) - (node.previous_value or 0.0),
            ):
                failures.append(f"{dimension} {node.value!r} change does not match its values")
            if node.expected_change is not None and node.excess_change is not None:
                checked += 1
                if not close(
                    node.absolute_change, node.expected_change + node.excess_change
                ):
                    failures.append(
                        f"{dimension} {node.value!r} expected + excess does not equal its change"
                    )

    # The additivity claim itself: segments of one dimension must reproduce the
    # whole movement. Skipped where the engine already says it cannot.
    if (
        result.attribution.basis
        in {AttributionBasis.NET_CHANGE, AttributionBasis.MIX_RATE}
        and kpi.absolute_change is not None
    ):
        for dimension, nodes in result.dimension_results:
            if not nodes:
                continue
            summary = next(
                (s for s in result.dimensions_analysed if s.dimension == dimension), None
            )
            if summary is not None and summary.truncated:
                continue
            total = sum(n.absolute_change for n in nodes if n.absolute_change is not None)
            checked += 1
            if not close(total, kpi.absolute_change):
                failures.append(
                    f"{dimension} segments sum to {total}, not the KPI change "
                    f"{kpi.absolute_change}"
                )
            break

    inputs = {"identities_checked": checked, "failures": failures}
    if failures:
        return QualityCheck(
            CHECK_NUMERICAL_CONSISTENCY,
            QualityCheckStatus.FAILED,
            "Numbers contradict each other: " + "; ".join(failures[:5]) + ".",
            inputs,
        )
    if not checked:
        return QualityCheck(
            CHECK_NUMERICAL_CONSISTENCY,
            QualityCheckStatus.NOT_APPLICABLE,
            "There are no numeric identities to check on this result.",
            inputs,
        )
    return QualityCheck(
        CHECK_NUMERICAL_CONSISTENCY,
        QualityCheckStatus.PASSED,
        f"All {checked} numeric identities hold: changes match their values, expected plus "
        "excess equals the change, and the segments reproduce the total movement.",
        inputs,
    )


def _check_reconciliation(reconciliation: Reconciliation) -> QualityCheck:
    inputs = {
        "contribution_sum": reconciliation.contribution_sum,
        "tolerance": reconciliation.tolerance,
        "basis": reconciliation.basis,
        "tree_drift_status": reconciliation.tree_drift_status.value,
    }
    if reconciliation.status is ReconciliationStatus.NOT_APPLICABLE:
        status = QualityCheckStatus.NOT_APPLICABLE
    elif reconciliation.passed:
        status = QualityCheckStatus.PASSED
    elif reconciliation.status is ReconciliationStatus.PASSED:
        # The level reconciles but a tree node drifts for no legitimate reason.
        status = QualityCheckStatus.WARNING
    else:
        status = QualityCheckStatus.FAILED
    return QualityCheck(
        CHECK_CONTRIBUTION_RECONCILIATION, status, reconciliation.detail, inputs
    )


def _check_provenance(
    result: RcaResult,
    records: list[EvidenceRecord],
    queries: QueryTracer,
    *,
    rca_statements: int | None = None,
) -> QualityCheck:
    """Every measured number points at the statement that produced it.

    The byte-identity assertion is the mechanical guard on "never fabricate SQL":
    a plausible-looking statement that was never executed fails here.
    """
    by_sequence = {r.sequence: r for r in queries.records}
    failures: list[str] = []
    linked = 0
    unlinked = 0

    # The trace can hold more than the RCA's statements when anomaly detection
    # shared it, so the comparison is against the RCA's own slice - not the total.
    traced_by_rca = queries.count if rca_statements is None else rca_statements
    if traced_by_rca != result.evidence.statements_executed:
        failures.append(
            f"the trace holds {traced_by_rca} root-cause statements but the result reports "
            f"{result.evidence.statements_executed}"
        )

    for record in records:
        if record.query_sequence is None and record.query is None:
            # A derived record restates other evidence, so having no statement is
            # correct rather than missing.
            if record.evidence_type in MEASURED_TYPES and not record.derived:
                unlinked += 1
            continue
        traced = by_sequence.get(record.query_sequence or -1)
        if traced is None:
            failures.append(
                f"{record.evidence_type.value} cites statement {record.query_sequence}, "
                "which is not in the trace"
            )
            continue
        if record.query != traced.sql:
            failures.append(
                f"{record.evidence_type.value} carries SQL that is not statement "
                f"{record.query_sequence} verbatim"
            )
            continue
        linked += 1

    inputs = {
        "statements_traced": queries.count,
        "root_cause_statements": traced_by_rca,
        "statements_reported": result.evidence.statements_executed,
        "records_linked": linked,
        "measured_records_without_query": unlinked,
        "failures": failures,
    }
    if failures:
        return QualityCheck(
            CHECK_QUERY_PROVENANCE,
            QualityCheckStatus.FAILED,
            "Query provenance is broken: " + "; ".join(failures[:5]) + ".",
            inputs,
        )
    if unlinked:
        return QualityCheck(
            CHECK_QUERY_PROVENANCE,
            QualityCheckStatus.WARNING,
            f"{linked} measured claims cite the exact statement that produced them, but "
            f"{unlinked} do not name a statement. No SQL was invented to fill the gap.",
            inputs,
        )
    return QualityCheck(
        CHECK_QUERY_PROVENANCE,
        QualityCheckStatus.PASSED,
        f"All {linked} measured claims cite a statement in the trace, and each carries that "
        f"statement verbatim. {queries.count} statements were executed in total.",
        inputs,
    )


def _check_traceability(plan: InvestigationPlan, records: list[EvidenceRecord]) -> QualityCheck:
    """Source metadata is present, and the columns really exist.

    Checking ``source_columns`` against what DESCRIBE returned doubles as
    schema-drift detection at the level of an individual claim.
    """
    physical = set(plan.provenance.physical_columns)
    failures: list[str] = []
    missing_metadata = 0
    unknown_columns: set[str] = set()

    for record in records:
        if not record.source_dataset or not record.source_relation:
            missing_metadata += 1
            continue
        if not record.source_columns:
            missing_metadata += 1
            continue
        if physical:
            for column in record.source_columns:
                if column not in physical:
                    unknown_columns.add(column)

    if missing_metadata:
        failures.append(f"{missing_metadata} records name no source dataset, relation or columns")
    if unknown_columns:
        failures.append(
            "these source columns are not in the relation that was read: "
            + ", ".join(sorted(unknown_columns))
        )

    inputs = {
        "records": len(records),
        "physical_columns": sorted(physical),
        "records_missing_metadata": missing_metadata,
        "unknown_columns": sorted(unknown_columns),
    }
    if failures:
        return QualityCheck(
            CHECK_SOURCE_TRACEABILITY,
            QualityCheckStatus.FAILED,
            "Source traceability is broken: " + "; ".join(failures) + ".",
            inputs,
        )
    return QualityCheck(
        CHECK_SOURCE_TRACEABILITY,
        QualityCheckStatus.PASSED,
        f"All {len(records)} records name their dataset, relation and columns, and every column "
        "named exists in the relation that was actually read.",
        inputs,
    )


def _check_metadata(records: list[EvidenceRecord]) -> QualityCheck:
    failures: list[str] = []
    for record in records:
        prefix = f"{record.evidence_type.value} ({record.sequence})"
        if not record.claim.strip():
            failures.append(f"{prefix} has no claim")
        if not record.analysis_tool:
            failures.append(f"{prefix} names no analysis tool")
        if record.confidence is None:
            failures.append(f"{prefix} carries no confidence")
        if record.evidence_type in SEGMENT_TYPES:
            if not record.dimension:
                failures.append(f"{prefix} names no dimension")
            if record.dimension_value is None and not record.dimension_value_is_null:
                failures.append(f"{prefix} names no segment value")
        if record.evidence_type in PERIOD_TYPES and not (
            record.previous_period and record.current_period
        ):
            failures.append(f"{prefix} does not name both periods")

    inputs = {"records": len(records), "failures": failures}
    if failures:
        return QualityCheck(
            CHECK_REQUIRED_METADATA,
            QualityCheckStatus.FAILED,
            "Required metadata is missing: " + "; ".join(failures[:5]) + ".",
            inputs,
        )
    return QualityCheck(
        CHECK_REQUIRED_METADATA,
        QualityCheckStatus.PASSED,
        f"All {len(records)} records carry a claim, an analysis tool, a confidence, and the "
        "dimension, segment and period fields their type requires.",
        inputs,
    )


# --- stamping the verdict onto the records ------------------------------------


def apply_verdict(
    records: list[EvidenceRecord], quality: EvidenceQualitySummary
) -> list[EvidenceRecord]:
    """Promote or demote each record now that the checks have run.

    A record left UNVERIFIED would mean the validator never reached it, so every
    record gets an explicit status.
    """
    failed_provenance = any(
        c.check in {CHECK_QUERY_PROVENANCE, CHECK_SOURCE_TRACEABILITY}
        and c.status is QualityCheckStatus.FAILED
        for c in quality.checks
    )
    failed_numbers = any(
        c.check in {CHECK_NUMERICAL_CONSISTENCY, CHECK_REQUIRED_METADATA}
        and c.status is QualityCheckStatus.FAILED
        for c in quality.checks
    )

    updated: list[EvidenceRecord] = []
    for record in records:
        if record.evidence_type is EvidenceType.VALIDATION:
            updated.append(record)
            continue
        if failed_numbers or (failed_provenance and record.evidence_type in MEASURED_TYPES):
            status = EvidenceValidationStatus.FAILED
        elif quality.verdict is EvidenceQuality.NOT_APPLICABLE:
            status = EvidenceValidationStatus.NOT_APPLICABLE
        else:
            status = EvidenceValidationStatus.VALIDATED
        updated.append(replace(record, validation_status=status))
    return updated
