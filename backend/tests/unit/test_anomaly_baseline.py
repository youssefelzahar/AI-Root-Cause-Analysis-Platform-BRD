"""Rolling baseline maths. No DuckDB, no database."""

import pytest

from app.analysis.anomaly.baseline import (
    build,
    mean_absolute_deviation,
    median,
    median_absolute_deviation,
    quartiles,
    trailing_window,
)
from app.analysis.anomaly.models import ScaleBasis

STEADY = [1000.0, 1020.0, 980.0, 1010.0, 990.0, 1005.0]


def test_the_window_holds_only_the_periods_before_the_one_being_scored():
    values: list[float | None] = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert trailing_window(values, 3, 12) == [1.0, 2.0, 3.0]


def test_the_window_slides_so_periods_older_than_it_drop_out():
    values: list[float | None] = [float(v) for v in range(10)]
    assert trailing_window(values, 8, 3) == [5.0, 6.0, 7.0]


def test_the_first_period_has_no_window_at_all():
    assert trailing_window([1.0, 2.0], 0, 12) == []


def test_missing_periods_are_dropped_from_the_window_rather_than_filled():
    """A month with no rows is not a month of zero, and inventing one would
    drag the median toward a value that was never observed."""
    values: list[float | None] = [10.0, None, 12.0, None, 14.0]
    assert trailing_window(values, 5, 12) == [10.0, 12.0, 14.0]


def test_a_window_shorter_than_the_minimum_produces_no_baseline():
    assert build([1.0, 2.0, 3.0, 4.0], minimum=5) is None


def test_a_window_at_the_minimum_produces_a_baseline():
    result = build([1.0, 2.0, 3.0, 4.0, 5.0], minimum=5)
    assert result is not None
    assert result.observations_used == 5


def test_the_baseline_is_the_median_and_the_median_absolute_deviation():
    result = build(STEADY, minimum=5)
    assert result is not None
    assert result.expected_value == pytest.approx(1002.5)
    assert result.scale == pytest.approx(10.0)
    assert result.scale_basis is ScaleBasis.MAD


def test_one_extreme_prior_does_not_move_the_median_baseline():
    """The whole reason for median and MAD over mean and standard deviation:
    a contaminating value must not become part of what counts as normal."""
    clean = build(STEADY, minimum=5)
    contaminated = build([*STEADY[:-1], 500_000.0], minimum=5)
    assert clean is not None and contaminated is not None
    assert contaminated.expected_value == pytest.approx(clean.expected_value, rel=0.02)


def test_a_window_where_most_values_are_identical_falls_back_to_the_mean_deviation():
    """MAD is zero whenever over half the window shares one value, which is the
    common case for a flat KPI - not a rare one."""
    result = build([10.0, 10.0, 10.0, 10.0, 12.0], minimum=5)
    assert result is not None
    assert result.scale_basis is ScaleBasis.MEAN_ABSOLUTE_DEVIATION
    assert result.scale > 0


def test_a_window_with_no_dispersion_at_all_is_reported_as_degenerate():
    result = build([10.0] * 6, minimum=5)
    assert result is not None
    assert result.scale_basis is ScaleBasis.DEGENERATE
    assert result.scale == 0.0


def test_the_median_absolute_deviation_is_the_median_of_the_distances():
    assert median_absolute_deviation([1.0, 2.0, 3.0, 4.0, 100.0], 3.0) == pytest.approx(1.0)


def test_the_mean_absolute_deviation_sees_a_value_the_median_deviation_cannot():
    values = [10.0, 10.0, 10.0, 10.0, 12.0]
    assert median_absolute_deviation(values, 10.0) == 0.0
    assert mean_absolute_deviation(values, 10.0) == pytest.approx(0.4)


def test_the_median_of_an_even_window_averages_the_two_middle_values():
    assert median([1.0, 2.0, 3.0, 4.0]) == pytest.approx(2.5)


def test_quartiles_interpolate_between_order_statistics():
    p25, p75 = quartiles([1.0, 2.0, 3.0, 4.0, 5.0])
    assert p25 == pytest.approx(2.0)
    assert p75 == pytest.approx(4.0)


def test_quartiles_of_a_single_value_are_that_value():
    assert quartiles([7.0]) == (7.0, 7.0)


def test_negative_values_are_summarised_like_any_other():
    result = build([-100.0, -102.0, -98.0, -101.0, -99.0], minimum=5)
    assert result is not None
    assert result.expected_value == pytest.approx(-100.0)
    assert result.scale > 0
