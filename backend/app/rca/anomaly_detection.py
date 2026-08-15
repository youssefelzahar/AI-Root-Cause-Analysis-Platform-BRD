"""KPI anomaly detection.

Extracted from the original services/rca_engine.py; behaviour is unchanged.
"""

from app.schemas.rca import AnomalyResult, MetricPoint

# Percent-change magnitude thresholds.
CRITICAL_THRESHOLD = 25.0
HIGH_THRESHOLD = 12.0
MEDIUM_THRESHOLD = 5.0


def average(points: list[MetricPoint]) -> float:
    if not points:
        return 0.0
    return sum(point.value for point in points) / len(points)


def severity(percent_change: float) -> str:
    magnitude = abs(percent_change)
    if magnitude >= CRITICAL_THRESHOLD:
        return "critical"
    if magnitude >= HIGH_THRESHOLD:
        return "high"
    if magnitude >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def detect(metric_name: str, baseline: list[MetricPoint], comparison: list[MetricPoint]) -> AnomalyResult:
    baseline_avg = average(baseline)
    comparison_avg = average(comparison)
    absolute_change = comparison_avg - baseline_avg
    percent_change = 0.0 if baseline_avg == 0 else (absolute_change / baseline_avg) * 100

    return AnomalyResult(
        metric_name=metric_name,
        baseline_average=round(baseline_avg, 2),
        comparison_average=round(comparison_avg, 2),
        absolute_change=round(absolute_change, 2),
        percent_change=round(percent_change, 2),
        severity=severity(percent_change),
    )
