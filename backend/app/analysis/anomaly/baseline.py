"""Rolling baseline maths. No DuckDB, no database, no I/O.

The baseline answers "what should this period have looked like, judging only by
the periods before it?". Two properties matter more than sophistication:

* **Causal.** Only strictly-prior observations are used. A baseline that peeks
  at the point it is judging, or at later points, will always find that point
  unremarkable.
* **Robust.** The median and the median absolute deviation have a 50% breakdown
  point: half the window would have to be anomalous before they move. A rolling
  *mean* and *standard deviation* do not have that property - a single large
  outlier inflates the standard deviation enough to hide itself, which is
  exactly the failure this feature exists to avoid.

Operating on the aggregated series (one value per period) rather than raw rows
is what keeps this in Python honestly: a decade of daily data is a few thousand
floats, not a dataset.
"""

import statistics

from app.analysis.anomaly.constants import SCALE_RELATIVE_EPSILON
from app.analysis.anomaly.models import Baseline, ScaleBasis


def scale_is_negligible(scale: float, reference: float) -> bool:
    """Is this dispersion indistinguishable from none, given the data's size?

    Judged relative to the baseline's own magnitude rather than against a fixed
    epsilon. A KPI held in fractions can legitimately have a MAD of 1e-10, and
    an absolute floor would declare that series flat and cap every score it ever
    produces. A MAD of 1e-10 around a baseline of 1,000 really is float noise.

    A scale of exactly zero is always negligible, including around a zero
    baseline - that is the genuinely constant case.
    """
    if scale <= 0.0:
        return True
    return scale <= SCALE_RELATIVE_EPSILON * abs(reference)


def median(values: list[float]) -> float:
    """The middle value, averaging the two middles for an even count."""
    return statistics.median(values)


def median_absolute_deviation(values: list[float], center: float) -> float:
    """median(|x - center|) - the robust analogue of the standard deviation."""
    return statistics.median([abs(value - center) for value in values])


def mean_absolute_deviation(values: list[float], center: float) -> float:
    """Fallback scale for when the MAD collapses to zero.

    A window like ``[10, 10, 10, 10, 12]`` has a MAD of 0 because more than half
    its values are identical, yet it is plainly not dispersion-free. The mean
    absolute deviation still sees the 12, so it keeps the score finite and
    ordered instead of forcing a divide-by-zero branch.
    """
    if not values:
        return 0.0
    return sum(abs(value - center) for value in values) / len(values)


def trailing_window(values: list[float | None], index: int, window: int) -> list[float]:
    """The non-missing observations strictly before ``index``.

    Missing periods are dropped rather than interpolated or zero-filled: a month
    with no rows is not a month of zero revenue, and inventing a value for it
    would drag the median toward a number that was never observed.
    """
    start = max(0, index - window)
    return [value for value in values[start:index] if value is not None]


def build(values: list[float], *, minimum: int) -> Baseline | None:
    """Summarise a window of prior observations, or None if it is too short.

    Returning None rather than a low-confidence Baseline is deliberate: the
    caller must then report INSUFFICIENT_HISTORY, which is a different and more
    honest statement than "normal".
    """
    if len(values) < minimum:
        return None

    center = median(values)
    mad = median_absolute_deviation(values, center)
    if not scale_is_negligible(mad, center):
        return Baseline(center, mad, ScaleBasis.MAD, len(values))

    fallback = mean_absolute_deviation(values, center)
    if not scale_is_negligible(fallback, center):
        return Baseline(center, fallback, ScaleBasis.MEAN_ABSOLUTE_DEVIATION, len(values))

    # Every value in the window is identical. There is no scale to divide by,
    # and the caller decides what a departure from a perfectly flat history
    # means - see scoring.score().
    return Baseline(center, 0.0, ScaleBasis.DEGENERATE, len(values))


def quartiles(values: list[float]) -> tuple[float, float]:
    """(p25, p75) by linear interpolation between order statistics.

    Hand-rolled rather than ``statistics.quantiles``: that function needs at
    least two points and uses a different (n+1) plotting position, and the IQR
    fences here should match the ones the profiler already reports so the two
    features do not disagree about what an outlier is.
    """
    ordered = sorted(values)
    return _percentile(ordered, 0.25), _percentile(ordered, 0.75)


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
