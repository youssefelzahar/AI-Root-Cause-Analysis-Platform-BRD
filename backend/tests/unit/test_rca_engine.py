"""Regression lock on the pre-existing RCA behaviour.

The engine was split into ``app/rca/*`` during Phase 1; these assertions pin
the original behaviour so that refactor stayed behaviour-preserving.
"""

from app.rca import anomaly_detection
from app.schemas.rca import InvestigationRequest
from app.services.rca_engine import analyze


def _request(**overrides):
    payload = {
        "metric_name": "Revenue",
        "baseline_period": [
            {"date": "2026-08-01", "value": 12000, "dimensions": {"region": "North"}},
            {"date": "2026-08-01", "value": 9000, "dimensions": {"region": "South"}},
        ],
        "comparison_period": [
            {"date": "2026-08-08", "value": 8700, "dimensions": {"region": "North"}},
            {"date": "2026-08-08", "value": 9300, "dimensions": {"region": "South"}},
        ],
        "dimensions": ["region"],
    }
    payload.update(overrides)
    return InvestigationRequest(**payload)


def test_severity_thresholds_are_unchanged():
    assert anomaly_detection.severity(30) == "critical"
    assert anomaly_detection.severity(25) == "critical"
    assert anomaly_detection.severity(24.9) == "high"
    assert anomaly_detection.severity(12) == "high"
    assert anomaly_detection.severity(11.9) == "medium"
    assert anomaly_detection.severity(5) == "medium"
    assert anomaly_detection.severity(4.9) == "low"
    assert anomaly_detection.severity(-30) == "critical"


def test_zero_baseline_does_not_divide_by_zero():
    assert analyze(_request(baseline_period=[])).anomaly.percent_change == 0.0


def test_contributions_are_reported_and_ranked():
    result = analyze(_request())
    assert result.top_drivers
    assert result.top_drivers == sorted(
        result.top_drivers, key=lambda d: d.contribution_pct, reverse=True
    )
    assert sum(d.contribution_pct for d in result.top_drivers) == 100.0
    assert result.anomaly.severity == "high"
    assert "decreased" in result.summary


def test_top_drivers_are_capped_at_eight():
    baseline = [
        {"date": "2026-08-01", "value": 100 + i, "dimensions": {"region": f"R{i}"}} for i in range(20)
    ]
    comparison = [
        {"date": "2026-08-08", "value": 50 + i, "dimensions": {"region": f"R{i}"}} for i in range(20)
    ]
    result = analyze(_request(baseline_period=baseline, comparison_period=comparison))
    assert len(result.top_drivers) == 8
