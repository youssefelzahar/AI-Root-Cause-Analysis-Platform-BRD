"""Drill-down decisions: which dimension to descend, and when to stop.

Pure logic. The engine owns the breadth-first loop because it is the only module
allowed to execute SQL; everything here decides *what* to ask for next.
"""

from dataclasses import dataclass

from app.analysis.rca.constants import (
    ABS_EPSILON,
    BRANCHING_BY_DEPTH,
    CONTRIBUTION_SUM_TOLERANCE,
    MIN_CONTRIBUTION_TO_DRILL,
    MIN_EXPLANATORY_POWER,
    MIN_ROWS_TO_DRILL,
)
from app.analysis.rca.contribution import explanatory_power
from app.analysis.rca.models import MEAN_LIKE, Classification, DriverNode
from app.db.models.enums import Aggregation


@dataclass(frozen=True)
class DimensionChoice:
    """The dimension picked for one node, and why.

    ``nodes`` stays a mutable list on a frozen dataclass deliberately: freezing
    prevents the choice being rebound, while ``ranking.classify`` still has to
    write classifications onto the nodes themselves.
    """

    dimension: str
    nodes: list[DriverNode]
    power: float | None
    split_type: str


@dataclass(frozen=True)
class DimensionCandidate:
    """A dimension that was considered at one level, chosen or not.

    Exists so a decision can say what a dimension was picked *instead of*.
    ``select_dimension`` used to sort its shortlist and return the head, which
    threw the comparison away.
    """

    dimension: str
    power: float | None
    split_type: str | None
    segment_count: int
    top_contribution: float | None
    rejected_reason: str | None = None  # None means it was eligible


@dataclass(frozen=True)
class DimensionRanking:
    chosen: DimensionChoice | None
    considered: tuple[DimensionCandidate, ...] = ()


@dataclass(frozen=True)
class TreeDrift:
    """A node whose children do not sum to it.

    ``is_pure_split`` and ``under_truncated_level`` are the two legitimate
    causes; a drift with neither set is the lost-rows bug worth acting on.
    """

    node_id: str
    depth: int
    parent_contribution: float
    children_sum: float
    is_pure_split: bool


def rank_dimensions(
    candidates: dict[str, list[DriverNode]],
    level_change: float | None,
    dimension_order: tuple[str, ...],
) -> DimensionRanking:
    """Rank every candidate dimension for one node, and pick the best.

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
    considered: list[DimensionCandidate] = []

    def top(nodes: list[DriverNode]) -> float | None:
        values = [abs(n.contribution) for n in nodes if n.contribution is not None]
        return max(values) if values else None

    for name in dimension_order:
        nodes = candidates.get(name)
        if not nodes:
            considered.append(
                DimensionCandidate(name, None, None, 0, None, "no_segments")
            )
            continue
        populated = [n for n in nodes if n.current_rows or n.previous_rows]
        if len(populated) <= 1:
            if populated:
                pure.append(DimensionChoice(name, populated, None, "pure"))
                considered.append(
                    DimensionCandidate(
                        name, None, "pure", len(populated), top(populated), "single_cell_pure_split"
                    )
                )
            else:
                considered.append(
                    DimensionCandidate(name, None, None, 0, None, "no_populated_segments")
                )
            continue
        power = explanatory_power(nodes, level_change)
        if power is not None and power >= MIN_EXPLANATORY_POWER:
            informative.append(DimensionChoice(name, nodes, power, "partition"))
            considered.append(
                DimensionCandidate(name, power, "partition", len(nodes), top(nodes), None)
            )
        else:
            considered.append(
                DimensionCandidate(
                    name, power, "partition", len(nodes), top(nodes), "below_min_explanatory_power"
                )
            )

    chosen: DimensionChoice | None = None
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
        chosen = informative[0]
    elif pure:
        chosen = pure[0]

    return DimensionRanking(chosen=chosen, considered=tuple(considered))


def select_dimension(
    candidates: dict[str, list[DriverNode]],
    level_change: float | None,
    dimension_order: tuple[str, ...],
) -> DimensionChoice | None:
    """The chosen dimension only. Behaviour unchanged; see ``rank_dimensions``."""
    return rank_dimensions(candidates, level_change, dimension_order).chosen


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


def tree_drift(node: DriverNode) -> list[TreeDrift]:
    """Every node whose children do not sum to it, at every depth.

    One implementation with two consumers: ``engine.verify_tree`` logs it, and
    the evidence validator turns it into a verdict. Keeping it in one place is
    what stops the log and the API disagreeing about whether a tree reconciles.
    """
    found: list[TreeDrift] = []
    if node.children:
        total = sum(c.contribution for c in node.children if c.contribution is not None)
        parent = node.contribution
        if parent is not None and abs(total - parent) > CONTRIBUTION_SUM_TOLERANCE:
            found.append(
                TreeDrift(
                    node_id=node.node_id,
                    depth=node.depth,
                    parent_contribution=parent,
                    children_sum=total,
                    is_pure_split=node.child_split_type == "pure",
                )
            )
        for child in node.children:
            found.extend(tree_drift(child))
    return found


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
    *,
    contribution: float | None = None,
) -> DriverNode:
    """A depth-0 node standing for the KPI as a whole.

    Having one makes "children sum to their parent" true at every level
    including the top, so a single recursive assertion covers the whole tree.

    ``contribution`` defaults to 1.0 - the root *is* the whole change. It has to
    be passed explicitly under a basis whose children do not sum to 1 in signed
    terms (gross movement), or the assertion the root exists to support fails at
    depth 0 on every such investigation.
    """
    if absolute_change in (None, 0.0):
        share = None
    else:
        share = 1.0 if contribution is None else contribution
    return DriverNode(
        node_id="",
        depth=0,
        path=(),
        dimension=None,
        value=None,
        current_value=current_value,
        previous_value=previous_value,
        absolute_change=absolute_change,
        contribution=share,
        classification=Classification.PRIMARY,
    )
