"""Request and response contracts for anomaly detection.

Nothing here is ORM-backed, so no schema carries ``from_attributes``: the engine
returns frozen dataclasses and ``to_report`` maps them at the API boundary. That
mapping is what keeps Pydantic out of ``app.analysis``.

Wording is deliberate: an observation is *unusual compared with its own history*.
The engine measures departure from a baseline; it does not establish that
anything went wrong, and a flagged period may be a very good month.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.analysis.anomaly.constants import (
    BASELINE_WINDOW,
    CRITICAL_THRESHOLD,
    HIGH_THRESHOLD,
    LOW_THRESHOLD,
    MEDIUM_THRESHOLD,
    MIN_BASELINE_OBSERVATIONS,
)
from app.analysis.anomaly.detectors import DEFAULT_METHOD
from app.analysis.anomaly.models import AnomalyReport, Observation


class DetectionRequest(BaseModel):
    dataset_id: UUID
    # Defaults to the dataset's active definition.
    kpi_definition_id: UUID | None = None
    # Overrides the profiled reporting frequency. The profiler reports how often
    # rows *arrive*, which for transactional data is daily regardless of the
    # grain the business reads the KPI at - so this is a primary control.
    grain: str | None = None
    method: str = DEFAULT_METHOD
    # Bounded rather than free: below 3 the median is meaningless, and above two
    # years a "recent baseline" is not recent.
    baseline_window: int = Field(default=BASELINE_WINDOW, ge=3, le=104)


class BaselineRead(BaseModel):
    """What normal looked like for one period, and how that was decided."""

    expected_value: float
    scale: float
    # Which dispersion estimate the score was divided by. A score computed
    # against a fallback scale is weaker evidence than one against a real MAD.
    scale_basis: str
    observations_used: int


class ObservationRead(BaseModel):
    """One period of the series, with its verdict.

    ``value`` is null only when the period has no rows at all. That is not a
    KPI of zero, and the two must not be drawn the same way.
    """

    period_start: datetime
    period_end: datetime  # Exclusive.
    value: float | None = None
    row_count: int = 0
    # EVALUATED | INSUFFICIENT_HISTORY | MISSING | PARTIAL
    status: str
    baseline: BaselineRead | None = None
    absolute_deviation: float | None = None
    percentage_deviation: float | None = None
    # "zero_baseline" when the expected value is zero, so an absent percentage
    # has a stated reason rather than being silently missing.
    percentage_unavailable_reason: str | None = None
    anomaly_score: float | None = None
    severity: str
    direction: str
    is_anomaly: bool = False


class MethodRead(BaseModel):
    """The settings the verdicts were produced under.

    Echoed back for the same reason the RCA response echoes its Pareto target:
    a threshold the reader cannot see is a threshold they cannot argue with.
    """

    name: str
    baseline_window: int
    min_baseline_observations: int
    anomaly_threshold: float
    severity_thresholds: dict[str, float]
    score_interpretation: str


class EvidenceRead(BaseModel):
    total_rows: int = 0
    rows_in_series: int = 0
    unparsed_time_rows: int = 0
    unparsed_measure_rows: int = 0
    periods_observed: int = 0
    periods_missing: int = 0
    periods_evaluated: int = 0
    statements_executed: int = 0
    duration_ms: int = 0


class NoticeRead(BaseModel):
    code: str
    severity: str  # "info" | "warning"
    message: str
    details: dict[str, Any] | None = None


class KpiRead(BaseModel):
    name: str
    column: str
    aggregation: str
    time_column: str | None = None
    grain: str


class DetectionResult(BaseModel):
    dataset_id: UUID
    dataset_name: str
    kpi_definition_id: UUID
    generated_at: datetime
    # OK | NO_DATA | INSUFFICIENT_HISTORY | NO_TIME_COLUMN
    status: str
    kpi: KpiRead
    method: MethodRead
    # Every period on the calendar, gaps included - this is what the chart draws.
    series: list[ObservationRead] = Field(default_factory=list)
    # The subset that cleared the threshold.
    anomalies: list[ObservationRead] = Field(default_factory=list)
    # The most recent evaluated period: what "is this KPI unusual now?" asks.
    latest: ObservationRead | None = None
    evidence: EvidenceRead
    notices: list[NoticeRead] = Field(default_factory=list)
    # What the method cannot see, stated up front rather than discovered.
    limitations: list[str] = Field(default_factory=list)
    summary: str = ""


SCORE_INTERPRETATION = (
    "Modified z-score: 0.6745 * (actual - baseline median) / MAD. Because "
    "1.4826 * MAD estimates the standard deviation for normally distributed "
    "data, the score reads as the number of robust standard deviations from "
    "normal. 3.5 is the Iglewicz & Hoaglin outlier cutoff."
)


def _baseline(observation: Observation) -> BaselineRead | None:
    if observation.baseline is None:
        return None
    return BaselineRead(
        expected_value=observation.baseline.expected_value,
        scale=observation.baseline.scale,
        scale_basis=observation.baseline.scale_basis.value,
        observations_used=observation.baseline.observations_used,
    )


def _observation(observation: Observation) -> ObservationRead:
    return ObservationRead(
        period_start=observation.period_start,
        period_end=observation.period_end,
        value=observation.value,
        row_count=observation.row_count,
        status=observation.status.value,
        baseline=_baseline(observation),
        absolute_deviation=observation.absolute_deviation,
        percentage_deviation=observation.percentage_deviation,
        percentage_unavailable_reason=observation.percentage_unavailable_reason,
        anomaly_score=observation.anomaly_score,
        severity=observation.severity.value,
        direction=observation.direction.value,
        is_anomaly=observation.is_anomaly,
    )


def to_report(
    *,
    dataset_id: UUID,
    dataset_name: str,
    kpi_definition_id: UUID,
    generated_at: datetime,
    measure_column: str,
    time_column: str | None,
    baseline_window: int,
    report: AnomalyReport,
) -> DetectionResult:
    """Map the engine's dataclasses onto the wire contract."""
    return DetectionResult(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        kpi_definition_id=kpi_definition_id,
        generated_at=generated_at,
        status=report.status.value,
        kpi=KpiRead(
            name=report.kpi_name,
            column=measure_column,
            aggregation=report.aggregation.value,
            time_column=time_column,
            grain=report.grain.value,
        ),
        method=MethodRead(
            name=report.method,
            baseline_window=baseline_window,
            min_baseline_observations=MIN_BASELINE_OBSERVATIONS,
            anomaly_threshold=LOW_THRESHOLD,
            severity_thresholds={
                "LOW": LOW_THRESHOLD,
                "MEDIUM": MEDIUM_THRESHOLD,
                "HIGH": HIGH_THRESHOLD,
                "CRITICAL": CRITICAL_THRESHOLD,
            },
            score_interpretation=SCORE_INTERPRETATION,
        ),
        series=[_observation(o) for o in report.series],
        anomalies=[_observation(o) for o in report.anomalies],
        latest=_observation(report.latest) if report.latest else None,
        evidence=EvidenceRead(
            total_rows=report.evidence.total_rows,
            rows_in_series=report.evidence.rows_in_series,
            unparsed_time_rows=report.evidence.unparsed_time_rows,
            unparsed_measure_rows=report.evidence.unparsed_measure_rows,
            periods_observed=report.evidence.periods_observed,
            periods_missing=report.evidence.periods_missing,
            periods_evaluated=report.evidence.periods_evaluated,
            statements_executed=report.evidence.statements_executed,
            duration_ms=report.evidence.duration_ms,
        ),
        notices=[
            NoticeRead(
                code=n.code, severity=n.severity, message=n.message, details=n.details
            )
            for n in report.notices
        ],
        limitations=list(report.limitations),
        summary=report.summary,
    )
