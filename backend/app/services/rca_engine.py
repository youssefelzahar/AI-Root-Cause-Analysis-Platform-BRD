"""RCA orchestration.

Behaviour is unchanged from the original single-module implementation; the
analytical steps now live in ``app.rca`` so the future RCA phase has somewhere
to grow. ``POST /api/investigations`` continues to work exactly as before.
"""

from app.rca import anomaly_detection, contribution, dimension_analysis, ranking
from app.schemas.rca import InvestigationRequest, InvestigationResult


def analyze(request: InvestigationRequest) -> InvestigationResult:
    anomaly = anomaly_detection.detect(
        request.metric_name, request.baseline_period, request.comparison_period
    )

    raw_changes: list[tuple[str, str, float, float, float]] = []
    for dimension in request.dimensions:
        for value, baseline_value, comparison_value, change in dimension_analysis.dimension_values(
            request.baseline_period, request.comparison_period, dimension
        ):
            raw_changes.append((dimension, value, baseline_value, comparison_value, change))

    findings = contribution.build_findings(raw_changes)
    top_drivers = ranking.rank(findings)

    direction = "increased" if anomaly.absolute_change >= 0 else "decreased"
    driver_text = (
        f"The largest contributor is {top_drivers[0].dimension}={top_drivers[0].value}."
        if top_drivers
        else "No dimensional driver was provided."
    )
    summary = (
        f"{request.metric_name} {direction} by {abs(anomaly.percent_change):.2f}% versus baseline. "
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
