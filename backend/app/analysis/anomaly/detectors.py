"""Detection strategies. Pure maths over an already-built time series.

A detector takes the raw series - one value per calendar period, with gaps
marked - and returns the same series with each observation judged. Every
detector walks the series in order and looks only backwards, so adding one never
changes the causality guarantee.

The abstraction is deliberately one method wide. A registry plus a Protocol is
enough to add a seasonal or forecast-residual detector later; anything larger
would be scaffolding for requirements nobody has yet.
"""

from dataclasses import replace
from typing import Any, Protocol

from app.analysis.anomaly import baseline as baseline_maths
from app.analysis.anomaly import scoring
from app.analysis.anomaly.constants import (
    ABS_EPSILON,
    IQR_FENCE_MULTIPLIER,
    LOW_THRESHOLD,
    MAX_ANOMALY_SCORE,
)
from app.analysis.anomaly.models import (
    Baseline,
    Direction,
    Observation,
    ObservationStatus,
    ScaleBasis,
)


class Detector(Protocol):
    """Judge every observation in a series against the ones before it."""

    name: str

    def evaluate(
        self,
        series: list[Observation],
        *,
        window: int,
        minimum: int,
    ) -> list[Observation]: ...


def _values(series: list[Observation]) -> list[float | None]:
    """The baseline's view of the series.

    A partial boundary period is masked to None here as well as skipped for
    scoring: a half-collected month is not a low month, and letting it into the
    window would drag the median down and make the *next* period look high.
    """
    return [observation.value if observation.is_scoreable else None for observation in series]


def _unscored(observation: Observation) -> Observation:
    """Carry a period through with no verdict attached.

    Used for missing periods, partial boundary periods and the start of the
    series. The status says which, so "we could not tell" is never rendered as
    "normal".
    """
    if observation.status is ObservationStatus.PARTIAL:
        return observation
    status = (
        ObservationStatus.MISSING
        if observation.value is None
        else ObservationStatus.INSUFFICIENT_HISTORY
    )
    return _replace(observation, status=status)


def _replace(observation: Observation, **changes: Any) -> Observation:
    """``dataclasses.replace`` under a shorter name, for readability below."""
    return replace(observation, **changes)


def _judged(observation: Observation, value: float, built: Baseline, raw_score: float) -> Observation:
    """Attach a score and everything derived from it to one observation."""
    deviation = value - built.expected_value
    percent, reason = scoring.deviation_percent(value, built.expected_value)
    return _replace(
        observation,
        status=ObservationStatus.EVALUATED,
        baseline=built,
        absolute_deviation=deviation,
        percentage_deviation=percent,
        percentage_unavailable_reason=reason,
        anomaly_score=raw_score,
        severity=scoring.severity(raw_score),
        direction=scoring.direction(deviation),
        is_anomaly=scoring.is_anomalous(raw_score),
    )


class RobustZScoreDetector:
    """Modified z-score against a trailing median and MAD.

    The default. Robust to the anomaly contaminating its own baseline, cheap,
    and the score carries a documented interpretation - see ``scoring``.
    """

    name = "robust_zscore"

    def evaluate(
        self,
        series: list[Observation],
        *,
        window: int,
        minimum: int,
    ) -> list[Observation]:
        values = _values(series)
        judged: list[Observation] = []

        for index, observation in enumerate(series):
            value = observation.value
            if value is None or not observation.is_scoreable:
                judged.append(_unscored(observation))
                continue

            priors = baseline_maths.trailing_window(values, index, window)
            built = baseline_maths.build(priors, minimum=minimum)
            if built is None:
                judged.append(_unscored(observation))
                continue

            judged.append(_judged(observation, value, built, scoring.score(value, built)))

        return judged


class IQRDetector:
    """Tukey fences over the trailing window.

    An alternative reading for series whose dispersion is asymmetric: the fences
    are built from the quartiles independently, so a long upper tail does not
    widen the lower fence. Distance past the nearer fence is expressed in
    IQR-multiples and then rescaled onto the same severity ladder, so a score
    from either detector means the same thing to the reader.
    """

    name = "iqr"

    def evaluate(
        self,
        series: list[Observation],
        *,
        window: int,
        minimum: int,
    ) -> list[Observation]:
        values = _values(series)
        judged: list[Observation] = []

        for index, observation in enumerate(series):
            value = observation.value
            if value is None or not observation.is_scoreable:
                judged.append(_unscored(observation))
                continue

            priors = baseline_maths.trailing_window(values, index, window)
            if len(priors) < minimum:
                judged.append(_unscored(observation))
                continue

            p25, p75 = baseline_maths.quartiles(priors)
            iqr = p75 - p25
            center = baseline_maths.median(priors)
            lower = p25 - IQR_FENCE_MULTIPLIER * iqr
            upper = p75 + IQR_FENCE_MULTIPLIER * iqr

            if iqr <= ABS_EPSILON:
                # No interquartile spread: the fences collapse onto the
                # quartiles, so fall back to the same degenerate handling the
                # z-score detector uses rather than declaring every point an
                # outlier by a hair.
                built = Baseline(center, 0.0, ScaleBasis.DEGENERATE, len(priors))
                judged.append(_judged(observation, value, built, scoring.score(value, built)))
                continue

            built = Baseline(center, iqr, ScaleBasis.IQR, len(priors))
            judged.append(_judged(observation, value, built, _iqr_score(value, lower, upper, iqr)))

        return judged


def _iqr_score(value: float, lower: float, upper: float, iqr: float) -> float:
    """Signed distance past the nearer fence, in IQR multiples, on the z ladder.

    Inside the fences the score is 0 - the point is unremarkable and the exact
    position within the box carries no information about unusualness. Outside,
    each further IQR is scaled so that landing exactly on a fence reads as the
    outlier cutoff, which keeps one severity ladder valid for both detectors.
    """
    if value > upper:
        excess = (value - upper) / iqr
    elif value < lower:
        excess = -(lower - value) / iqr
    else:
        return 0.0

    scaled = (abs(excess) + 1.0) * LOW_THRESHOLD
    scaled = min(scaled, MAX_ANOMALY_SCORE)
    return scaled if excess > 0 else -scaled


DETECTORS: dict[str, Detector] = {
    RobustZScoreDetector.name: RobustZScoreDetector(),
    IQRDetector.name: IQRDetector(),
}

DEFAULT_METHOD = RobustZScoreDetector.name


def get_detector(method: str) -> Detector | None:
    """The named detector, or None so the caller can raise with its own code."""
    return DETECTORS.get(method)


def summarise(observation: Observation | None, kpi_name: str) -> str:
    """One sentence describing the latest verdict, in the reader's terms."""
    if observation is None:
        return f"There is not enough history to judge whether {kpi_name} is behaving unusually."
    if observation.status is ObservationStatus.MISSING:
        return f"The most recent period has no {kpi_name} data."
    if observation.status is ObservationStatus.INSUFFICIENT_HISTORY:
        return f"There is not enough history to judge whether {kpi_name} is behaving unusually."
    if not observation.is_anomaly:
        return f"{kpi_name} is within its normal range."

    way = "above" if observation.direction is Direction.UPWARD else "below"
    band = observation.severity.value.lower()
    if observation.percentage_deviation is not None:
        return (
            f"{kpi_name} is {abs(observation.percentage_deviation):.1f}% {way} its recent "
            f"baseline - a {band} anomaly."
        )
    return f"{kpi_name} is {way} its recent baseline - a {band} anomaly."
