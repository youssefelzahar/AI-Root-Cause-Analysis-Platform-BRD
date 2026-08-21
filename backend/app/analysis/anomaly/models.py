"""Value types for the anomaly engine.

Frozen dataclasses rather than Pydantic: this package is pure analysis and must
stay importable without the web layer. ``app.schemas.anomaly`` maps these onto
the wire contract at the API boundary.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.analysis.rca.models import Grain
from app.db.models.enums import Aggregation


class Severity(str, Enum):
    """How far outside normal an observation sits.

    Deliberately not ``rca.ranking.severity``: that ladder is driven by percent
    change and has no NORMAL rung, because every RCA driver is by definition
    already interesting. Here most observations are ordinary and must be
    sayable as such.
    """

    NORMAL = "NORMAL"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Direction(str, Enum):
    """Which way the observation departed from its baseline."""

    UPWARD = "UPWARD"
    DOWNWARD = "DOWNWARD"
    NONE = "NONE"


class ObservationStatus(str, Enum):
    """Why an observation does or does not carry a score."""

    # Scored against a baseline built from enough history.
    EVALUATED = "EVALUATED"
    # Too few prior observations to judge this one. Not an anomaly, not normal.
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    # The period exists on the calendar but the dataset has no rows in it.
    MISSING = "MISSING"
    # A boundary period that is still being collected. Its total is not
    # comparable with a whole period's, so it is shown but never scored.
    PARTIAL = "PARTIAL"


class ReportStatus(str, Enum):
    """The outcome of the run as a whole."""

    OK = "OK"
    NO_DATA = "NO_DATA"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    NO_TIME_COLUMN = "NO_TIME_COLUMN"


class ScaleBasis(str, Enum):
    """Which dispersion estimate the score was divided by.

    Reported rather than assumed: a score computed against a fallback scale is
    weaker evidence than one computed against a real MAD, and the reader is
    entitled to know which they are looking at.
    """

    MAD = "mad"
    # MAD collapsed to zero (more than half the window identical), so the mean
    # absolute deviation stood in for it.
    MEAN_ABSOLUTE_DEVIATION = "mean_absolute_deviation"
    # Interquartile range, used by the IQR detector, which scores against its
    # own fences rather than through the z-score constants.
    IQR = "iqr"
    # Every value in the window is identical. There is no dispersion at all.
    DEGENERATE = "degenerate"


@dataclass(frozen=True)
class AnomalySpec:
    """Everything the engine needs, with no database or ORM in sight."""

    kpi_name: str
    measure_column: str
    aggregation: Aggregation
    time_column: str | None
    grain: Grain
    filters: tuple[dict[str, Any], ...] = ()
    method: str = "robust_zscore"
    baseline_window: int = 12
    min_baseline_observations: int = 5
    max_periods: int = 5_000
    # The profiler's reporting frequency for the time column. This is the rows'
    # *arrival* cadence, which is what decides whether a boundary bucket is
    # still being collected - not necessarily the grain the series is read at.
    detected_frequency: str | None = None
    # True when the grain was assumed rather than detected, so the engine can
    # say so instead of the caller having to infer it.
    grain_assumed: bool = False


@dataclass(frozen=True)
class Baseline:
    """What normal looked like for one observation, and how that was decided."""

    expected_value: float
    scale: float
    scale_basis: ScaleBasis
    observations_used: int


@dataclass(frozen=True)
class Observation:
    """One period of the KPI time series, with its verdict.

    ``value is None`` and ``status is MISSING`` mean the period had no rows -
    which is not the same as a KPI of zero, and is never scored.
    """

    period_start: datetime
    period_end: datetime
    value: float | None
    row_count: int = 0
    status: ObservationStatus = ObservationStatus.INSUFFICIENT_HISTORY
    baseline: Baseline | None = None
    absolute_deviation: float | None = None
    percentage_deviation: float | None = None
    # Why there is no percentage: "zero_baseline" when the expected value is
    # zero, so the field is absent for a stated reason rather than silently.
    percentage_unavailable_reason: str | None = None
    anomaly_score: float | None = None
    severity: Severity = Severity.NORMAL
    direction: Direction = Direction.NONE
    is_anomaly: bool = False

    @property
    def is_missing(self) -> bool:
        return self.status is ObservationStatus.MISSING

    @property
    def is_scoreable(self) -> bool:
        """A real, whole period holding a usable value."""
        return self.value is not None and self.status is not ObservationStatus.PARTIAL


@dataclass(frozen=True)
class Notice:
    """Something the reader must know to interpret the result correctly."""

    code: str
    severity: str  # "info" | "warning"
    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class Evidence:
    """What the report was built from, so a number can be traced to its rows."""

    total_rows: int = 0
    rows_in_series: int = 0
    unparsed_time_rows: int = 0
    unparsed_measure_rows: int = 0
    periods_observed: int = 0
    periods_missing: int = 0
    periods_evaluated: int = 0
    statements_executed: int = 0
    duration_ms: int = 0


@dataclass(frozen=True)
class AnomalyReport:
    """The engine's whole answer."""

    status: ReportStatus
    kpi_name: str
    aggregation: Aggregation
    grain: Grain
    method: str
    series: tuple[Observation, ...] = ()
    anomalies: tuple[Observation, ...] = ()
    # The most recent evaluated period - what "is the KPI unusual right now?"
    # actually asks. None when nothing could be evaluated.
    latest: Observation | None = None
    evidence: Evidence = field(default_factory=Evidence)
    notices: tuple[Notice, ...] = ()
    limitations: tuple[str, ...] = ()
    summary: str = ""
