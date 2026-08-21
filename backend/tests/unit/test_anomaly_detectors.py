"""Detection over a built series. Pure maths, no DuckDB, no database.

The golden series is the spec's own example. Nothing here tells the detector
which period is unusual - the assertions are on what it found.
"""

from datetime import datetime

import pytest

from app.analysis.anomaly.detectors import (
    DETECTORS,
    IQRDetector,
    RobustZScoreDetector,
    get_detector,
    summarise,
)
from app.analysis.anomaly.models import (
    Direction,
    Observation,
    ObservationStatus,
    ScaleBasis,
    Severity,
)

GOLDEN = [1000.0, 1020.0, 980.0, 1010.0, 990.0, 1005.0, 600.0]


def _series(values: list[float | None], *, partial_tail: bool = False) -> list[Observation]:
    """A monthly series starting 2026-01, one observation per value."""
    observations = []
    for index, value in enumerate(values):
        year, month = 2026 + index // 12, index % 12 + 1
        end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
        status = ObservationStatus.MISSING if value is None else ObservationStatus.INSUFFICIENT_HISTORY
        if partial_tail and index == len(values) - 1:
            status = ObservationStatus.PARTIAL
        observations.append(
            Observation(
                period_start=datetime(year, month, 1),
                period_end=datetime(end_year, end_month, 1),
                value=value,
                row_count=0 if value is None else 1,
                status=status,
            )
        )
    return observations


def _evaluate(values: list[float | None], *, minimum: int = 5, window: int = 12, **kwargs):
    return RobustZScoreDetector().evaluate(
        _series(values, **kwargs), window=window, minimum=minimum
    )


def _anomalies(judged):
    return [o for o in judged if o.is_anomaly]


# --- the golden series --------------------------------------------------------


def test_the_golden_series_flags_exactly_one_period():
    assert len(_anomalies(_evaluate(GOLDEN))) == 1


def test_the_golden_series_flags_the_period_that_actually_collapsed():
    found = _anomalies(_evaluate(GOLDEN))[0]
    assert found.value == 600.0
    assert found.period_start == datetime(2026, 7, 1)


def test_the_golden_collapse_is_critical_and_downward():
    found = _anomalies(_evaluate(GOLDEN))[0]
    assert found.severity is Severity.CRITICAL
    assert found.direction is Direction.DOWNWARD


def test_the_golden_collapse_scores_the_documented_modified_z_score():
    found = _anomalies(_evaluate(GOLDEN))[0]
    assert found.anomaly_score == pytest.approx(-27.1486, abs=1e-4)
    assert found.baseline is not None
    assert found.baseline.expected_value == pytest.approx(1002.5)
    assert found.baseline.scale == pytest.approx(10.0)


def test_the_golden_collapse_reports_its_deviation_both_ways():
    found = _anomalies(_evaluate(GOLDEN))[0]
    assert found.absolute_deviation == pytest.approx(-402.5)
    assert found.percentage_deviation == pytest.approx(-40.15, abs=0.01)


def test_the_period_before_the_collapse_is_evaluated_and_normal():
    """Proves the detector is discriminating, not flagging the whole tail."""
    judged = _evaluate(GOLDEN)
    assert judged[5].status is ObservationStatus.EVALUATED
    assert judged[5].severity is Severity.NORMAL
    assert not judged[5].is_anomaly


def test_the_start_of_the_series_is_unscored_rather_than_called_normal():
    judged = _evaluate(GOLDEN)
    assert [o.status for o in judged[:5]] == [ObservationStatus.INSUFFICIENT_HISTORY] * 5
    assert all(o.anomaly_score is None for o in judged[:5])


# --- no anomaly ---------------------------------------------------------------


def test_a_steady_series_produces_no_anomalies():
    assert _anomalies(_evaluate([1000.0, 1020.0, 980.0, 1010.0, 990.0, 1005.0, 995.0])) == []


def test_a_perfectly_constant_series_produces_no_anomalies():
    assert _anomalies(_evaluate([1000.0] * 12)) == []


def test_a_kpi_that_is_zero_in_every_period_produces_no_anomalies():
    assert _anomalies(_evaluate([0.0] * 12)) == []


def test_a_linear_trend_alone_does_not_flag_every_period():
    """A trailing baseline lags a ramp, but the lag is the same size every step,
    so an in-trend period must not read as unusual."""
    assert _anomalies(_evaluate([1000.0 + 50 * i for i in range(24)])) == []


# --- anomalies ----------------------------------------------------------------


def test_a_strong_upward_spike_is_flagged_upward():
    found = _anomalies(_evaluate([100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 400.0]))
    assert len(found) == 1
    assert found[0].direction is Direction.UPWARD


def test_a_perfectly_flat_series_that_steps_is_still_flagged():
    """The case a plain MAD cannot see at all. Every prior is identical, so the
    MAD is zero and a naive modified z-score divides by zero - leaving the most
    obvious anomaly imaginable undetectable. Against a history with no spread
    whatever, any movement is maximally surprising, so the score caps."""
    found = _anomalies(_evaluate([1000.0] * 12 + [1400.0]))
    assert len(found) == 1
    assert found[0].direction is Direction.UPWARD
    assert found[0].severity is Severity.CRITICAL
    assert found[0].baseline is not None
    assert found[0].baseline.scale_basis is ScaleBasis.DEGENERATE


def test_a_mostly_flat_window_scores_against_the_mean_deviation():
    """Between "no spread" and "real spread": more than half the window shares
    one value, so the MAD is zero but the window is plainly not dispersion-free.
    Iglewicz & Hoaglin's second form keeps the score finite and ordered."""
    judged = _evaluate([1000.0] * 8 + [1400.0, 1000.0, 1000.0, 1000.0, 1700.0])
    scored = judged[-1]
    assert scored.baseline is not None
    assert scored.baseline.scale_basis is ScaleBasis.MEAN_ABSOLUTE_DEVIATION
    assert scored.anomaly_score is not None
    assert abs(scored.anomaly_score) < 1_000.0  # finite, not the cap


def test_a_level_shift_stops_being_flagged_once_it_fills_the_baseline():
    """Detecting unusual periods, not change points: a new level becomes normal."""
    judged = _evaluate([1000.0] * 12 + [1400.0] * 12)
    flagged = _anomalies(judged)
    assert flagged
    assert not judged[-1].is_anomaly


def test_a_negative_kpi_falling_further_is_flagged_downward():
    values = [-100.0, -102.0, -98.0, -101.0, -99.0, -100.0, -400.0]
    found = _anomalies(_evaluate(values))
    assert len(found) == 1
    assert found[0].direction is Direction.DOWNWARD
    assert found[0].percentage_deviation == pytest.approx(-300.0, abs=1.0)


def test_a_move_off_an_all_zero_history_is_flagged_without_a_percentage():
    found = _anomalies(_evaluate([0.0] * 12 + [5_000_000.0]))
    assert len(found) == 1
    assert found[0].severity is Severity.CRITICAL
    assert found[0].percentage_deviation is None
    assert found[0].percentage_unavailable_reason == "zero_baseline"


def test_values_in_the_billions_are_scored_like_values_below_one():
    big = _evaluate([1e9, 1.02e9, 0.98e9, 1.01e9, 0.99e9, 1.005e9, 0.6e9])
    small = _evaluate([1e-9, 1.02e-9, 0.98e-9, 1.01e-9, 0.99e-9, 1.005e-9, 0.6e-9])
    assert _anomalies(big)[0].anomaly_score == pytest.approx(
        _anomalies(small)[0].anomaly_score, rel=1e-6
    )


# --- degenerate input ---------------------------------------------------------


def test_an_empty_series_yields_nothing_rather_than_raising():
    assert _evaluate([]) == []


def test_a_single_observation_is_unscored():
    judged = _evaluate([1000.0])
    assert judged[0].status is ObservationStatus.INSUFFICIENT_HISTORY
    assert not judged[0].is_anomaly


def test_a_series_shorter_than_the_minimum_baseline_is_entirely_unscored():
    judged = _evaluate([1000.0, 1010.0, 990.0, 1005.0])
    assert all(o.status is ObservationStatus.INSUFFICIENT_HISTORY for o in judged)


def test_missing_periods_are_neither_scored_nor_used_as_history():
    """A gap must not become a zero in the baseline - that would drag the median
    down and make the next real period look unusually high."""
    judged = _evaluate([1000.0, 1005.0, None, 995.0, 1010.0, 990.0, 1000.0, 1002.0])
    assert judged[2].status is ObservationStatus.MISSING
    assert judged[2].anomaly_score is None
    assert _anomalies(judged) == []


def test_a_partial_boundary_period_is_shown_but_never_scored():
    """A half-collected period looks exactly like a collapse. Scoring it would
    report a critical anomaly every time someone uploaded data mid-month."""
    judged = _evaluate([1000.0] * 6 + [400.0], partial_tail=True)
    assert judged[-1].status is ObservationStatus.PARTIAL
    assert judged[-1].anomaly_score is None
    assert not judged[-1].is_anomaly


def test_a_partial_period_is_kept_out_of_later_baselines_too():
    judged = _evaluate([1000.0] * 7 + [1000.0], partial_tail=True)
    assert all(not o.is_anomaly for o in judged)


# --- the window ---------------------------------------------------------------


def test_only_the_periods_inside_the_window_inform_a_baseline():
    """An old regime must leave the baseline once the window has moved past it."""
    judged = _evaluate([1.0] * 6 + [1000.0] * 6 + [1000.0], window=6)
    assert not judged[-1].is_anomaly


def test_the_baseline_never_includes_the_period_it_judges():
    judged = _evaluate(GOLDEN)
    collapse = _anomalies(judged)[0]
    assert collapse.baseline is not None
    assert collapse.baseline.observations_used == 6


# --- the iqr detector ---------------------------------------------------------


def test_the_iqr_detector_finds_the_same_collapse():
    judged = IQRDetector().evaluate(_series(GOLDEN), window=12, minimum=5)
    found = _anomalies(judged)
    assert len(found) == 1
    assert found[0].value == 600.0
    assert found[0].direction is Direction.DOWNWARD


def test_the_iqr_detector_leaves_a_steady_series_alone():
    judged = IQRDetector().evaluate(
        _series([1000.0, 1020.0, 980.0, 1010.0, 990.0, 1005.0, 995.0]), window=12, minimum=5
    )
    assert _anomalies(judged) == []


def test_the_iqr_detector_reports_which_scale_it_used():
    judged = IQRDetector().evaluate(_series(GOLDEN), window=12, minimum=5)
    collapse = _anomalies(judged)[0]
    assert collapse.baseline is not None
    assert collapse.baseline.scale_basis is ScaleBasis.IQR


# --- registry -----------------------------------------------------------------


def test_both_detectors_are_reachable_by_name():
    assert set(DETECTORS) == {"robust_zscore", "iqr"}
    assert get_detector("robust_zscore") is not None
    assert get_detector("iqr") is not None


def test_an_unknown_method_is_not_silently_substituted():
    assert get_detector("seasonal") is None
    assert get_detector("nonsense") is None


# --- the summary sentence -----------------------------------------------------


def test_the_summary_names_the_deviation_and_the_severity():
    collapse = _anomalies(_evaluate(GOLDEN))[0]
    text = summarise(collapse, "Revenue")
    assert "Revenue" in text
    assert "40.1% below" in text
    assert "critical" in text


def test_the_summary_says_so_when_nothing_is_unusual():
    judged = _evaluate([1000.0, 1020.0, 980.0, 1010.0, 990.0, 1005.0, 995.0])
    assert "within its normal range" in summarise(judged[-1], "Revenue")


def test_the_summary_says_so_when_there_is_not_enough_history():
    assert "not enough history" in summarise(None, "Revenue")
