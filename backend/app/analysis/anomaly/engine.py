"""The anomaly engine: the only module here that touches a connection.

Flow:

1. Resolve the physical column types from the live relation.
2. Project a two-column base table (bucketable timestamp, numeric measure).
3. Aggregate it into one value per occupied period.
4. Reindex onto a complete calendar grid, so a period with no rows is visibly
   *missing* rather than silently absent or - worse - zero.
5. Hand the series to a detector, which judges each point against the ones
   before it.

Everything after step 3 is pure maths over a few thousand floats.
"""

import math
import statistics
import time
from datetime import datetime

import duckdb

from app.analysis.anomaly import detectors, series
from app.analysis.anomaly.constants import (
    ABS_EPSILON,
    DEFAULT_GRAIN,
    HIGH_ANOMALY_RATE,
    MIN_PERIODS_FOR_FIT_DIAGNOSTICS,
    TRENDING_DRIFT_RATIO,
)
from app.analysis.anomaly.models import (
    AnomalyReport,
    AnomalySpec,
    Evidence,
    Notice,
    Observation,
    ObservationStatus,
    ReportStatus,
)
from app.analysis.rca.casting import measure_expression, time_expression
from app.analysis.rca.models import Grain
from app.analysis.rca.period_analysis import bucket_end, bucket_start, shift_bucket
from app.core.exceptions import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Fewest observations in each half before a drift comparison means anything.
MIN_DRIFT_HALF = 3

# Stated in every report so a reader knows what the method cannot see, rather
# than discovering it from a surprising result.
LIMITATIONS = (
    "The baseline has no seasonal component, so a KPI with a strong weekly or "
    "annual cycle will show its peaks and troughs as anomalies.",
    "A sustained shift to a new level is flagged while it is new, then becomes "
    "the baseline. This detects unusual periods, not change points.",
    "Each period is judged only against the periods before it, so a strong "
    "trend makes the baseline lag behind the series.",
)


class _Counter:
    """Counts statements so the report can say what it cost."""

    def __init__(self) -> None:
        self.count = 0

    def execute(self, conn: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):  # noqa: ANN201
        self.count += 1
        return conn.execute(sql, params) if params else conn.execute(sql)


def _describe(conn: duckdb.DuckDBPyConnection, relation: str) -> dict[str, str]:
    rows = conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return {row[0]: str(row[1]) for row in rows}


def _require_column(physical: dict[str, str], column: str, role: str) -> str:
    """Whitelist an identifier against the live relation.

    Doubles as schema-drift detection: a KPI naming a column the file no longer
    has fails with a message saying which, rather than a DuckDB binder error.
    """
    if column not in physical:
        raise ValidationError(
            f"The {role} column {column!r} is not present in this dataset.",
            code="ANOMALY_COLUMN_MISSING",
            details={"column": column, "role": role},
        )
    return physical[column]


def _empty_report(spec: AnomalySpec, status: ReportStatus, notices: list[Notice],
                  counter: _Counter, started: float, evidence: Evidence | None = None) -> AnomalyReport:
    return AnomalyReport(
        status=status,
        kpi_name=spec.kpi_name,
        aggregation=spec.aggregation,
        grain=spec.grain,
        method=spec.method,
        evidence=evidence
        or Evidence(
            statements_executed=counter.count,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ),
        notices=tuple(notices),
        limitations=LIMITATIONS,
        summary=_empty_summary(status, spec.kpi_name),
    )


def _empty_summary(status: ReportStatus, kpi_name: str) -> str:
    if status is ReportStatus.NO_TIME_COLUMN:
        return f"{kpi_name} has no time column, so it has no history to compare against."
    if status is ReportStatus.NO_DATA:
        return f"There are no {kpi_name} rows to analyse."
    return f"There is not enough history to judge whether {kpi_name} is behaving unusually."


def _reindex(
    rows: list[tuple[datetime, float | None, int]],
    grain: Grain,
    *,
    max_periods: int,
    partial_head: bool = False,
    partial_tail: bool = False,
) -> tuple[list[Observation], bool]:
    """Place the aggregated rows on a complete calendar grid.

    A period the dataset simply has no rows for becomes ``value=None`` with
    status MISSING. It is never zero: "we sold nothing" and "we have no data"
    are different claims, and only one of them is supported by an absent row.

    The grid is capped by span, not by row count - see ``series.grid_bounds``.
    Returns ``(observations, truncated)``.
    """
    if not rows:
        return [], False

    occupied = {row[0]: (row[1], row[2]) for row in rows}
    start, end, truncated = series.grid_bounds(
        rows[0][0], rows[-1][0], grain, max_periods=max_periods
    )

    observations: list[Observation] = []
    cursor = start
    while cursor <= end:
        value, row_count = occupied.get(cursor, (None, 0))
        if value is None:
            status = ObservationStatus.MISSING
        elif (partial_head and cursor == start and not truncated) or (
            partial_tail and cursor == end
        ):
            status = ObservationStatus.PARTIAL
        else:
            status = ObservationStatus.INSUFFICIENT_HISTORY
        observations.append(
            Observation(
                period_start=cursor,
                period_end=bucket_end(cursor, grain),
                value=value,
                row_count=row_count,
                status=status,
            )
        )
        cursor = shift_bucket(cursor, grain, 1)

    return observations, truncated


def detect_anomalies(
    conn: duckdb.DuckDBPyConnection,
    relation: str,
    spec: AnomalySpec,
) -> AnomalyReport:
    """Build the KPI's history and judge every period in it."""
    started = time.perf_counter()
    counter = _Counter()
    notices: list[Notice] = []

    if not spec.time_column:
        notices.append(
            Notice(
                code="NO_TIME_COLUMN",
                severity="warning",
                message=(
                    "This KPI has no time column, so it has no history to compare "
                    "against. Add one in KPI setup."
                ),
            )
        )
        return _empty_report(spec, ReportStatus.NO_TIME_COLUMN, notices, counter, started)

    detector = detectors.get_detector(spec.method)
    if detector is None:
        raise ValidationError(
            f"{spec.method!r} is not a supported detection method.",
            code="ANOMALY_METHOD_UNSUPPORTED",
            details={"method": spec.method, "supported": sorted(detectors.DETECTORS)},
        )

    if not series.is_bucketable(spec.grain):
        raise ValidationError(
            f"{spec.grain.value!r} is not a reporting grain a series can be built on.",
            code="ANOMALY_GRAIN_UNSUPPORTED",
            details={"grain": spec.grain.value},
        )

    if spec.grain_assumed:
        notices.append(
            Notice(
                code="GRAIN_ASSUMED",
                severity="info",
                message=(
                    f"No reporting frequency could be detected for this dataset, so the "
                    f"series was built at {spec.grain.value} grain."
                ),
                details={"grain": spec.grain.value, "default": DEFAULT_GRAIN},
            )
        )

    physical = _describe(conn, relation)
    time_type = _require_column(physical, spec.time_column, "time")
    time_expr = time_expression(spec.time_column, time_type)
    if not time_expr:
        raise ValidationError(
            f"The time column {spec.time_column!r} holds {time_type}, which cannot be read as a date.",
            code="ANOMALY_TIME_COLUMN_NOT_TEMPORAL",
            details={"column": spec.time_column, "type": time_type},
        )

    measure_type = _require_column(physical, spec.measure_column, "measure")
    measure_expr = measure_expression(spec.measure_column, measure_type, spec.aggregation)
    if not measure_expr:
        raise ValidationError(
            f"The measure column {spec.measure_column!r} holds {measure_type}, which cannot be "
            f"aggregated with {spec.aggregation.value}.",
            code="ANOMALY_MEASURE_NOT_NUMERIC",
            details={"column": spec.measure_column, "type": measure_type},
        )

    where_clause, params = series.build_filter_clause(spec.filters, physical)
    counter.execute(
        conn,
        series.build_base_table_sql(
            relation, time_expr=time_expr, measure_expr=measure_expr, where_clause=where_clause
        ),
        params or None,
    )

    total_rows, parsed_time, parsed_measure, min_ts, _max_ts = counter.execute(
        conn, series.build_bounds_sql()
    ).fetchone()
    total_rows = int(total_rows or 0)
    unparsed_time = total_rows - int(parsed_time or 0)
    unparsed_measure = total_rows - int(parsed_measure or 0)

    if unparsed_time:
        notices.append(
            Notice(
                code="UNPARSED_TIME_ROWS",
                severity="warning",
                message=(
                    f"{unparsed_time:,} rows have a {spec.time_column} value that is not a date. "
                    "They belong to no period and were left out of the series."
                ),
                details={"rows": unparsed_time},
            )
        )
    if unparsed_measure:
        notices.append(
            Notice(
                code="UNPARSED_MEASURE_ROWS",
                severity="warning",
                message=(
                    f"{unparsed_measure:,} rows have a {spec.measure_column} value that is not a "
                    "number and did not contribute to any period's total."
                ),
                details={"rows": unparsed_measure},
            )
        )

    if not total_rows or min_ts is None:
        return _empty_report(
            spec,
            ReportStatus.NO_DATA,
            notices,
            counter,
            started,
            Evidence(
                total_rows=total_rows,
                unparsed_time_rows=unparsed_time,
                unparsed_measure_rows=unparsed_measure,
                statements_executed=counter.count,
                duration_ms=int((time.perf_counter() - started) * 1000),
            ),
        )

    rows = counter.execute(
        conn, series.build_series_sql(spec.aggregation, spec.grain, limit=spec.max_periods)
    ).fetchall()
    raw = [(row[0], _clean(row[1]), int(row[2] or 0)) for row in rows]

    # A bucket whose aggregate came back NaN or Infinity - a 'nan' or 'inf' cell
    # survives TRY_CAST as a real IEEE value and poisons the sum - is treated as
    # unmeasured rather than allowed to propagate into the median.
    non_finite = sum(1 for row in raw if row[1] is None and row[2] > 0)
    if non_finite:
        notices.append(
            Notice(
                code="NON_FINITE_PERIODS",
                severity="warning",
                message=(
                    f"{non_finite:,} period(s) aggregated to a value that is not a finite number, "
                    "usually a 'nan' or 'inf' cell in the source. They are shown as gaps."
                ),
                details={"periods": non_finite},
            )
        )

    head_partial, tail_partial = series.partial_boundaries(
        bucket_start(raw[0][0], spec.grain),
        bucket_start(raw[-1][0], spec.grain),
        min_ts,
        _max_ts,
        spec.grain,
        spec.detected_frequency,
    )

    grid, truncated = _reindex(
        raw,
        spec.grain,
        max_periods=spec.max_periods,
        partial_head=head_partial,
        partial_tail=tail_partial,
    )

    if truncated or len(raw) >= spec.max_periods:
        notices.append(
            Notice(
                code="SERIES_TRUNCATED",
                severity="warning",
                message=(
                    f"This KPI spans more than {spec.max_periods:,} {spec.grain.value} periods. "
                    "Only the most recent ones were analysed."
                ),
                details={"limit": spec.max_periods},
            )
        )

    if head_partial or tail_partial:
        which = "first and last" if head_partial and tail_partial else (
            "first" if head_partial else "last"
        )
        notices.append(
            Notice(
                code="PARTIAL_BOUNDARY_PERIODS",
                severity="info",
                message=(
                    f"The {which} {spec.grain.value} in this range is only partly collected, so its "
                    "total is not comparable with a whole period's. It is shown but not scored."
                ),
                details={"head": head_partial, "tail": tail_partial},
            )
        )

    judged = detector.evaluate(
        grid, window=spec.baseline_window, minimum=spec.min_baseline_observations
    )

    evaluated = [o for o in judged if o.status is ObservationStatus.EVALUATED]
    anomalies = tuple(o for o in evaluated if o.is_anomaly)
    latest = evaluated[-1] if evaluated else None
    missing = sum(1 for o in judged if o.is_missing)

    if not evaluated:
        notices.append(
            Notice(
                code="INSUFFICIENT_HISTORY",
                severity="info",
                message=(
                    f"At least {spec.min_baseline_observations} earlier periods are needed before a "
                    f"period can be judged. This KPI has {len(raw)}."
                ),
                details={
                    "required": spec.min_baseline_observations,
                    "observed": len(raw),
                },
            )
        )

    status = ReportStatus.OK if evaluated else ReportStatus.INSUFFICIENT_HISTORY
    evidence = Evidence(
        total_rows=total_rows,
        rows_in_series=sum(o.row_count for o in judged),
        unparsed_time_rows=unparsed_time,
        unparsed_measure_rows=unparsed_measure,
        periods_observed=len(raw),
        periods_missing=missing,
        periods_evaluated=len(evaluated),
        statements_executed=counter.count,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )

    if missing:
        notices.append(
            Notice(
                code="MISSING_PERIODS",
                severity="info",
                message=(
                    f"{missing:,} {spec.grain.value} period(s) in this range have no rows. They are "
                    "shown as gaps and were left out of the baseline, not counted as zero."
                ),
                details={"periods": missing},
            )
        )

    # Both diagnostics below describe how well the model fits the series, which
    # is not a statement two data points can support.
    if len(evaluated) >= MIN_PERIODS_FOR_FIT_DIAGNOSTICS:
        # A trailing baseline assumes no repeating cycle. If a sixth of the
        # series is "unusual", that assumption has failed and the list of
        # anomalies is an artefact of the method, not a finding. Saying so is
        # more useful than handing back fifty Saturdays.
        rate = len(anomalies) / len(evaluated)
        if rate > HIGH_ANOMALY_RATE:
            notices.append(
                Notice(
                    code="HIGH_ANOMALY_RATE",
                    severity="warning",
                    message=(
                        f"{rate:.0%} of the periods analysed were flagged. A baseline with no "
                        "seasonal component does not fit this KPI - it likely has a repeating "
                        "cycle, which this method does not model."
                    ),
                    details={"rate": round(rate, 4), "threshold": HIGH_ANOMALY_RATE},
                )
            )

        drift = _baseline_drift(judged)
        if drift is not None:
            notices.append(
                Notice(
                    code="TRENDING_BASELINE",
                    severity="info",
                    message=(
                        "This KPI is trending, so the baseline lags behind it. Moves with the "
                        "trend are flagged less readily than moves against it."
                    ),
                    details={"drift": round(drift, 4)},
                )
            )

    return AnomalyReport(
        status=status,
        kpi_name=spec.kpi_name,
        aggregation=spec.aggregation,
        grain=spec.grain,
        method=detector.name,
        series=tuple(judged),
        anomalies=anomalies,
        latest=latest,
        evidence=evidence,
        notices=tuple(notices),
        limitations=LIMITATIONS,
        summary=detectors.summarise(latest, spec.kpi_name),
    )


def _baseline_drift(judged: list[Observation]) -> float | None:
    """Signed drift across the most recent window, or None if it is not trending.

    Compares the median of the window's later half with its earlier half - one
    subtraction, no fitted slope. A trailing baseline necessarily lags a trending
    series, which makes moves *with* the trend harder to flag than moves against
    it. The bias is disclosed rather than corrected: de-trending would make the
    score unexplainable, and an unexplainable score is worse than a biased one
    the reader has been told about.
    """
    values = [o.value for o in judged if o.is_scoreable and o.value is not None]
    if len(values) < 2 * MIN_DRIFT_HALF:
        return None

    window = values[-2 * (len(values) // 2) :]
    half = len(window) // 2
    earlier, later = window[:half], window[half:]
    drift = statistics.median(later) - statistics.median(earlier)

    spread = statistics.median([abs(v - statistics.median(window)) for v in window])
    if spread <= ABS_EPSILON or abs(drift) < TRENDING_DRIFT_RATIO * spread:
        return None
    return drift


def _clean(value: object) -> float | None:
    """DuckDB can return NaN/Infinity, which are not valid JSON."""
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
