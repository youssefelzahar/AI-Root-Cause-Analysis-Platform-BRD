"""Driver classification, severity and drill-down decisions."""

import pytest

from app.analysis.rca import ranking, tree
from app.analysis.rca.constants import CRITICAL_THRESHOLD, HIGH_THRESHOLD, MEDIUM_THRESHOLD
from app.analysis.rca.models import ChangePattern, Classification, DriverNode


def _node(value: str, contribution: float, *, low_support: bool = False, rows: int = 100):
    return DriverNode(
        node_id=f"region={value}",
        depth=1,
        path=(("region", value),),
        dimension="region",
        value=value,
        absolute_change=contribution * -1000,
        contribution=contribution,
        current_rows=rows,
        previous_rows=rows,
        low_support=low_support,
    )


# --- severity: carried over verbatim so the UI vocabulary is unchanged --------


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (CRITICAL_THRESHOLD, "critical"),
        (CRITICAL_THRESHOLD - 0.1, "high"),
        (HIGH_THRESHOLD, "high"),
        (HIGH_THRESHOLD - 0.1, "medium"),
        (MEDIUM_THRESHOLD, "medium"),
        (MEDIUM_THRESHOLD - 0.1, "low"),
        (-30.0, "critical"),
        (None, "low"),
    ],
)
def test_severity_thresholds_are_unchanged(percent, expected):
    assert ranking.severity(percent) == expected


@pytest.mark.parametrize(
    ("change", "expected"),
    [(-5.0, "down"), (5.0, "up"), (0.0, "flat"), (None, "unknown")],
)
def test_direction_reads_the_sign_of_the_change(change, expected):
    assert ranking.direction(change) == expected


# --- classification -----------------------------------------------------------


def test_primary_drivers_stop_once_they_cover_the_pareto_target():
    nodes = [_node("A", 0.5), _node("B", 0.35), _node("C", 0.1), _node("D", 0.05)]
    primary, secondary, offsetting = ranking.classify(nodes)
    assert [n.value for n in primary] == ["A", "B"]  # 0.85 >= 0.80
    assert [n.value for n in secondary] == ["C", "D"]
    assert offsetting == []


def test_a_single_dominant_segment_is_the_only_primary_driver():
    nodes = [_node("A", 0.9), _node("B", 0.1)]
    primary, secondary, _ = ranking.classify(nodes)
    assert [n.value for n in primary] == ["A"]
    assert [n.value for n in secondary] == ["B"]


def test_segments_moving_against_the_kpi_become_offsetting_factors():
    nodes = [_node("A", 1.4), _node("B", -0.4)]
    primary, _, offsetting = ranking.classify(nodes)
    assert [n.value for n in primary] == ["A"]
    assert [n.value for n in offsetting] == ["B"]
    # Offsetting contributions stay negative - that is what makes them readable
    # as "this pushed the other way".
    assert offsetting[0].contribution < 0


def test_immaterial_segments_are_named_in_no_list():
    nodes = [_node("A", 0.97), _node("Tiny", 0.03)]
    primary, secondary, offsetting = ranking.classify(nodes)
    assert "Tiny" not in {n.value for n in primary + secondary + offsetting}
    tiny = next(n for n in nodes if n.value == "Tiny")
    assert tiny.classification is Classification.IMMATERIAL


def test_primary_drivers_are_capped():
    nodes = [_node(str(i), 0.2) for i in range(10)]
    primary, _, _ = ranking.classify(nodes, max_drivers=3)
    assert len(primary) == 3


def test_a_thin_segment_yields_to_a_better_supported_one():
    nodes = [_node("Thin", 0.9, low_support=True), _node("Solid", 0.6)]
    primary, secondary, _ = ranking.classify(nodes)
    assert [n.value for n in primary] == ["Solid"]
    assert "Thin" in {n.value for n in secondary}


def test_thin_segments_are_still_named_when_nothing_is_better_supported():
    """Suppressing every candidate would report "nothing explains this" when
    something demonstrably does. Naming them with the flag is more useful."""
    nodes = [_node("A", 0.9, low_support=True), _node("B", 0.1, low_support=True)]
    primary, _, _ = ranking.classify(nodes)
    assert [n.value for n in primary] == ["A"]
    assert primary[0].low_support is True


def test_ranking_orders_by_contribution_magnitude():
    nodes = [_node("A", 0.2), _node("B", -0.9), _node("C", 0.5)]
    ranking.classify(nodes)
    assert [n.value for n in sorted(nodes, key=lambda n: n.rank)] == ["B", "C", "A"]


# --- change pattern -----------------------------------------------------------


def test_a_proportional_change_is_reported_as_broad_based():
    """No dimension concentrates it, so there is no driver to name."""
    nodes = [_node(str(i), 0.25) for i in range(4)]
    primary, _, offsetting = ranking.classify(nodes)
    assert ranking.change_pattern(primary, offsetting, 0.0) is ChangePattern.BROAD_BASED


def test_one_dominant_driver_is_reported_as_a_single_driver():
    nodes = [_node("A", 0.95), _node("B", 0.05)]
    primary, _, offsetting = ranking.classify(nodes)
    assert ranking.change_pattern(primary, offsetting, 0.9) is ChangePattern.SINGLE_DRIVER


def test_large_cancelling_movements_are_reported_as_offsetting():
    nodes = [_node("A", 1.0), _node("B", -0.8)]
    primary, _, offsetting = ranking.classify(nodes)
    assert ranking.change_pattern(primary, offsetting, 0.9) is ChangePattern.OFFSETTING


# --- drill-down ---------------------------------------------------------------


def test_the_dimension_with_the_most_explanatory_power_wins():
    concentrated = [
        DriverNode(
            node_id="a",
            depth=1,
            path=(),
            dimension="region",
            value="Cairo",
            absolute_change=-300,
            excess_change=-140,
            contribution=1.0,
            current_rows=10,
            previous_rows=10,
        ),
        DriverNode(
            node_id="b",
            depth=1,
            path=(),
            dimension="region",
            value="Giza",
            absolute_change=0,
            excess_change=140,
            contribution=0.0,
            current_rows=10,
            previous_rows=10,
        ),
    ]
    diffuse = [
        DriverNode(
            node_id="c",
            depth=1,
            path=(),
            dimension="channel",
            value="Direct",
            absolute_change=-150,
            excess_change=-10,
            contribution=0.5,
            current_rows=10,
            previous_rows=10,
        ),
        DriverNode(
            node_id="d",
            depth=1,
            path=(),
            dimension="channel",
            value="Partner",
            absolute_change=-150,
            excess_change=10,
            contribution=0.5,
            current_rows=10,
            previous_rows=10,
        ),
    ]
    choice = tree.select_dimension(
        {"region": concentrated, "channel": diffuse}, -300.0, ("region", "channel")
    )
    assert choice is not None
    assert choice.dimension == "region"
    assert choice.split_type == "partition"


def test_a_dimension_with_one_value_is_a_pure_split_not_a_partition():
    """"This whole segment is Enterprise" is a finding, but it cannot be scored
    by deviation-from-proportional, which is zero for a single cell."""
    single = [
        DriverNode(
            node_id="x",
            depth=2,
            path=(),
            dimension="segment",
            value="Enterprise",
            absolute_change=-300,
            excess_change=0,
            contribution=1.0,
            current_rows=5,
            previous_rows=5,
        )
    ]
    choice = tree.select_dimension({"segment": single}, -300.0, ("segment",))
    assert choice is not None
    assert choice.split_type == "pure"


def test_a_uniformly_spread_dimension_is_not_worth_descending():
    flat = [
        DriverNode(
            node_id=str(i),
            depth=1,
            path=(),
            dimension="channel",
            value=str(i),
            absolute_change=-100,
            excess_change=0.0,
            contribution=0.25,
            current_rows=10,
            previous_rows=10,
        )
        for i in range(4)
    ]
    assert tree.select_dimension({"channel": flat}, -400.0, ("channel",)) is None


@pytest.mark.parametrize(
    ("contribution", "remaining", "depth", "expected_reason"),
    [
        (1.0, ("product",), 3, "max_depth_reached"),
        (1.0, (), 1, "no_dimensions_left"),
        (0.01, ("product",), 1, "contribution_immaterial"),
    ],
)
def test_stopping_reasons_are_recorded_on_the_node(
    contribution, remaining, depth, expected_reason
):
    from app.db.models.enums import Aggregation

    node = _node("Cairo", contribution)
    node.depth = depth
    assert not tree.drillable(node, remaining, Aggregation.SUM, 3)
    assert node.stop_reason == expected_reason


def test_a_residual_bucket_is_never_descended_into():
    from app.db.models.enums import Aggregation

    node = _node("(other)", 0.4)
    node.is_other_bucket = True
    assert not tree.drillable(node, ("product",), Aggregation.SUM, 3)
    assert node.stop_reason == "residual_bucket"


def test_only_mean_like_aggregations_gate_on_row_count():
    """For SUM the decomposition is exact arithmetic, so a three-row segment
    that moved the total really did move it."""
    from app.db.models.enums import Aggregation

    thin = _node("Cairo", 0.9, rows=1)
    assert tree.drillable(thin, ("product",), Aggregation.SUM, 3)

    thin_avg = _node("Cairo", 0.9, rows=1)
    assert not tree.drillable(thin_avg, ("product",), Aggregation.AVG, 3)
    assert thin_avg.stop_reason == "insufficient_rows"


def test_branching_narrows_as_the_tree_deepens():
    assert tree.branching_at(1) == 3
    assert tree.branching_at(2) == 2
    assert tree.branching_at(3) == 2


def test_nodes_beyond_the_branching_limit_say_so():
    # 5 x 0.15 never reaches the 0.80 Pareto target, so all five are primary and
    # the branching limit is what actually trims the frontier.
    nodes = [_node(str(i), 0.15) for i in range(5)]
    primary, _, _ = ranking.classify(nodes, max_drivers=5)
    assert len(primary) == 5
    kept = tree.frontier_for(nodes, 1)
    assert len(kept) == 3
    dropped = [n for n in nodes if n.stop_reason == "branching_limit"]
    assert dropped, "nodes left unexpanded must record why"


def test_a_synthetic_root_lets_one_assertion_cover_the_whole_tree():
    root = tree.synthetic_root(1200.0, 1500.0, -300.0)
    assert root.depth == 0
    assert root.dimension is None
    assert root.contribution == pytest.approx(1.0)


def test_unexplained_share_reports_what_the_children_miss():
    root = tree.synthetic_root(1200.0, 1500.0, -300.0)
    root.children = [_node("A", 0.5)]
    root.children[0].absolute_change = -240.0
    # 60 of the 300 is unaccounted for.
    assert tree.unexplained_share(root) == pytest.approx(0.2)
