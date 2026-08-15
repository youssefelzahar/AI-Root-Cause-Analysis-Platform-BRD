"""Dimension drill-down."""

from collections import defaultdict

from app.schemas.rca import MetricPoint


def group_sum(points: list[MetricPoint], dimension: str) -> dict[str, float]:
    grouped: dict[str, float] = defaultdict(float)
    for point in points:
        grouped[point.dimensions.get(dimension, "Unknown")] += point.value
    return dict(grouped)


def dimension_values(
    baseline: list[MetricPoint], comparison: list[MetricPoint], dimension: str
) -> list[tuple[str, float, float, float]]:
    """Return (value, baseline, comparison, change) for each dimension value."""
    baseline_groups = group_sum(baseline, dimension)
    comparison_groups = group_sum(comparison, dimension)

    rows: list[tuple[str, float, float, float]] = []
    for value in sorted(set(baseline_groups) | set(comparison_groups)):
        baseline_value = baseline_groups.get(value, 0.0)
        comparison_value = comparison_groups.get(value, 0.0)
        rows.append((value, baseline_value, comparison_value, comparison_value - baseline_value))
    return rows
