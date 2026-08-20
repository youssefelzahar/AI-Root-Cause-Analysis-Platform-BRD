"""Period resolution: turning a comparison setting into two concrete windows."""

from datetime import datetime

import pytest

from app.analysis.rca.models import Grain
from app.analysis.rca.period_analysis import resolve_grain, resolve_periods
from app.core.exceptions import ValidationError
from app.db.models.enums import ComparisonPeriod


def _resolve(comparison, *, min_ts, max_ts, frequency=None, config=None):
    return resolve_periods(
        comparison=comparison,
        comparison_config=config,
        min_ts=min_ts,
        max_ts=max_ts,
        detected_frequency=frequency,
        )


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        ("daily", Grain.DAY),
        ("weekly", Grain.WEEK),
        ("monthly", Grain.MONTH),
        ("quarterly", Grain.QUARTER),
        ("yearly", Grain.YEAR),
        (None, Grain.EQUAL_SPAN),
        ("nonsense", Grain.EQUAL_SPAN),
    ],
)
def test_previous_period_takes_its_grain_from_the_profiled_frequency(frequency, expected):
    assert resolve_grain(ComparisonPeriod.PREVIOUS_PERIOD, frequency) is expected


@pytest.mark.parametrize(
    ("comparison", "expected"),
    [
        (ComparisonPeriod.PREVIOUS_MONTH, Grain.MONTH),
        (ComparisonPeriod.PREVIOUS_QUARTER, Grain.QUARTER),
        (ComparisonPeriod.PREVIOUS_YEAR, Grain.YEAR),
    ],
)
def test_named_comparisons_ignore_the_detected_frequency(comparison, expected):
    assert resolve_grain(comparison, "daily") is expected


def test_windows_are_half_open_so_a_boundary_row_belongs_to_one_period():
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_MONTH,
        min_ts=datetime(2026, 5, 1),
        max_ts=datetime(2026, 7, 15),
        frequency="monthly",
    )
    assert resolution.previous.end == resolution.current.start
    assert resolution.current.start == datetime(2026, 7, 1)
    assert resolution.current.end == datetime(2026, 8, 1)
    assert resolution.previous.start == datetime(2026, 6, 1)


def test_monthly_data_stamped_mid_month_is_treated_as_a_complete_month():
    """Monthly rows land wherever the reporting date falls.

    Requiring a row on the 31st before calling July complete would throw the
    newest month away on every monthly dataset.
    """
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_MONTH,
        min_ts=datetime(2026, 6, 15),
        max_ts=datetime(2026, 7, 15),
        frequency="monthly",
    )
    assert resolution.current.start == datetime(2026, 7, 1)
    assert resolution.excluded_partial_period is None


def test_daily_data_stopping_mid_month_excludes_the_incomplete_month():
    """The false-collapse guard: a half-collected July is not a 50% drop."""
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_MONTH,
        min_ts=datetime(2026, 5, 1),
        max_ts=datetime(2026, 7, 12),
        frequency="daily",
    )
    assert resolution.excluded_partial_period is not None
    assert resolution.excluded_partial_period.start == datetime(2026, 7, 1)
    assert resolution.current.start == datetime(2026, 6, 1)
    assert resolution.previous.start == datetime(2026, 5, 1)


def test_daily_data_running_to_month_end_keeps_that_month():
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_MONTH,
        min_ts=datetime(2026, 5, 1),
        max_ts=datetime(2026, 7, 31),
        frequency="daily",
    )
    assert resolution.excluded_partial_period is None
    assert resolution.current.start == datetime(2026, 7, 1)


def test_a_partial_month_is_kept_when_there_is_no_earlier_month_to_fall_back_to():
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_MONTH,
        min_ts=datetime(2026, 7, 1),
        max_ts=datetime(2026, 7, 12),
        frequency="daily",
    )
    assert resolution.current.start == datetime(2026, 7, 1)


def test_an_unknown_frequency_splits_the_range_in_half_instead_of_guessing():
    """The profiler already failed to find a frequency, so inventing a calendar
    grain here would be fabrication. Halving is deterministic and disclosed."""
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_PERIOD,
        min_ts=datetime(2026, 1, 1),
        max_ts=datetime(2026, 1, 11),
        frequency=None,
    )
    assert resolution.grain is Grain.EQUAL_SPAN
    assert resolution.strategy == "equal_span_split"
    assert resolution.previous.start == datetime(2026, 1, 1)
    assert resolution.previous.end == resolution.current.start
    # Every row is inside one window or the other.
    assert resolution.current.end > datetime(2026, 1, 11)


def test_quarter_and_year_comparisons_snap_to_calendar_boundaries():
    quarterly = _resolve(
        ComparisonPeriod.PREVIOUS_QUARTER,
        min_ts=datetime(2026, 1, 1),
        max_ts=datetime(2026, 8, 20),
        frequency="quarterly",
    )
    assert quarterly.current.start == datetime(2026, 7, 1)
    assert quarterly.previous.start == datetime(2026, 4, 1)

    yearly = _resolve(
        ComparisonPeriod.PREVIOUS_YEAR,
        min_ts=datetime(2024, 1, 1),
        max_ts=datetime(2026, 8, 20),
        frequency="yearly",
    )
    assert yearly.current.start == datetime(2026, 1, 1)
    assert yearly.previous.start == datetime(2025, 1, 1)


def test_weeks_start_on_monday():
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_PERIOD,
        min_ts=datetime(2026, 8, 1),
        max_ts=datetime(2026, 8, 20),  # a Thursday
        frequency="weekly",
    )
    assert resolution.current.start.weekday() == 0
    assert resolution.previous.start.weekday() == 0


# --- custom windows -----------------------------------------------------------


def test_a_custom_comparison_uses_the_windows_it_is_given():
    resolution = _resolve(
        ComparisonPeriod.CUSTOM,
        min_ts=datetime(2026, 1, 1),
        max_ts=datetime(2026, 8, 1),
        config={
            "current_start": "2026-07-01",
            "current_end": "2026-07-31",
            "previous_start": "2026-06-01",
            "previous_end": "2026-06-30",
        },
    )
    assert resolution.grain is Grain.CUSTOM
    # end is inclusive on the wire, exclusive internally.
    assert resolution.current.end == datetime(2026, 8, 1)
    assert resolution.previous.end == datetime(2026, 7, 1)


def test_a_custom_comparison_derives_the_preceding_window_of_equal_length():
    resolution = _resolve(
        ComparisonPeriod.CUSTOM,
        min_ts=datetime(2026, 1, 1),
        max_ts=datetime(2026, 8, 1),
        config={"current_start": "2026-07-11", "current_end": "2026-07-20"},
    )
    assert resolution.previous.end == datetime(2026, 7, 11)
    assert resolution.previous.start == datetime(2026, 7, 1)


@pytest.mark.parametrize(
    "config",
    [
        {"current_end": "2026-07-31"},
        {"current_start": "2026-07-01"},
        {"current_start": "2026-07-31", "current_end": "2026-07-01"},
        {"current_start": "not-a-date", "current_end": "2026-07-31"},
        {
            "current_start": "2026-07-01",
            "current_end": "2026-07-31",
            "previous_start": "2026-06-01",
            "previous_end": "2026-07-15",  # overlaps the current window
        },
    ],
)
def test_a_malformed_custom_comparison_is_a_caller_error(config):
    with pytest.raises(ValidationError) as excinfo:
        _resolve(
            ComparisonPeriod.CUSTOM,
            min_ts=datetime(2026, 1, 1),
            max_ts=datetime(2026, 8, 1),
            config=config,
        )
    assert excinfo.value.code == "RCA_CUSTOM_PERIOD_INVALID"


def test_the_anchor_comes_from_the_data_not_the_clock():
    """Investigations must be reproducible from the file alone."""
    resolution = _resolve(
        ComparisonPeriod.PREVIOUS_MONTH,
        min_ts=datetime(2019, 1, 1),
        max_ts=datetime(2019, 4, 20),
        frequency="monthly",
    )
    assert resolution.anchor == datetime(2019, 4, 20)
    assert resolution.current.start == datetime(2019, 4, 1)
