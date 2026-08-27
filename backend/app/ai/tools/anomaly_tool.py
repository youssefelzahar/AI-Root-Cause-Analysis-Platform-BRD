"""``detect_anomaly`` - is this movement unusual, or ordinary variation?

The one analytical tool that is a real second computation rather than a projection.
An investigation already runs anomaly detection and evidences the anomalies that
fall *inside* the two compared windows, which is the right filter when the question
is "why did this change". But when the question is "was this unusual", the reader
wants the whole series, the per-period severity and the thresholds those severities
came from - and only ``anomaly_service.run`` produces that.

The thresholds are reported and never applied here. ``3.5`` deviations is the
detector's cutoff and it is not tunable, by design: an operator who could lower it
could make anything anomalous.
"""

from typing import Any

from app.ai.tools.base import ToolContext, ToolSpec

DESCRIPTION = (
    "Whether the KPI's recent periods deviate from their own trailing baseline, "
    "with each anomalous period's score, severity and direction."
)

# The tail of the series, most recent last. A local model given eighty periods
# summarises the list instead of the finding, and the anomalies themselves are
# reported separately in full.
MAX_SERIES_POINTS = 12


def run(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    report = context.anomaly
    if not report:
        # The step was planned but the detection did not run - no reporting
        # frequency, no time column, or a non-decomposable measure. A limitation,
        # never an error.
        return {
            "ran": False,
            "detail": (
                "Anomaly detection did not run for this KPI, so there is no verdict on "
                "whether the movement is unusual."
            ),
            "anomalies": [],
        }

    series = report.get("series") or []
    method = report.get("method") or {}

    return {
        "ran": True,
        "status": report.get("status"),
        "grain": report.get("grain"),
        "method": method.get("name") or report.get("method_name"),
        "anomaly_threshold": method.get("anomaly_threshold"),
        "severity_thresholds": method.get("severity_thresholds"),
        "score_interpretation": method.get("score_interpretation"),
        "periods_evaluated": len(series),
        "recent_series": [
            {
                "period_start": point.get("period_start"),
                "value": point.get("value"),
                "status": point.get("status"),
                "anomaly_score": point.get("anomaly_score"),
                "severity": point.get("severity"),
                "is_anomaly": point.get("is_anomaly", False),
            }
            for point in series[-MAX_SERIES_POINTS:]
        ],
        "anomalies": [
            {
                "period_start": point.get("period_start"),
                "value": point.get("value"),
                "expected_value": (point.get("baseline") or {}).get("expected_value"),
                "absolute_deviation": point.get("absolute_deviation"),
                "percentage_deviation": point.get("percentage_deviation"),
                "anomaly_score": point.get("anomaly_score"),
                "severity": point.get("severity"),
                "direction": point.get("direction"),
            }
            for point in (report.get("anomalies") or [])
        ],
        "latest": report.get("latest"),
        "summary": report.get("summary"),
        "limitations": report.get("limitations") or [],
    }


SPEC = ToolSpec(
    name="detect_anomaly",
    description=DESCRIPTION,
    arguments={},
    run=run,
)
