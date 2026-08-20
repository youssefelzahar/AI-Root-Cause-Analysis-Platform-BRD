"""Contribution maths. No DuckDB, no database."""

import random

import pytest

from app.analysis.rca.contribution import (
    basis_for,
    build_nodes,
    contribution_sum,
    explanatory_power,
    percent_change,
)
from app.analysis.rca.models import (
    AttributionBasis,
    SegmentTotals,
    Totals,
    UnattributableReason,
)
from app.db.models.enums import Aggregation


def _segment(
    value: str,
    previous: float | None,
    current: float | None,
    *,
    previous_count: int = 10,
    current_count: int = 10,
    dimension: str = "region",
) -> SegmentTotals:
    return SegmentTotals(
        dimension=dimension,
        value=value,
        value_is_null=False,
        current_value=current,
        previous_value=previous,
        current_count=current_count if current is not None else 0,
        previous_count=previous_count if previous is not None else 0,
        current_rows=current_count if current is not None else 0,
        previous_rows=previous_count if previous is not None else 0,
    )


def _totals(segments: list[SegmentTotals]) -> Totals:
    return Totals(
        current_value=sum(s.current_value or 0.0 for s in segments),
        previous_value=sum(s.previous_value or 0.0 for s in segments),
        current_count=sum(s.current_count for s in segments),
        previous_count=sum(s.previous_count for s in segments),
        current_rows=sum(s.current_rows for s in segments),
        previous_rows=sum(s.previous_rows for s in segments),
    )


def _nodes(segments, aggregation=Aggregation.SUM, basis=AttributionBasis.NET_CHANGE):
    totals = _totals(segments)
    return build_nodes(segments, totals, totals.absolute_change, aggregation, basis, None)


def test_contributions_sum_to_one():
    nodes = _nodes([_segment("Cairo", 800, 500), _segment("Giza", 700, 700)])
    assert contribution_sum(nodes) == pytest.approx(1.0)


def test_a_segment_moving_against_the_kpi_gets_a_negative_contribution():
    # Total falls by 200; Giza rises. Giza is an offsetting factor, and the old
    # gross-movement formula would have reported it as a top driver.
    nodes = _nodes([_segment("Cairo", 1000, 700), _segment("Giza", 500, 600)])
    by_value = {n.value: n for n in nodes}
    assert by_value["Cairo"].contribution == pytest.approx(1.5)
    assert by_value["Giza"].contribution == pytest.approx(-0.5)
    assert contribution_sum(nodes) == pytest.approx(1.0)


def test_contributions_above_one_are_not_clamped():
    """Over 100% is meaningful, not an error: something else offset it."""
    nodes = _nodes([_segment("Cairo", 1000, 700), _segment("Giza", 500, 600)])
    assert max(n.contribution for n in nodes) > 1.0


def test_a_new_segment_contributes_its_whole_current_value():
    nodes = _nodes([_segment("Cairo", 1000, 700), _segment("Aswan", None, 100)])
    aswan = next(n for n in nodes if n.value == "Aswan")
    assert aswan.is_new_segment
    assert aswan.absolute_change == pytest.approx(100.0)
    assert aswan.percent_change is None
    assert aswan.percent_change_undefined_reason == "zero_baseline"


def test_a_lost_segment_contributes_its_whole_previous_value():
    nodes = _nodes([_segment("Cairo", 1000, 1000), _segment("Luxor", 250, None)])
    luxor = next(n for n in nodes if n.value == "Luxor")
    assert luxor.is_lost_segment
    assert luxor.absolute_change == pytest.approx(-250.0)
    assert luxor.contribution == pytest.approx(1.0)


def test_percent_change_of_a_negative_baseline_keeps_the_right_sign():
    """A metric going from -100 to -150 got worse, so the sign must be negative.

    Dividing by the raw baseline instead of its magnitude reports +50%, which
    reads as an improvement. This is the classic period-over-period sign bug.
    """
    value, reason = percent_change(-150, -100)
    assert value == pytest.approx(-50.0)
    assert reason is None


def test_percent_change_is_undefined_rather_than_infinite_on_a_zero_baseline():
    value, reason = percent_change(500, 0)
    assert value is None
    assert reason == "zero_baseline"


def test_a_near_zero_net_change_switches_to_gross_movement():
    """Large movements cancelling out must not divide by the tiny remainder."""
    segments = [_segment("Cairo", 1000, 2000), _segment("Giza", 1000, 10)]
    basis, reason = basis_for(Aggregation.SUM, _totals(segments), segments)
    assert basis is AttributionBasis.GROSS_MOVEMENT
    assert reason is None

    nodes = _nodes(segments, basis=AttributionBasis.GROSS_MOVEMENT)
    # Shares of gross movement: magnitudes sum to 1, signs preserved.
    assert sum(abs(n.contribution) for n in nodes) == pytest.approx(1.0)


def test_nothing_moving_anywhere_is_not_a_gross_movement_case():
    segments = [_segment("Cairo", 500, 500), _segment("Giza", 500, 500)]
    basis, _ = basis_for(Aggregation.SUM, _totals(segments), segments)
    assert basis is AttributionBasis.NET_CHANGE


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [
        (Aggregation.MIN, UnattributableReason.ORDER_STATISTIC),
        (Aggregation.MAX, UnattributableReason.ORDER_STATISTIC),
        (Aggregation.MEDIAN, UnattributableReason.DISTRIBUTIONAL_STATISTIC),
        (Aggregation.COUNT_DISTINCT, UnattributableReason.NON_ADDITIVE_SET_OVERLAP),
    ],
)
def test_non_decomposable_aggregations_report_no_contribution(aggregation, expected):
    segments = [_segment("Cairo", 800, 500), _segment("Giza", 700, 700)]
    basis, reason = basis_for(aggregation, _totals(segments), segments)
    assert basis is AttributionBasis.UNATTRIBUTABLE
    assert reason is expected

    nodes = _nodes(segments, aggregation, AttributionBasis.UNATTRIBUTABLE)
    assert all(n.contribution is None for n in nodes)
    # The numbers are still reported - only the attribution is withheld.
    assert all(n.absolute_change is not None for n in nodes)


def test_count_distinct_is_additive_when_the_dimension_partitions_the_keys():
    """Checked rather than assumed: zero overlap means the split is valid."""
    segments = [_segment("Cairo", 40, 30), _segment("Giza", 20, 20)]
    basis, reason = basis_for(
        Aggregation.COUNT_DISTINCT, _totals(segments), segments, distinct_overlap_gap=(0, 0)
    )
    assert basis is AttributionBasis.NET_CHANGE
    assert reason is None


def test_count_distinct_with_overlapping_keys_stays_unattributable():
    segments = [_segment("Cairo", 40, 30), _segment("Giza", 20, 20)]
    basis, reason = basis_for(
        Aggregation.COUNT_DISTINCT, _totals(segments), segments, distinct_overlap_gap=(5, 3)
    )
    assert basis is AttributionBasis.UNATTRIBUTABLE
    assert reason is UnattributableReason.NON_ADDITIVE_SET_OVERLAP


# --- AVG: the decomposition must be exact -------------------------------------


def _avg_case(rows):
    """rows: (label, n_prev, m_prev, n_curr, m_curr)."""
    segments = [
        SegmentTotals(
            dimension="region",
            value=label,
            value_is_null=False,
            previous_value=m0,
            current_value=m1,
            previous_count=n0,
            current_count=n1,
            previous_rows=n0,
            current_rows=n1,
        )
        for label, n0, m0, n1, m1 in rows
    ]
    total_n0 = sum(r[1] for r in rows)
    total_n1 = sum(r[3] for r in rows)
    a0 = sum(r[1] * r[2] for r in rows) / total_n0
    a1 = sum(r[3] * r[4] for r in rows) / total_n1
    totals = Totals(
        current_value=a1,
        previous_value=a0,
        current_count=total_n1,
        previous_count=total_n0,
        current_rows=total_n1,
        previous_rows=total_n0,
    )
    nodes = build_nodes(
        segments, totals, a1 - a0, Aggregation.AVG, AttributionBasis.MIX_RATE, None
    )
    return nodes, a1 - a0


def test_average_rate_and_mix_effects_reconstruct_the_total_change():
    """The identity the whole AVG path rests on."""
    nodes, delta = _avg_case(
        [("A", 100, 10.0, 150, 12.0), ("B", 100, 20.0, 50, 20.0), ("C", 50, 15.0, 50, 15.0)]
    )
    combined = sum((n.rate_effect or 0.0) + (n.mix_effect or 0.0) for n in nodes)
    assert combined == pytest.approx(delta)
    assert contribution_sum(nodes) == pytest.approx(1.0)


def test_average_decomposition_is_exact_for_random_inputs():
    """Property test: exactness must not depend on the numbers chosen."""
    rng = random.Random(20260817)
    for _ in range(200):
        rows = [
            (
                f"s{i}",
                rng.randint(1, 500),
                rng.uniform(-100, 100),
                rng.randint(1, 500),
                rng.uniform(-100, 100),
            )
            for i in range(rng.randint(2, 6))
        ]
        nodes, delta = _avg_case(rows)
        combined = sum((n.rate_effect or 0.0) + (n.mix_effect or 0.0) for n in nodes)
        assert combined == pytest.approx(delta, abs=1e-9)


def test_shifting_volume_between_equal_means_reports_no_mix_effect():
    """Why the decomposition is centred on the overall average.

    Every group has the same mean, so moving volume around cannot move the total
    average. An uncentred split would still report large equal-and-opposite mix
    effects per group - pure noise.
    """
    nodes, delta = _avg_case([("A", 100, 10.0, 200, 10.0), ("B", 100, 10.0, 50, 10.0)])
    assert delta == pytest.approx(0.0)
    assert all((n.mix_effect or 0.0) == pytest.approx(0.0) for n in nodes)
    assert all((n.rate_effect or 0.0) == pytest.approx(0.0) for n in nodes)


def test_a_new_segment_contributes_only_through_mix():
    """It had no average of its own last period, so it cannot have moved one."""
    nodes, _ = _avg_case([("A", 100, 10.0, 100, 10.0), ("New", 0, 0.0, 50, 30.0)])
    new = next(n for n in nodes if n.value == "New")
    assert new.rate_effect == pytest.approx(0.0)
    assert new.mix_effect != pytest.approx(0.0)


# --- explanatory power --------------------------------------------------------


def test_a_proportional_change_has_no_explanatory_power():
    """Every cell shrank by the same fraction, so the dimension explains nothing."""
    segments = [_segment("Cairo", 600, 300), _segment("Giza", 400, 200)]
    nodes = _nodes(segments)
    assert explanatory_power(nodes, _totals(segments).absolute_change) == pytest.approx(0.0)


def test_a_concentrated_change_has_high_explanatory_power():
    segments = [_segment("Cairo", 600, 300), _segment("Giza", 400, 400)]
    nodes = _nodes(segments)
    power = explanatory_power(nodes, _totals(segments).absolute_change)
    assert power is not None and power > 0.5
