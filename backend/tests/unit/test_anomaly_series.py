"""Series SQL and calendar maths. Builds strings and grids; runs one DuckDB
query only to prove the Python grid and DuckDB's date_trunc agree.
"""

from datetime import datetime

import duckdb
import pytest

from app.analysis.anomaly.series import (
    build_series_sql,
    grain_for,
    grid_bounds,
    is_bucketable,
    partial_boundaries,
)
from app.analysis.rca.models import Grain
from app.db.models.enums import Aggregation

# --- grain resolution ---------------------------------------------------------


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        ("daily", Grain.DAY),
        ("weekly", Grain.WEEK),
        ("monthly", Grain.MONTH),
        ("quarterly", Grain.QUARTER),
        ("yearly", Grain.YEAR),
        ("MONTHLY", Grain.MONTH),
    ],
)
def test_a_profiled_frequency_maps_onto_a_reporting_grain(frequency, expected):
    assert grain_for(frequency, None) is expected


def test_an_undetected_frequency_yields_nothing_rather_than_a_guess():
    assert grain_for(None, None) is None
    assert grain_for("", None) is None
    assert grain_for("fortnightly", None) is None


def test_an_explicit_grain_overrides_the_profiled_frequency():
    """The profiler reports how often rows arrive, which for transactional data
    is daily whatever grain the business reads the KPI at."""
    assert grain_for("daily", Grain.MONTH) is Grain.MONTH


def test_the_two_rca_window_strategies_are_not_series_grains():
    """EQUAL_SPAN means "halve the observed range", which is meaningless for a
    continuous series - and every calendar helper raises on it."""
    assert not is_bucketable(Grain.EQUAL_SPAN)
    assert not is_bucketable(Grain.CUSTOM)
    assert grain_for("daily", Grain.EQUAL_SPAN) is None
    assert grain_for("daily", Grain.CUSTOM) is None


# --- the grid -----------------------------------------------------------------


def test_the_grid_spans_from_the_first_bucket_to_the_last():
    start, end, truncated = grid_bounds(
        datetime(2026, 1, 15), datetime(2026, 7, 15), Grain.MONTH, max_periods=100
    )
    assert start == datetime(2026, 1, 1)
    assert end == datetime(2026, 7, 1)
    assert truncated is False


def test_one_stray_far_future_timestamp_cannot_expand_the_grid_without_bound():
    """Two rows, one stamped year 9999, would otherwise build nearly three
    million empty daily buckets - all held in memory and serialised."""
    start, end, truncated = grid_bounds(
        datetime(2024, 1, 1), datetime(9999, 12, 31), Grain.DAY, max_periods=30
    )
    assert truncated is True
    assert end == datetime(9999, 12, 31)
    assert (end - start).days == 29


def test_capping_the_grid_keeps_the_most_recent_periods():
    start, end, truncated = grid_bounds(
        datetime(2000, 1, 1), datetime(2026, 1, 1), Grain.YEAR, max_periods=5
    )
    assert truncated is True
    assert end == datetime(2026, 1, 1)
    assert start == datetime(2022, 1, 1)


def test_a_single_bucket_is_a_valid_grid():
    start, end, truncated = grid_bounds(
        datetime(2026, 3, 4), datetime(2026, 3, 20), Grain.MONTH, max_periods=100
    )
    assert start == end == datetime(2026, 3, 1)
    assert truncated is False


# --- partial boundaries -------------------------------------------------------


def test_a_month_read_from_daily_rows_that_stop_midway_is_partial():
    """Otherwise a mid-month upload reports a critical collapse every time."""
    _, tail = partial_boundaries(
        datetime(2026, 1, 1), datetime(2026, 7, 1),
        datetime(2026, 1, 1), datetime(2026, 7, 14), Grain.MONTH, "daily",
    )
    assert tail is True


def test_a_month_read_from_daily_rows_that_run_to_the_end_is_complete():
    _, tail = partial_boundaries(
        datetime(2026, 1, 1), datetime(2026, 7, 1),
        datetime(2026, 1, 1), datetime(2026, 7, 31), Grain.MONTH, "daily",
    )
    assert tail is False


def test_monthly_rows_stamped_mid_month_do_not_make_the_month_partial():
    """Monthly data is typically stamped mid-period, so "the last row is not on
    the 31st" says nothing - treating it as partial would discard the newest
    period on every dataset."""
    _, tail = partial_boundaries(
        datetime(2026, 1, 1), datetime(2026, 7, 1),
        datetime(2026, 1, 15), datetime(2026, 7, 15), Grain.MONTH, "monthly",
    )
    assert tail is False


def test_a_first_month_that_starts_midway_is_partial():
    head, _ = partial_boundaries(
        datetime(2026, 1, 1), datetime(2026, 7, 1),
        datetime(2026, 1, 20), datetime(2026, 7, 31), Grain.MONTH, "daily",
    )
    assert head is True


def test_an_unknown_frequency_assumes_whole_periods():
    """Dropping a real period is a worse error than keeping a partial one, which
    the row counts in the response reveal anyway."""
    head, tail = partial_boundaries(
        datetime(2026, 1, 1), datetime(2026, 7, 1),
        datetime(2026, 1, 20), datetime(2026, 7, 14), Grain.MONTH, None,
    )
    assert head is False
    assert tail is False


# --- the SQL ------------------------------------------------------------------


def test_the_series_statement_buckets_at_the_resolved_grain():
    sql = build_series_sql(Aggregation.SUM, Grain.QUARTER, limit=100)
    assert "date_trunc('quarter', t)" in sql


def test_the_series_statement_casts_the_bucket_back_to_a_timestamp():
    """date_trunc narrows a TIMESTAMP to a DATE at month grain and coarser, and
    the calendar helpers all take a datetime."""
    assert "AS TIMESTAMP" in build_series_sql(Aggregation.SUM, Grain.MONTH, limit=10)


@pytest.mark.parametrize(
    ("aggregation", "fragment"),
    [
        (Aggregation.SUM, "sum(m)"),
        (Aggregation.AVG, "avg(m)"),
        (Aggregation.COUNT, "count(m)"),
        (Aggregation.COUNT_DISTINCT, "count(DISTINCT m)"),
        (Aggregation.MIN, "min(m)"),
        (Aggregation.MAX, "max(m)"),
        (Aggregation.MEDIAN, "median(m)"),
    ],
)
def test_every_aggregation_reaches_the_statement_rather_than_defaulting_to_sum(
    aggregation, fragment
):
    assert fragment in build_series_sql(aggregation, Grain.MONTH, limit=10)


def test_rows_with_no_readable_date_are_excluded_from_the_series():
    assert "t IS NOT NULL" in build_series_sql(Aggregation.SUM, Grain.MONTH, limit=10)


def test_the_limit_keeps_the_most_recent_periods_then_restores_order():
    sql = build_series_sql(Aggregation.SUM, Grain.MONTH, limit=25)
    assert "ORDER BY bucket DESC LIMIT 25" in sql
    assert sql.rstrip().endswith("ORDER BY bucket")


def test_an_unbucketable_grain_is_refused_rather_than_producing_bad_sql():
    with pytest.raises(ValueError):
        build_series_sql(Aggregation.SUM, Grain.EQUAL_SPAN, limit=10)


# --- the two calendars must agree ---------------------------------------------


@pytest.mark.parametrize(
    "grain", [Grain.DAY, Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.YEAR]
)
def test_the_python_grid_agrees_with_duckdbs_date_trunc(grain):
    """The series is bucketed in SQL and reindexed in Python. If the two ever
    disagree - say a DuckDB upgrade changes the first day of the week - real
    values would land in buckets the grid does not contain and silently vanish.
    """
    from app.analysis.rca.period_analysis import bucket_start

    con = duckdb.connect()
    moments = [
        datetime(2026, 1, 1),
        datetime(2026, 2, 14, 13, 45),
        datetime(2026, 7, 15, 23, 59),
        datetime(2026, 12, 31),
        datetime(2024, 2, 29, 6, 30),
    ]
    for moment in moments:
        duck = con.execute(
            f"SELECT CAST(date_trunc('{grain.value}', CAST(? AS TIMESTAMP)) AS TIMESTAMP)",
            [moment],
        ).fetchone()[0]
        assert duck == bucket_start(moment, grain), f"{grain.value} disagreed on {moment}"
