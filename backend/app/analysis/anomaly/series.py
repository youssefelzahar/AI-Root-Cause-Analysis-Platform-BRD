"""SQL for the KPI time series. Builds strings; never executes them.

One statement produces the whole series: bucket the time column to the reporting
grain, aggregate the measure inside each bucket with the KPI's own aggregation,
and count the rows behind each point so a value can be traced back to its data.

The aggregation is looked up from a literal map in ``rca.casting`` rather than
formatted from the stored string, so a corrupted KPI definition cannot become
SQL. Duplicate timestamps collapse into their bucket by construction - a GROUP
BY does not care how many rows shared a period.
"""

from datetime import datetime

from app.analysis.rca.casting import aggregate_expression
from app.analysis.rca.dimension_analysis import build_filter_clause
from app.analysis.rca.models import Grain
from app.analysis.rca.period_analysis import (
    FREQUENCY_TO_GRAIN,
    bucket_end,
    bucket_is_complete,
    bucket_start,
    shift_bucket,
)
from app.db.models.enums import Aggregation

# Re-exported so the engine has one import site for the filter grammar. The
# operator whitelist and the parameter binding are the security-relevant parts
# of a KPI filter, and there should be exactly one implementation of them.
__all__ = [
    "BASE_TABLE",
    "build_base_table_sql",
    "build_bounds_sql",
    "build_filter_clause",
    "build_series_sql",
    "grain_for",
    "grid_bounds",
    "is_bucketable",
    "partial_boundaries",
]

BASE_TABLE = "anomaly_base"

# Grains that map onto a DuckDB date_trunc unit. EQUAL_SPAN and CUSTOM are RCA's
# two-window strategies and have no meaning for a continuous series.
BUCKETABLE = frozenset(
    {Grain.DAY, Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.YEAR}
)


def is_bucketable(grain: Grain) -> bool:
    return grain in BUCKETABLE


def build_base_table_sql(
    relation: str,
    *,
    time_expr: str,
    measure_expr: str,
    where_clause: str = "",
) -> str:
    """Project just the two columns the series needs, once.

    Everything downstream reads this table rather than the file, so the casting
    expressions - which can be a strptime chain over text - are evaluated once
    instead of per aggregate.
    """
    where = f" WHERE {where_clause}" if where_clause else ""
    return (
        f"CREATE TEMP TABLE {BASE_TABLE} AS "
        f"SELECT {time_expr} AS t, {measure_expr} AS m FROM {relation}{where}"
    )


def build_bounds_sql() -> str:
    """Row totals and the parse rates behind them.

    ``parsed_time_rows`` and ``parsed_measure_rows`` are what let the engine
    report how many rows it had to leave out, rather than quietly returning a
    series built from a fraction of the file.
    """
    return (
        "SELECT count(*), count(t), count(m), min(t), max(t) "
        f"FROM {BASE_TABLE}"
    )


def build_series_sql(aggregation: Aggregation, grain: Grain, *, limit: int) -> str:
    """One row per occupied period, most recent ``limit`` periods, oldest first.

    Rows with an unparseable timestamp are excluded here - they belong to no
    period, so there is nowhere to put them - and are reported separately from
    the bounds query above.

    ``count(*)`` is the rows behind the point, not the KPI: for a COUNT KPI the
    two coincide, for every other aggregation they do not.
    """
    if not is_bucketable(grain):
        raise ValueError(f"{grain!r} cannot be bucketed into a continuous series.")

    value = aggregate_expression(aggregation, "m")
    # ORDER BY ... DESC LIMIT keeps the *recent* tail when a series is capped;
    # the outer query restores chronological order for the baseline walk.
    #
    # The cast back to TIMESTAMP is load-bearing: date_trunc narrows a TIMESTAMP
    # to a DATE at month grain and coarser, which reaches Python as a
    # datetime.date. Every calendar helper here takes a datetime, and
    # date.replace() does not accept an hour - so without this the grid raises.
    return (
        "SELECT bucket, value, row_count FROM ("
        f"SELECT CAST(date_trunc('{grain.value}', t) AS TIMESTAMP) AS bucket, "
        f"{value} AS value, count(*) AS row_count "
        f"FROM {BASE_TABLE} WHERE t IS NOT NULL "
        "GROUP BY bucket ORDER BY bucket DESC "
        f"LIMIT {int(limit)}"
        ") ORDER BY bucket"
    )


def grain_for(detected_frequency: str | None, requested: Grain | None) -> Grain | None:
    """The grain to build the series at, or None if nothing can be decided.

    An explicit request wins - the profiler's frequency is the *arrival* cadence
    of the rows, which for transactional data is "daily" no matter what the
    business considers a reporting period.

    Deliberately not ``period_analysis.resolve_grain``: that falls back to
    ``EQUAL_SPAN``, which means "halve the observed range" and is meaningless
    for a continuous series - and every calendar helper raises on it.
    """
    if requested is not None:
        return requested if is_bucketable(requested) else None
    return FREQUENCY_TO_GRAIN.get((detected_frequency or "").lower())


def grid_bounds(
    first: datetime, last: datetime, grain: Grain, *, max_periods: int
) -> tuple[datetime, datetime, bool]:
    """The calendar span to materialise, capped to the most recent periods.

    The cap is on the *grid*, not on the occupied buckets. One row stamped
    year 9999 alongside one stamped 2024 is two rows and two buckets, but the
    span between them at day grain is nearly three million periods - every one
    of which would be built, held in memory and serialised. Capping the query's
    row count does not prevent that; capping the span does.

    Returns ``(start, end, truncated)``, keeping the recent end.
    """
    start = bucket_start(first, grain)
    end = bucket_start(last, grain)

    periods = 0
    cursor = start
    while cursor < end and periods < max_periods:
        cursor = shift_bucket(cursor, grain, 1)
        periods += 1

    if cursor < end:
        # The span is longer than the cap. Walk back from the newest bucket
        # instead, so the series that survives is the one a reader cares about.
        return shift_bucket(end, grain, -(max_periods - 1)), end, True
    return start, end, False


def partial_boundaries(
    first_bucket: datetime,
    last_bucket: datetime,
    min_ts: datetime,
    max_ts: datetime,
    grain: Grain,
    detected_frequency: str | None,
) -> tuple[bool, bool]:
    """Whether the first and last buckets are still being filled.

    This is the difference between a real collapse and an artefact. Daily rows
    read at month grain leave the newest month half-collected on every day but
    the last, and a half-collected month looks exactly like a 50% crash - which
    would be reported as a CRITICAL anomaly every time anyone uploaded data
    mid-month.

    Judged against the data's own extremes, never the clock, so the answer is
    reproducible from the file. Reuses the RCA engine's completeness test rather
    than restating its reasoning.
    """
    tail_partial = not bucket_is_complete(max_ts, bucket_end(last_bucket, grain), detected_frequency)

    # The head is the mirror image: could an earlier observation, at this data's
    # own step, have landed inside the first bucket before min_ts did?
    arrival = FREQUENCY_TO_GRAIN.get((detected_frequency or "").lower())
    head_partial = (
        arrival is not None and shift_bucket(min_ts, arrival, -1) >= first_bucket
    )

    return head_partial, tail_partial


