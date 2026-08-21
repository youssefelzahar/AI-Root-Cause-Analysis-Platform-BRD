"""Turning a deviation into a score, a severity and a direction. Pure maths.

The score is the **modified z-score**:

    score = 0.6745 * (actual - median) / MAD

``1.4826 * MAD`` is a consistent estimator of the standard deviation for
normally distributed data, and ``0.6745 = 1 / 1.4826``. So the score is on the
same scale as an ordinary z-score and reads as *"how many robust standard
deviations from normal this period is"* - a stated interpretation, not an
arbitrary index. ``|score| >= 3.5`` is Iglewicz & Hoaglin's published outlier
cutoff (NIST/SEMATECH e-Handbook 1.3.5.17), which is where NORMAL ends.
"""

from app.analysis.anomaly.baseline import scale_is_negligible
from app.analysis.anomaly.constants import (
    ABS_EPSILON,
    CRITICAL_THRESHOLD,
    HIGH_THRESHOLD,
    LOW_THRESHOLD,
    MAD_TO_SIGMA,
    MAX_ANOMALY_SCORE,
    MEAN_AD_TO_SIGMA,
    MEDIUM_THRESHOLD,
)
from app.analysis.anomaly.models import Baseline, Direction, ScaleBasis, Severity
from app.analysis.rca.contribution import percent_change

# Each dispersion estimate needs its own constant to land on the z-score scale.
# Dividing a mean-absolute-deviation by the MAD constant would silently shrink
# every fallback score by about 15%.
SCALE_CONSTANT = {
    ScaleBasis.MAD: MAD_TO_SIGMA,
    ScaleBasis.MEAN_ABSOLUTE_DEVIATION: MEAN_AD_TO_SIGMA,
}


def score(actual: float, baseline: Baseline) -> float:
    """The modified z-score of ``actual`` against ``baseline``.

    When the window has no dispersion at all (every prior value identical) there
    is no scale to divide by. Rather than return infinity or crash, a departure
    from a perfectly flat history is reported at the cap and an exact match at
    zero: with that history, *any* movement is maximally surprising, and that is
    the honest reading.
    """
    deviation = actual - baseline.expected_value

    if baseline.scale_basis is ScaleBasis.DEGENERATE or scale_is_negligible(
        baseline.scale, baseline.expected_value
    ):
        if scale_is_negligible(abs(deviation), baseline.expected_value):
            return 0.0
        return MAX_ANOMALY_SCORE if deviation > 0 else -MAX_ANOMALY_SCORE

    raw = SCALE_CONSTANT[baseline.scale_basis] * deviation / baseline.scale
    # Clamped, not dropped: past the cap the exact magnitude is noise, but the
    # ordering between two extreme periods still needs to hold.
    return max(-MAX_ANOMALY_SCORE, min(MAX_ANOMALY_SCORE, raw))


def severity(anomaly_score: float | None) -> Severity:
    """Which band the score falls in. NORMAL below the outlier cutoff."""
    if anomaly_score is None:
        return Severity.NORMAL
    magnitude = abs(anomaly_score)
    if magnitude >= CRITICAL_THRESHOLD:
        return Severity.CRITICAL
    if magnitude >= HIGH_THRESHOLD:
        return Severity.HIGH
    if magnitude >= MEDIUM_THRESHOLD:
        return Severity.MEDIUM
    if magnitude >= LOW_THRESHOLD:
        return Severity.LOW
    return Severity.NORMAL


def direction(deviation: float | None) -> Direction:
    """Which way the observation departed. NONE when it sits on the baseline."""
    if deviation is None or abs(deviation) <= ABS_EPSILON:
        return Direction.NONE
    return Direction.UPWARD if deviation > 0 else Direction.DOWNWARD


def deviation_percent(actual: float, expected: float) -> tuple[float | None, str | None]:
    """Percentage deviation from the baseline, or None with a reason.

    Delegates to the RCA engine's ``percent_change``: it already refuses a zero
    denominator (returning the reason ``"zero_baseline"``) and divides by
    ``abs(expected)`` so that a negative-valued KPI moving further negative
    reads as a decrease rather than an improvement. Duplicating that logic here
    would be two chances to get the sign convention wrong.
    """
    return percent_change(actual, expected)


def is_anomalous(anomaly_score: float | None) -> bool:
    """True once the score clears the outlier cutoff."""
    return anomaly_score is not None and abs(anomaly_score) >= LOW_THRESHOLD
