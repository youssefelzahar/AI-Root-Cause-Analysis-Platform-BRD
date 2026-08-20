"""Drill-down decisions: which dimension to descend, and when to stop.

Pure logic. The engine owns the breadth-first loop because it is the only module
allowed to execute SQL; everything here decides *what* to ask for next.
"""

from app.analysis.rca.constants import (
    ABS_EPSILON,
    BRANCHING_BY_DEPTH,
    MIN_CONTRIBUTION_TO_DRILL,
    MIN_EXPLANATORY_POWER,
    MIN_ROWS_TO_DRILL,
)
from app.analysis.rca.contribution import explanatory_power
from app.analysis.rca.models import MEAN_LIKE, Classification, DriverNode
from app.db.models.enums import Aggregation


class DimensionChoice:
    """The dimension picked for one node, and why."""

    def __init__(
        self,
        dimension: str,
        nodes: list[DriverNode],
        power: float | None,
        split_type: str,
    ) -> None:
        self.dimension = dimension
        self.nodes = nodes
        self.power = power
        self.split_type = split_type


def select_dimension(
    candidates: dict[str, list[DriverNode]],
    level_change: float | None,
    dimension_order: tuple[str, ...],
) -> DimensionChoice | None:
    """Choose the dimension that best explains this node's change.

    Ranked by explanatory power - deviation from "everything moved in proportion
    to its baseline share" - rather than by the strongest single value, which
    would systematically favour whichever dimension happens to contain one large
    segment.

    A dimension with only one non-empty cell scores zero by construction (a
    single cell cannot deviate from proportional), yet "this entire segment is
    Enterprise" is a real finding. Those are handled as a separate 'pure' split
    rather than by fudging the score.
    """
    informative: list[DimensionChoice] = []
    pure: list[DimensionChoice] = []

    for name in dimension_order:
        nodes = candidates.get(name)
        if not nodes:
            continue
        populated = [n for n in nodes if n.current_rows or n.previous_rows]
        if len(populated) <= 1:
            if populated:
                pure.append(DimensionChoice(name, populated, None, "pure"))
            continue
        power = explanatory_power(nodes, level_change)
        if power is not None and power >= MIN_EXPLANATORY_POWER:
            informative.append(DimensionChoice(name, nodes, power, "partition"))

    if informative:
        order = {name: i for i, name in enumerate(dimension_order)}
        informative.sort(
            key=lambda c: (
                -(c.power or 0.0),
                -max((abs(n.contribution or 0.0) for n in c.nodes), default=0.0),
                len(c.nodes),
                order[c.dimension],
            )
        )
        return informative[0]

    return pure[0] if pure else None


def branching_at(depth: int) -> int:
    """How many nodes to expand at this depth.

    Decaying, because deeper levels are both less certain and less actionable:
    3 + 3*2 + 6*2 = 21 nodes worst case.
    """
    index = min(max(depth, 1), len(BRANCHING_BY_DEPTH)) - 1
    return BRANCHING_BY_DEPTH[index]


def drillable(
    node: DriverNode,
    remaining: tuple[str, ...],
    aggregation: Aggregation,
    max_depth: int,
) -> bool:
    """Whether this node earns a query level, recording why not if it doesn't."""
    if node.depth >= max_depth:
        node.stop_reason = "max_depth_reached"
        return False
    if not remaining:
        node.stop_reason = "no_dimensions_left"
        return False
    if node.is_other_bucket:
        node.stop_reason = "residual_bucket"
        return False
    if node.contribution is None or abs(node.contribution) < MIN_CONTRIBUTION_TO_DRILL:
        node.stop_reason = "contribution_immaterial"
        return False
    # Only mean-like aggregations gate on row count: for SUM and COUNT the
    # decomposition is exact arithmetic, so a small group is not less true.
    if aggregation in MEAN_LIKE and (node.current_rows + node.previous_rows) < MIN_ROWS_TO_DRILL:
        node.stop_reason = "insufficient_rows"
        return False
    return True


def frontier_for(nodes: list[DriverNode], depth: int) -> list[DriverNode]:
    """The nodes worth expanding at this depth: primaries, most material first."""
    primary = [n for n in nodes if n.classification is Classification.PRIMARY]
    primary.sort(key=lambda n: abs(n.contribution or 0.0), reverse=True)
    limit = branching_at(depth)
    for node in primary[limit:]:
        node.stop_reason = "branching_limit"
    return primary[:limit]


def unexplained_share(parent: DriverNode) -> float | None:
    """How much of a node's change its children fail to account for.

    Nonzero when the level was truncated or the children only partially cover
    the parent; surfacing it stops a tree implying more certainty than it has.
    """
    if parent.absolute_change is None or abs(parent.absolute_change) <= ABS_EPSILON:
        return None
    if not parent.children:
        return None
    covered = sum(
        child.absolute_change for child in parent.children if child.absolute_change is not None
    )
    return (parent.absolute_change - covered) / parent.absolute_change


def synthetic_root(
    current_value: float | None,
    previous_value: float | None,
    absolute_change: float | None,
) -> DriverNode:
    """A depth-0 node standing for the KPI as a whole.

    Having one makes "children sum to their parent" true at every level
    including the top, so a single recursive assertion covers the whole tree.
    """
    return DriverNode(
        node_id="",
        depth=0,
        path=(),
        dimension=None,
        value=None,
        current_value=current_value,
        previous_value=previous_value,
        absolute_change=absolute_change,
        contribution=1.0 if absolute_change not in (None, 0.0) else None,
        classification=Classification.PRIMARY,
    )
