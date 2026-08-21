"""Score, severity, direction and deviation. Pure maths, no database."""

import pytest

from app.analysis.anomaly.constants import MAX_ANOMALY_SCORE
from app.analysis.anomaly.models import Baseline, Direction, ScaleBasis, Severity
from app.analysis.anomaly.scoring import (
    deviation_percent,
    direction,
    is_anomalous,
    score,
    severity,
)


def _mad(expected: float, scale: float) -> Baseline:
    return Baseline(expected, scale, ScaleBasis.MAD, 12)


def _mean_ad(expected: float, scale: float) -> Baseline:
    return Baseline(expected, scale, ScaleBasis.MEAN_ABSOLUTE_DEVIATION, 12)


def test_the_score_is_the_modified_z_score_of_the_deviation():
    """0.6745 * (600 - 1002.5) / 10 - the documented interpretation, checked."""
    assert score(600.0, _mad(1002.5, 10.0)) == pytest.approx(-27.1486, abs=1e-4)


def test_a_value_sitting_on_its_baseline_scores_zero():
    assert score(1000.0, _mad(1000.0, 10.0)) == 0.0


def test_the_score_is_signed_so_direction_survives_it():
    assert score(1100.0, _mad(1000.0, 10.0)) > 0
    assert score(900.0, _mad(1000.0, 10.0)) < 0


def test_the_mean_deviation_fallback_uses_its_own_scaling_constant():
    """0.7979, not 0.6745. Reusing the MAD constant would shrink every fallback
    score by about 15% and quietly under-report a flat KPI that steps."""
    assert score(112.0, _mean_ad(100.0, 10.0)) == pytest.approx(0.9575, abs=1e-4)


def test_a_departure_from_a_history_with_no_dispersion_scores_at_the_cap():
    degenerate = Baseline(1000.0, 0.0, ScaleBasis.DEGENERATE, 12)
    assert score(1400.0, degenerate) == MAX_ANOMALY_SCORE
    assert score(600.0, degenerate) == -MAX_ANOMALY_SCORE


def test_matching_a_history_with_no_dispersion_is_not_an_anomaly():
    degenerate = Baseline(1000.0, 0.0, ScaleBasis.DEGENERATE, 12)
    assert score(1000.0, degenerate) == 0.0
    assert not is_anomalous(0.0)


def test_an_extreme_score_is_capped_rather_than_returned_unbounded():
    assert score(1e12, _mad(0.0, 1e-6)) == MAX_ANOMALY_SCORE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, Severity.NORMAL),
        (3.49, Severity.NORMAL),
        (3.5, Severity.LOW),
        (4.9, Severity.LOW),
        (5.0, Severity.MEDIUM),
        (7.9, Severity.MEDIUM),
        (8.0, Severity.HIGH),
        (11.9, Severity.HIGH),
        (12.0, Severity.CRITICAL),
        (-27.15, Severity.CRITICAL),
    ],
)
def test_severity_bands_are_keyed_to_the_magnitude_of_the_score(value, expected):
    assert severity(value) is expected


def test_an_unscored_period_is_normal_rather_than_an_anomaly():
    assert severity(None) is Severity.NORMAL
    assert not is_anomalous(None)


def test_the_anomaly_cutoff_is_the_published_threshold():
    assert not is_anomalous(3.49)
    assert is_anomalous(3.5)
    assert is_anomalous(-3.5)


@pytest.mark.parametrize(
    ("deviation", "expected"),
    [
        (150.0, Direction.UPWARD),
        (-150.0, Direction.DOWNWARD),
        (0.0, Direction.NONE),
        (None, Direction.NONE),
        (1e-12, Direction.NONE),
    ],
)
def test_direction_reports_which_way_the_period_departed(deviation, expected):
    assert direction(deviation) is expected


def test_percentage_deviation_is_signed_against_the_baseline():
    percent, reason = deviation_percent(800.0, 1000.0)
    assert percent == pytest.approx(-20.0)
    assert reason is None


def test_a_zero_baseline_yields_no_percentage_and_says_why():
    """Never a division by zero, and never a silently absent field."""
    percent, reason = deviation_percent(500.0, 0.0)
    assert percent is None
    assert reason == "zero_baseline"


def test_a_negative_kpi_falling_further_reads_as_a_decrease():
    """Dividing by the magnitude, not the signed value: -100 to -150 is -50%,
    not the +50% a naive ratio would report as an improvement."""
    percent, _ = deviation_percent(-150.0, -100.0)
    assert percent == pytest.approx(-50.0)


def test_very_large_and_very_small_values_score_on_the_same_scale():
    big = score(6e11, _mad(1.0025e12, 1e10))
    small = score(6e-9, _mad(1.0025e-8, 1e-10))
    assert big == pytest.approx(small, rel=1e-6)
