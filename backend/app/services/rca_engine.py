from collections import defaultdict

from app.schemas.rca import (
    AnomalyResult,
    DriverFinding,
    InvestigationRequest,
    InvestigationResult,
    MetricPoint,
)


def _average(points: list[MetricPoint]) -> float:
    if not points:
        return 0.0
    return sum(point.value for point in points) / len(points)


def _severity(percent_change: float) -> str:
    magnitude = abs(percent_change)
    if magnitude >= 25:
        return "critical"
    if magnitude >= 12:
        return "high"
    if magnitude >= 5:
        return "medium"
    return "low"


def _group_sum(points: list[MetricPoint], dimension: str) -> dict[str, float]:
    grouped: dict[str, float] = defaultdict(float)
    for point in points:
        grouped[point.dimensions.get(dimension, "Unknown")] += point.value
    return dict(grouped)


def analyze(request: InvestigationRequest) -> InvestigationResult:
    baseline_avg = _average(request.baseline_period)
    comparison_avg = _average(request.comparison_period)
    absolute_change = comparison_avg - baseline_avg
    percent_change = 0.0 if baseline_avg == 0 else (absolute_change / baseline_avg) * 100

    anomaly = AnomalyResult(
        metric_name=request.metric_name,
        baseline_average=round(baseline_avg, 2),
        comparison_average=round(comparison_avg, 2),
        absolute_change=round(absolute_change, 2),
        percent_change=round(percent_change, 2),
        severity=_severity(percent_change),
    )

    raw_changes: list[tuple[str, str, float, float, float]] = []
    total_abs_change = 0.000001
    for dimension in request.dimensions:
        baseline_groups = _group_sum(request.baseline_period, dimension)
        comparison_groups = _group_sum(request.comparison_period, dimension)
        for value in sorted(set(baseline_groups) | set(comparison_groups)):
            baseline_value = baseline_groups.get(value, 0.0)
            comparison_value = comparison_groups.get(value, 0.0)
            change = comparison_value - baseline_value
            raw_changes.append((dimension, value, baseline_value, comparison_value, change))
            total_abs_change += abs(change)

    findings = [
        DriverFinding(
            dimension=dimension,
            value=value,
            baseline_value=round(baseline_value, 2),
            comparison_value=round(comparison_value, 2),
            absolute_change=round(change, 2),
            contribution_pct=round((abs(change) / total_abs_change) * 100, 2),
        )
        for dimension, value, baseline_value, comparison_value, change in raw_changes
    ]

    top_drivers = sorted(findings, key=lambda item: item.contribution_pct, reverse=True)[:8]
    direction = "increased" if absolute_change >= 0 else "decreased"
    driver_text = (
        f"The largest contributor is {top_drivers[0].dimension}={top_drivers[0].value}."
        if top_drivers
        else "No dimensional driver was provided."
    )
    summary = (
        f"{request.metric_name} {direction} by {abs(percent_change):.2f}% versus baseline. "
        f"Severity is {anomaly.severity}. {driver_text}"
    )

    return InvestigationResult(
        anomaly=anomaly,
        top_drivers=top_drivers,
        summary=summary,
        recommended_actions=[
            "Review the top contributing segments before taking corrective action.",
            "Validate data freshness and metric definition changes for the comparison window.",
            "Add analyst feedback to improve future RCA ranking.",
        ],
    )
