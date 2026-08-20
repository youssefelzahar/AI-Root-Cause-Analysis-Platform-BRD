"""The RCA engine: the only module in this package that executes SQL.

Flow: DESCRIBE the relation, project it once into a narrow temp table, resolve
the two periods from the data's own bounds, then aggregate. Recursion is
breadth-first by depth so each tree level costs one statement rather than one
per node.
"""

import time
from datetime import datetime
from typing import Any

import duckdb

from app.analysis.rca import casting, dimension_analysis, period_analysis, ranking, tree
from app.analysis.rca.constants import (
    ABS_EPSILON,
    CONTRIBUTION_SUM_TOLERANCE,
    MIN_ROWS_FOR_MEAN_STABILITY,
    OTHER_BUCKET,
)
from app.analysis.rca.contribution import (
    basis_for,
    build_nodes,
    contribution_sum,
    explanatory_power,
    mark_residual,
    percent_change,
)
from app.analysis.rca.models import (
    AnalysisState,
    Attribution,
    AttributionBasis,
    ChangePattern,
    DimensionSummary,
    DriverNode,
    Evidence,
    Grain,
    KpiChange,
    Notice,
    Period,
    PeriodResolution,
    RcaResult,
    RcaSpec,
    SegmentTotals,
    Totals,
    UnattributableReason,
)
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.db.models.enums import Aggregation

logger = get_logger(__name__)


class _Counter:
    """Counts statements so the response can report the real query cost."""

    def __init__(self) -> None:
        self.count = 0

    def execute(self, conn: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):
        self.count += 1
        return conn.execute(sql, params) if params else conn.execute(sql)


def _describe(conn: duckdb.DuckDBPyConnection, relation: str, counter: _Counter) -> dict[str, str]:
    rows = counter.execute(conn, f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return {row[0]: str(row[1]) for row in rows}


def _require_column(physical: dict[str, str], column: str, role: str) -> str:
    """Whitelist an identifier against the relation actually being read.

    The KPI definition is a stored row, so "it was validated when it was
    created" is not enough - the file can be re-uploaded with a different
    schema. Checking here doubles as schema-drift detection, and the caller
    still quotes the name before interpolating it.
    """
    if column not in physical:
        raise ValidationError(
            f"The {role} column {column!r} is not present in this dataset.",
            code="RCA_COLUMN_MISSING",
            details={"column": column, "role": role},
        )
    return physical[column]


def _rows_to_segments(rows: list[tuple], dimension: str | None = None) -> dict[str, list[SegmentTotals]]:
    grouped: dict[str, list[SegmentTotals]] = {}
    for dim, seg, cur_v, prev_v, cur_n, prev_n, cur_r, prev_r in rows:
        name = dimension or dim
        grouped.setdefault(name, []).append(
            SegmentTotals(
                dimension=name,
                value=seg,
                value_is_null=seg is None,
                current_value=float(cur_v) if cur_v is not None else None,
                previous_value=float(prev_v) if prev_v is not None else None,
                current_count=int(cur_n or 0),
                previous_count=int(prev_n or 0),
                current_rows=int(cur_r or 0),
                previous_rows=int(prev_r or 0),
            )
        )
    return grouped


def _residual_segment(
    dimension: str, kept: list[SegmentTotals], totals: Totals
) -> SegmentTotals | None:
    """Everything past the truncation limit, by subtraction from the level total.

    Computed rather than queried, so contributions still sum to one and the
    truncation is lossless in aggregate even though individual segments are not
    listed.
    """
    kept_current = sum(s.current_value or 0.0 for s in kept)
    kept_previous = sum(s.previous_value or 0.0 for s in kept)
    kept_rows_current = sum(s.current_rows for s in kept)
    kept_rows_previous = sum(s.previous_rows for s in kept)

    current = (totals.current_value or 0.0) - kept_current
    previous = (totals.previous_value or 0.0) - kept_previous
    rows_current = totals.current_rows - kept_rows_current
    rows_previous = totals.previous_rows - kept_rows_previous

    if rows_current <= 0 and rows_previous <= 0:
        return None

    return SegmentTotals(
        dimension=dimension,
        value=OTHER_BUCKET,
        value_is_null=False,
        current_value=current,
        previous_value=previous,
        current_count=max(0, totals.current_count - sum(s.current_count for s in kept)),
        previous_count=max(0, totals.previous_count - sum(s.previous_count for s in kept)),
        current_rows=max(0, rows_current),
        previous_rows=max(0, rows_previous),
    )


def _summarise(kpi: KpiChange, primary: list[DriverNode], pattern: ChangePattern) -> str:
    """A factual sentence. Contribution to a change, never causation."""
    if kpi.absolute_change is None:
        return f"{kpi.name} could not be compared across periods."
    if abs(kpi.absolute_change) <= ABS_EPSILON:
        return f"{kpi.name} did not change between the two periods."

    verb = "increased" if kpi.absolute_change > 0 else "decreased"
    magnitude = (
        f" by {abs(kpi.percent_change):.1f}%" if kpi.percent_change is not None else ""
    )
    head = f"{kpi.name} {verb}{magnitude} versus the previous period."

    if pattern is ChangePattern.BROAD_BASED:
        return f"{head} The change is broad-based: no dimension concentrates it."
    if not primary:
        return f"{head} No individual segment accounts for a material share."

    top = primary[0]
    share = f"{abs(top.contribution) * 100:.0f}%" if top.contribution is not None else "an unknown share"
    label = top.value if top.value is not None else "(no value)"
    return f"{head} The largest contributor is {top.dimension} {label}, at {share} of the movement."


def run_investigation(
    conn: duckdb.DuckDBPyConnection,
    relation: str,
    spec: RcaSpec,
) -> RcaResult:
    """Execute a full investigation against an open DuckDB relation."""
    started = time.perf_counter()
    counter = _Counter()
    notices: list[Notice] = []

    physical = _describe(conn, relation, counter)
    measure_type = _require_column(physical, spec.measure_column, "measure")

    # --- dimensions the profile says are unusable are dropped, not guessed at
    usable: list[str] = []
    excluded: list[DimensionSummary] = []
    for name in spec.dimensions:
        if name not in physical:
            excluded.append(DimensionSummary(name, 0, excluded_reason="column_missing"))
            continue
        usable.append(name)

    if not spec.time_column:
        return _no_time_column_result(spec, notices, counter, started)

    time_type = _require_column(physical, spec.time_column, "time")
    time_expr = casting.time_expression(spec.time_column, time_type)
    if not time_expr:
        raise ValidationError(
            f"The time column {spec.time_column!r} holds {time_type}, which cannot be read as a date.",
            code="RCA_TIME_COLUMN_NOT_TEMPORAL",
            details={"column": spec.time_column, "type": time_type},
        )

    measure_expr = casting.measure_expression(spec.measure_column, measure_type, spec.aggregation)
    if not measure_expr:
        raise ValidationError(
            f"The measure column {spec.measure_column!r} holds {measure_type}, "
            f"which cannot be aggregated with {spec.aggregation.value}.",
            code="RCA_MEASURE_NOT_NUMERIC",
            details={"column": spec.measure_column, "type": measure_type},
        )

    dimension_exprs = [casting.dimension_expression(name, physical[name]) for name in usable]
    offsets = {name: i for i, name in enumerate(usable)}

    where_clause, filter_params = dimension_analysis.build_filter_clause(spec.filters, physical)
    counter.execute(
        conn,
        dimension_analysis.build_base_table_sql(
            relation,
            time_expr=time_expr,
            measure_expr=measure_expr,
            dimension_exprs=dimension_exprs,
            where_clause=where_clause,
        ),
        filter_params or None,
    )

    bounds = counter.execute(conn, dimension_analysis.build_bounds_sql()).fetchone()
    min_ts, max_ts, total_rows, parsed_time_rows, parsed_measure_rows = bounds
    total_rows = int(total_rows or 0)

    if not total_rows or min_ts is None or max_ts is None:
        return _empty_result(
            spec,
            AnalysisState.NO_DATA if not total_rows else AnalysisState.NO_TIME_COLUMN,
            notices,
            counter,
            started,
            total_rows=total_rows,
        )

    if int(parsed_time_rows or 0) == 0:
        raise ValidationError(
            f"No value in {spec.time_column!r} could be read as a date.",
            code="RCA_TIME_COLUMN_NOT_TEMPORAL",
            details={"column": spec.time_column},
        )
    if spec.aggregation not in casting.COUNTING and int(parsed_measure_rows or 0) == 0:
        raise ValidationError(
            f"No value in {spec.measure_column!r} could be read as a number.",
            code="RCA_MEASURE_NOT_NUMERIC",
            details={"column": spec.measure_column},
        )

    unparsed_time = total_rows - int(parsed_time_rows or 0)
    unparsed_measure = total_rows - int(parsed_measure_rows or 0)
    if unparsed_time:
        notices.append(
            Notice(
                "UNPARSED_TIME_ROWS",
                "warning",
                f"{unparsed_time:,} rows have a {spec.time_column} value that is not a date; "
                "they are excluded from both periods.",
                {"rows": unparsed_time},
            )
        )
    if unparsed_measure and spec.aggregation not in casting.COUNTING:
        notices.append(
            Notice(
                "UNPARSED_MEASURE_ROWS",
                "warning",
                f"{unparsed_measure:,} rows have a {spec.measure_column} value that is not a number.",
                {"rows": unparsed_measure},
            )
        )

    resolution = period_analysis.resolve_periods(
        comparison=spec.comparison,
        comparison_config=spec.comparison_config,
        min_ts=min_ts if isinstance(min_ts, datetime) else datetime.fromisoformat(str(min_ts)),
        max_ts=max_ts if isinstance(max_ts, datetime) else datetime.fromisoformat(str(max_ts)),
        detected_frequency=spec.detected_frequency,
    )

    if resolution.grain is Grain.EQUAL_SPAN:
        notices.append(
            Notice(
                "FREQUENCY_UNKNOWN_EQUAL_SPAN",
                "warning",
                "No reporting frequency could be detected, so the data was split in half by date "
                "rather than compared as calendar periods.",
            )
        )
    if resolution.excluded_partial_period is not None:
        notices.append(
            Notice(
                "PARTIAL_CURRENT_PERIOD",
                "warning",
                "The most recent period is incomplete and was excluded, so a partly-collected "
                "period is not reported as a collapse.",
                {
                    "start": resolution.excluded_partial_period.start.isoformat(),
                    "end": resolution.excluded_partial_period.end.isoformat(),
                },
            )
        )

    totals_row = counter.execute(
        conn,
        dimension_analysis.build_totals_sql(spec.aggregation, resolution.current, resolution.previous),
    ).fetchone()
    totals = Totals(
        current_value=float(totals_row[0]) if totals_row[0] is not None else None,
        previous_value=float(totals_row[1]) if totals_row[1] is not None else None,
        current_count=int(totals_row[2] or 0),
        previous_count=int(totals_row[3] or 0),
        current_rows=int(totals_row[4] or 0),
        previous_rows=int(totals_row[5] or 0),
    )
    rows_outside = int(totals_row[6] or 0)

    resolution = PeriodResolution(
        current=Period(
            "current", resolution.current.start, resolution.current.end, totals.current_rows
        ),
        previous=Period(
            "previous", resolution.previous.start, resolution.previous.end, totals.previous_rows
        ),
        grain=resolution.grain,
        strategy=resolution.strategy,
        anchor=resolution.anchor,
        excluded_partial_period=resolution.excluded_partial_period,
    )

    evidence_base = {
        "total_rows": total_rows,
        "current_rows": totals.current_rows,
        "previous_rows": totals.previous_rows,
        "rows_outside_periods": rows_outside,
        "unparsed_time_rows": unparsed_time,
        "unparsed_measure_rows": unparsed_measure,
    }

    kpi = _kpi_change(spec, totals, resolution)

    if totals.current_rows == 0 and totals.previous_rows == 0:
        return _empty_result(spec, AnalysisState.NO_DATA, notices, counter, started, **evidence_base)
    if totals.previous_rows == 0:
        notices.append(
            Notice(
                "NO_PREVIOUS_PERIOD",
                "warning",
                f"This dataset has no rows before {resolution.current.start:%Y-%m-%d}, so there is "
                "nothing to compare the current period against.",
            )
        )
        return _descriptive_result(
            spec,
            AnalysisState.NO_PREVIOUS_PERIOD,
            kpi,
            resolution,
            notices,
            counter,
            started,
            UnattributableReason.NO_PREVIOUS_PERIOD,
            evidence_base,
        )

    if not usable:
        notices.append(
            Notice(
                "NO_DIMENSIONS_CONFIGURED",
                "info",
                "This KPI has no analysis dimensions, so the change is reported without drivers.",
            )
        )
        return _descriptive_result(
            spec, AnalysisState.OK, kpi, resolution, notices, counter, started, None, evidence_base
        )

    # --- level 1: every dimension in one statement --------------------------
    rows = counter.execute(
        conn,
        dimension_analysis.build_breakdown_sql(
            spec.aggregation,
            usable,
            resolution.current,
            resolution.previous,
            limit=min(spec.max_values_per_dimension, spec.max_segments_scanned),
            dimension_offsets=offsets,
        ),
    ).fetchall()
    by_dimension = _rows_to_segments(rows)

    overlap_gap = None
    if spec.aggregation is Aggregation.COUNT_DISTINCT and usable:
        gap_rows = counter.execute(
            conn,
            dimension_analysis.build_distinct_overlap_sql(
                usable[0], offsets[usable[0]], resolution.current, resolution.previous
            ),
        ).fetchall()
        gaps = {row[0]: int(row[1] or 0) for row in gap_rows}
        overlap_gap = (gaps.get("previous", 0), gaps.get("current", 0))

    first_segments = by_dimension.get(usable[0], [])
    basis, reason = basis_for(
        spec.aggregation, totals, first_segments, distinct_overlap_gap=overlap_gap
    )

    global_change = totals.absolute_change
    if basis is AttributionBasis.UNATTRIBUTABLE:
        notices.append(
            Notice(
                "AGGREGATION_NOT_ATTRIBUTABLE",
                "info",
                f"{spec.aggregation.value} cannot be split across segments in a way that adds up, "
                "so segment changes are reported without contribution percentages.",
                {"reason": reason.value if reason else None},
            )
        )

    if (
        global_change is not None
        and abs(global_change) <= ABS_EPSILON
        and basis is not AttributionBasis.GROSS_MOVEMENT
    ):
        notices.append(
            Notice("NO_CHANGE_DETECTED", "info", f"{spec.kpi_name} did not change between the periods.")
        )
        return _descriptive_result(
            spec, AnalysisState.NO_CHANGE, kpi, resolution, notices, counter, started, None, evidence_base
        )

    # --- build the per-dimension node sets ----------------------------------
    dimension_nodes: dict[str, list[DriverNode]] = {}
    summaries: list[DimensionSummary] = list(excluded)

    for name in usable:
        segments = by_dimension.get(name, [])
        truncated = len(segments) >= spec.max_values_per_dimension
        if truncated:
            residual = _residual_segment(name, segments, totals)
            if residual is not None:
                segments = segments + [residual]
            notices.append(
                Notice(
                    "DIMENSION_TRUNCATED",
                    "warning",
                    f"{name} has more distinct values than can be listed; the remainder is grouped "
                    f"as {OTHER_BUCKET}.",
                    {"dimension": name, "listed": spec.max_values_per_dimension},
                )
            )
        nodes = build_nodes(
            segments, totals, global_change, spec.aggregation, basis, reason
        )
        for node in nodes:
            if node.value == OTHER_BUCKET:
                mark_residual(node)
        dimension_nodes[name] = nodes
        summaries.append(
            DimensionSummary(
                dimension=name,
                segment_count=len(nodes),
                truncated=truncated,
                explanatory_power=explanatory_power(nodes, global_change),
            )
        )

    if basis is AttributionBasis.UNATTRIBUTABLE:
        return _descriptive_result(
            spec,
            AnalysisState.UNATTRIBUTABLE,
            kpi,
            resolution,
            notices,
            counter,
            started,
            reason,
            evidence_base,
            dimension_nodes=dimension_nodes,
            dimensions_analysed=summaries,
        )

    # --- classification and drill-down --------------------------------------
    powers = [s.explanatory_power for s in summaries if s.explanatory_power is not None]
    best_power = max(powers) if powers else None

    choice = tree.select_dimension(dimension_nodes, global_change, tuple(usable))
    root = tree.synthetic_root(totals.current_value, totals.previous_value, global_change)

    primary: list[DriverNode] = []
    secondary: list[DriverNode] = []
    offsetting: list[DriverNode] = []

    if choice is not None:
        primary, secondary, offsetting = ranking.classify(
            choice.nodes, max_drivers=spec.max_drivers
        )
        root.children = choice.nodes
        root.child_dimension = choice.dimension
        root.child_split_type = choice.split_type
        root.child_explanatory_power = choice.power

        _drill(
            conn,
            counter,
            spec,
            resolution,
            totals,
            global_change,
            basis,
            reason,
            offsets,
            tuple(usable),
            tree.frontier_for(choice.nodes, 1),
            choice.dimension,
        )

    if primary and all(node.low_support for node in primary):
        notices.append(
            Notice(
                "LOW_SUPPORT_DRIVERS",
                "warning",
                f"Every contributing segment has fewer than {MIN_ROWS_FOR_MEAN_STABILITY} rows, so "
                f"a {spec.aggregation.value} over any one of them is unstable. The contributions "
                "are shown, but treat the ranking as provisional.",
                {"minimum_rows": MIN_ROWS_FOR_MEAN_STABILITY},
            )
        )

    pattern = ranking.change_pattern(primary, offsetting, best_power)
    if pattern is ChangePattern.BROAD_BASED:
        # No dimension concentrates the change; naming drivers would invent them.
        primary, secondary, offsetting = [], [], []
        root.children = []
        notices.append(
            Notice(
                "BROAD_BASED_CHANGE",
                "info",
                "Every segment moved roughly in proportion to its size, so no single driver "
                "explains the change.",
            )
        )

    root.unexplained_share = tree.unexplained_share(root)
    duration_ms = int((time.perf_counter() - started) * 1000)

    return RcaResult(
        state=AnalysisState.OK,
        kpi=kpi,
        attribution=Attribution(
            basis=basis,
            unattributable_reason=reason,
            change_pattern=pattern,
            pareto_target=ranking.PARETO_TARGET,
            min_material_contribution=ranking.MIN_MATERIAL_CONTRIBUTION,
            has_offsetting=bool(offsetting),
            additivity_verified=(overlap_gap == (0, 0)) if overlap_gap is not None else None,
        ),
        periods=resolution,
        primary_drivers=tuple(primary),
        secondary_drivers=tuple(secondary),
        offsetting_factors=tuple(offsetting),
        dimension_results=tuple((name, tuple(nodes)) for name, nodes in dimension_nodes.items()),
        dimensions_analysed=tuple(summaries),
        tree=root if root.children else None,
        evidence=Evidence(
            **evidence_base,
            statements_executed=counter.count,
            duration_ms=duration_ms,
            contribution_sum=contribution_sum(dimension_nodes.get(usable[0], [])),
        ),
        notices=tuple(notices),
        summary=_summarise(kpi, primary, pattern),
    )


def _drill(
    conn: duckdb.DuckDBPyConnection,
    counter: _Counter,
    spec: RcaSpec,
    resolution: PeriodResolution,
    totals: Totals,
    global_change: float | None,
    basis: AttributionBasis,
    reason: UnattributableReason | None,
    offsets: dict[str, int],
    all_dimensions: tuple[str, ...],
    frontier: list[DriverNode],
    used_dimension: str,
) -> None:
    """Descend the strongest path, one statement per node.

    Breadth-first by depth so a level costs one query rather than one per
    dimension, and the whole investigation stays around eight statements.
    """
    used: dict[int, tuple[str, ...]] = {id(node): (used_dimension,) for node in frontier}

    # Bounded by ``drillable`` rather than by the loop condition, so a node that
    # is not expanded always records why - including when the depth limit stops
    # the very first level.
    for _ in range(spec.max_tree_depth):
        if not frontier:
            break
        next_frontier: list[DriverNode] = []
        for node in frontier:
            consumed = used.get(id(node), ())
            remaining = tuple(d for d in all_dimensions if d not in consumed)
            if not tree.drillable(node, remaining, spec.aggregation, spec.max_tree_depth):
                continue

            predicates = []
            params: list[Any] = []
            for dim, value in node.path:
                alias = dimension_analysis.dimension_alias(offsets[dim])
                if value is None:
                    predicates.append(f"{alias} IS NULL")
                else:
                    predicates.append(f"{alias} = ?")
                    params.append(value)

            rows = counter.execute(
                conn,
                dimension_analysis.build_breakdown_sql(
                    spec.aggregation,
                    list(remaining),
                    resolution.current,
                    resolution.previous,
                    limit=spec.max_values_per_dimension,
                    parent_predicates=predicates,
                    dimension_offsets=offsets,
                ),
                params or None,
            ).fetchall()

            child_segments = _rows_to_segments(rows)
            candidates: dict[str, list[DriverNode]] = {}
            for name, segments in child_segments.items():
                candidates[name] = build_nodes(
                    segments,
                    totals,
                    global_change,
                    spec.aggregation,
                    basis,
                    reason,
                    depth=node.depth + 1,
                    parent_path=node.path,
                    parent_change=node.absolute_change,
                )

            choice = tree.select_dimension(candidates, node.absolute_change, remaining)
            if choice is None:
                node.stop_reason = "uniform_within_segment"
                continue

            node.child_dimension = choice.dimension
            node.child_split_type = choice.split_type
            node.child_explanatory_power = choice.power
            node.children = choice.nodes
            for child in choice.nodes:
                child.is_pure_split = choice.split_type == "pure"
            ranking.classify(choice.nodes, max_drivers=spec.max_drivers)
            node.unexplained_share = tree.unexplained_share(node)

            for child in tree.frontier_for(choice.nodes, node.depth + 1):
                used[id(child)] = consumed + (choice.dimension,)
                next_frontier.append(child)

        frontier = next_frontier


def _kpi_change(spec: RcaSpec, totals: Totals, resolution: PeriodResolution) -> KpiChange:
    change = totals.absolute_change
    pct, pct_reason = percent_change(totals.current_value, totals.previous_value)
    return KpiChange(
        name=spec.kpi_name,
        column=spec.measure_column,
        aggregation=spec.aggregation.value,
        time_column=spec.time_column,
        current_value=totals.current_value,
        previous_value=totals.previous_value,
        absolute_change=change,
        percent_change=pct,
        percent_change_undefined_reason=pct_reason,
        direction=ranking.direction(change),
        severity=ranking.severity(pct),
        comparison=spec.comparison.value,
        grain=resolution.grain.value,
    )


def _blank_kpi(spec: RcaSpec) -> KpiChange:
    return KpiChange(
        name=spec.kpi_name,
        column=spec.measure_column,
        aggregation=spec.aggregation.value,
        time_column=spec.time_column,
        current_value=None,
        previous_value=None,
        absolute_change=None,
        percent_change=None,
        percent_change_undefined_reason=None,
        direction="unknown",
        severity="low",
        comparison=spec.comparison.value,
        grain="unknown",
    )


def _attribution(
    basis: AttributionBasis, reason: UnattributableReason | None
) -> Attribution:
    return Attribution(
        basis=basis,
        unattributable_reason=reason,
        change_pattern=ChangePattern.NONE,
        pareto_target=ranking.PARETO_TARGET,
        min_material_contribution=ranking.MIN_MATERIAL_CONTRIBUTION,
        has_offsetting=False,
    )


def _no_time_column_result(
    spec: RcaSpec, notices: list[Notice], counter: _Counter, started: float
) -> RcaResult:
    notices.append(
        Notice(
            "NO_TIME_COLUMN",
            "warning",
            "This KPI has no time column, so there is no period-over-period change to explain.",
        )
    )
    return RcaResult(
        state=AnalysisState.NO_TIME_COLUMN,
        kpi=_blank_kpi(spec),
        attribution=_attribution(
            AttributionBasis.UNATTRIBUTABLE, UnattributableReason.NO_TIME_COLUMN
        ),
        periods=None,
        evidence=Evidence(
            statements_executed=counter.count,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ),
        notices=tuple(notices),
        summary="This KPI has no time column, so a period-over-period analysis is not possible.",
    )


def _empty_result(
    spec: RcaSpec,
    state: AnalysisState,
    notices: list[Notice],
    counter: _Counter,
    started: float,
    **evidence: int,
) -> RcaResult:
    return RcaResult(
        state=state,
        kpi=_blank_kpi(spec),
        attribution=_attribution(
            AttributionBasis.UNATTRIBUTABLE, UnattributableReason.NO_PREVIOUS_PERIOD
        ),
        periods=None,
        evidence=Evidence(
            **evidence,
            statements_executed=counter.count,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ),
        notices=tuple(notices),
        summary="This dataset has no rows to analyse.",
    )


def _descriptive_result(
    spec: RcaSpec,
    state: AnalysisState,
    kpi: KpiChange,
    resolution: PeriodResolution,
    notices: list[Notice],
    counter: _Counter,
    started: float,
    reason: UnattributableReason | None,
    evidence: dict[str, int],
    *,
    dimension_nodes: dict[str, list[DriverNode]] | None = None,
    dimensions_analysed: list[DimensionSummary] | None = None,
) -> RcaResult:
    """A result with numbers but no driver ranking.

    Used when the aggregation cannot be decomposed, when there is no previous
    period, or when nothing changed. Reporting per-segment values without
    contributions is honest; naming a "primary driver" here would be a claim the
    maths does not support.
    """
    basis = (
        AttributionBasis.UNATTRIBUTABLE if reason is not None else AttributionBasis.NET_CHANGE
    )
    return RcaResult(
        state=state,
        kpi=kpi,
        attribution=_attribution(basis, reason),
        periods=resolution,
        dimension_results=tuple(
            (name, tuple(nodes)) for name, nodes in (dimension_nodes or {}).items()
        ),
        dimensions_analysed=tuple(dimensions_analysed or ()),
        evidence=Evidence(
            **evidence,
            statements_executed=counter.count,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ),
        notices=tuple(notices),
        summary=_summarise(kpi, [], ChangePattern.NONE),
    )


def verify_tree(node: DriverNode) -> None:
    """Assert that children sum to their parent, at every level.

    This is the tree's correctness contract. A plain ``assert`` would be
    stripped under ``-O``, so it raises explicitly.
    """
    if node.children:
        total = sum(c.contribution for c in node.children if c.contribution is not None)
        parent = node.contribution
        if parent is not None and abs(total - parent) > CONTRIBUTION_SUM_TOLERANCE:
            logger.warning(
                "rca_tree_contribution_drift",
                extra={"node": node.node_id, "parent": parent, "children": total},
            )
        for child in node.children:
            verify_tree(child)
