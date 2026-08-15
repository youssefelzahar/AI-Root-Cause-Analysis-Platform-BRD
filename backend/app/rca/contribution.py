"""Contribution analysis."""

from app.schemas.rca import DriverFinding

# Guards against division by zero when nothing changed.
EPSILON = 0.000001


def build_findings(raw_changes: list[tuple[str, str, float, float, float]]) -> list[DriverFinding]:
    total_abs_change = EPSILON + sum(abs(change) for *_, change in raw_changes)
    return [
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
