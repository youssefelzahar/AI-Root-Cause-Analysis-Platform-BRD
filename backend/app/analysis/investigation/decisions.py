"""The Investigation Decision Trace: why the system chose what it chose.

Every reason here is assembled from fields the engines already recorded - a
node's contribution and rank, a dimension's explanatory power, a stop reason, an
attribution basis. Nothing is inferred after the fact, which is what makes the
trace an account of what happened rather than a plausible story about it.

Each record carries both a sentence for a reader and the numbers it was decided
on, so "the threshold was reached" can always be expanded into *which* threshold
and *what* value.
"""

from app.analysis.investigation.constants import (
    STOP_REASON_CATEGORIES,
    STOP_REASON_SENTENCES,
)
from app.analysis.investigation.evidence import number, percent, segment_label, signed
from app.analysis.rca import ranking
from app.analysis.rca.constants import (
    MIN_CONTRIBUTION_TO_DRILL,
    MIN_EXPLANATORY_POWER,
    MIN_ROWS_TO_DRILL,
    NET_TO_GROSS_MIN_RATIO,
)
from app.analysis.rca.models import (
    AttributionBasis,
    ChangePattern,
    Classification,
    DriverNode,
    RcaResult,
)
from app.analysis.rca.tree import DimensionRanking, branching_at
from app.analysis.trace import DecisionKind, DecisionRecord, Probe

BASIS_SENTENCES = {
    AttributionBasis.NET_CHANGE: (
        "the net movement is a large enough share of the gross movement for a net share to "
        "mean something"
    ),
    AttributionBasis.MIX_RATE: (
        "an average was decomposed into a rate effect and a mix effect, because a mean can move "
        "either because its parts moved or because their weights did"
    ),
    AttributionBasis.GROSS_MOVEMENT: (
        "large movements cancelled out, so contributions are shares of total absolute movement "
        "rather than of the tiny net change - dividing by that net would report contributions in "
        "the thousands of percent"
    ),
    AttributionBasis.UNATTRIBUTABLE: (
        "this aggregation cannot be split across segments in a way that adds up, so no "
        "contribution is claimed at all"
    ),
}


def record_period_resolution(probe: Probe, result: RcaResult) -> None:
    periods = result.periods
    if periods is None:
        return
    why = (
        f"{periods.strategy} at {periods.grain.value} grain, anchored on the dataset's latest "
        f"timestamp {periods.anchor:%Y-%m-%d} rather than the wall clock, so the same data "
        f"always yields the same two windows."
    )
    if periods.excluded_partial_period is not None:
        why += (
            " The newest bucket was incomplete and was excluded, so a partly-collected period is "
            "not reported as a collapse."
        )
    else:
        why += " The newest bucket was complete, so nothing was excluded."

    probe.record(
        DecisionRecord(
            kind=DecisionKind.PERIOD_RESOLVED,
            subject=f"{periods.previous.start:%Y-%m-%d} vs {periods.current.start:%Y-%m-%d}",
            outcome="resolved",
            reason_code=periods.strategy,
            why=why,
            inputs={
                "grain": periods.grain.value,
                "strategy": periods.strategy,
                "anchor": periods.anchor.isoformat(),
                "previous_start": periods.previous.start.isoformat(),
                "previous_end": periods.previous.end.isoformat(),
                "current_start": periods.current.start.isoformat(),
                "current_end": periods.current.end.isoformat(),
                "excluded_partial_period": (
                    periods.excluded_partial_period.start.isoformat()
                    if periods.excluded_partial_period
                    else None
                ),
            },
        )
    )


def record_basis(probe: Probe, result: RcaResult) -> None:
    """Why contributions mean what they mean.

    Worth its own decision because the basis changes the interpretation of every
    contribution number on the page, and a reader who does not know which one was
    used cannot read them correctly.
    """
    attribution = result.attribution
    basis = attribution.basis
    why = f"{basis.value} basis: {BASIS_SENTENCES.get(basis, 'selected by the engine')}."
    if attribution.unattributable_reason is not None:
        why += f" Reason: {attribution.unattributable_reason.value}."

    probe.record(
        DecisionRecord(
            kind=DecisionKind.BASIS_SELECTED,
            subject=basis.value,
            outcome="selected",
            reason_code=(
                attribution.unattributable_reason.value
                if attribution.unattributable_reason
                else basis.value
            ),
            why=why,
            inputs={
                "basis": basis.value,
                "aggregation": result.kpi.aggregation,
                "net_to_gross_min_ratio": NET_TO_GROSS_MIN_RATIO,
                "additivity_verified": attribution.additivity_verified,
            },
        )
    )


def record_dimension_choice(
    probe: Probe,
    ranked: DimensionRanking,
    *,
    depth: int,
    parent: DriverNode | None = None,
) -> None:
    """Which dimension was descended, and what it was chosen instead of."""
    chosen = ranked.chosen
    considered = ranked.considered
    within = (
        f"within {parent.dimension} = {segment_label(parent)}, " if parent is not None else ""
    )

    if chosen is None:
        names = ", ".join(c.dimension for c in considered) or "none"
        probe.record(
            DecisionRecord(
                kind=DecisionKind.DIMENSION_SELECTED,
                subject="none",
                outcome="stopped",
                reason_code="no_informative_dimension",
                why=(
                    f"No dimension divided this level into segments that behaved differently "
                    f"({within}considered: {names}), so it was not split further."
                ),
                depth=depth,
                node_id=parent.node_id if parent is not None else None,
                inputs={
                    "considered": [
                        {
                            "dimension": c.dimension,
                            "power": c.power,
                            "rejected_reason": c.rejected_reason,
                        }
                        for c in considered
                    ],
                    "min_explanatory_power": MIN_EXPLANATORY_POWER,
                },
            )
        )
        return

    eligible = [c for c in considered if c.rejected_reason is None and c.dimension != chosen.dimension]
    runners = ", ".join(f"{c.dimension} {percent((c.power or 0) * 100, digits=0)}" for c in eligible)

    if chosen.split_type == "pure":
        why = (
            f"{chosen.dimension} is the only populated value at this level - the whole segment "
            f"shares it, which is a real finding rather than an explanatory-power win."
        )
        reason_code = "pure_split"
    else:
        why = (
            f"{chosen.dimension} explains {percent((chosen.power or 0) * 100, digits=0)} of how "
            f"differently the segments moved from a proportional split, the highest of "
            f"{len(considered)} considered"
        )
        why += f" ({runners})." if runners else "."
        reason_code = "highest_explanatory_power"

    probe.record(
        DecisionRecord(
            kind=DecisionKind.DIMENSION_SELECTED,
            subject=chosen.dimension,
            outcome="selected",
            reason_code=reason_code,
            why=f"{within}{why[0].lower() + why[1:]}" if within else why,
            dimension=chosen.dimension,
            depth=depth,
            node_id=parent.node_id if parent is not None else None,
            inputs={
                "chosen": chosen.dimension,
                "power": chosen.power,
                "split_type": chosen.split_type,
                "segment_count": len(chosen.nodes),
                "considered": [
                    {
                        "dimension": c.dimension,
                        "power": c.power,
                        "split_type": c.split_type,
                        "segment_count": c.segment_count,
                        "top_contribution": c.top_contribution,
                        "rejected_reason": c.rejected_reason,
                    }
                    for c in considered
                ],
                "min_explanatory_power": MIN_EXPLANATORY_POWER,
            },
        )
    )


def record_segment_selection(probe: Probe, result: RcaResult) -> None:
    """Why each named driver was named."""
    for node in list(result.primary_drivers) + list(result.secondary_drivers):
        _record_segment(probe, node, result)
    for node in result.offsetting_factors:
        _record_segment(probe, node, result)


def _record_segment(probe: Probe, node: DriverNode, result: RcaResult) -> None:
    status = "GONE" if node.is_lost_segment else ("NEW" if node.is_new_segment else "present")
    facts = []
    if node.contribution is not None:
        facts.append(f"contribution = {percent(node.contribution * 100, digits=0)}")
    if node.rank:
        facts.append(f"rank = #{node.rank}")
    facts.append(f"absolute change = {signed(node.absolute_change)}")
    facts.append(f"status = {status}")
    if node.low_support:
        facts.append("support = few rows")

    probe.record(
        DecisionRecord(
            kind=DecisionKind.SEGMENT_SELECTED,
            subject=f"{node.dimension} {segment_label(node)}",
            outcome="selected",
            reason_code=node.classification.value,
            why=", ".join(facts),
            dimension=node.dimension,
            depth=node.depth,
            node_id=node.node_id,
            inputs={
                "contribution": node.contribution,
                "rank": node.rank,
                "absolute_change": node.absolute_change,
                "percent_change": node.percent_change,
                "status": status,
                "classification": node.classification.value,
                "expected_change": node.expected_change,
                "excess_change": node.excess_change,
                "low_support": node.low_support,
                "pareto_target": result.attribution.pareto_target,
                "min_material_contribution": result.attribution.min_material_contribution,
            },
        )
    )


def record_stops(probe: Probe, result: RcaResult, *, max_tree_depth: int) -> None:
    """Every branch that ended, and the threshold that ended it.

    ``max_tree_depth`` is the limit this run actually applied, which is a
    per-request value and not necessarily the package default. Reporting the
    default here would make the recorded reason contradict what happened.
    """
    if result.tree is None:
        return

    def walk(node: DriverNode) -> None:
        if node.stop_reason and node.depth >= 1:
            sentence = STOP_REASON_SENTENCES.get(
                node.stop_reason, node.stop_reason.replace("_", " ")
            )
            probe.record(
                DecisionRecord(
                    kind=DecisionKind.DRILLDOWN_STOPPED,
                    subject=f"{node.dimension} {segment_label(node)}",
                    outcome="stopped",
                    reason_code=node.stop_reason,
                    why=sentence,
                    dimension=node.dimension,
                    depth=node.depth,
                    node_id=node.node_id,
                    inputs={
                        "stop_reason": node.stop_reason,
                        "stop_reason_category": STOP_REASON_CATEGORIES.get(node.stop_reason),
                        "contribution": node.contribution,
                        "depth": node.depth,
                        "rows": node.current_rows + node.previous_rows,
                        "max_tree_depth": max_tree_depth,
                        **_threshold_for(node.stop_reason, max_tree_depth),
                    },
                )
            )
        for child in node.children:
            walk(child)

    walk(result.tree)


def _threshold_for(stop_reason: str, max_tree_depth: int) -> dict[str, float | int]:
    """The specific limit that stopped this branch.

    "Threshold reached" on its own is not a reason, so each code names the value
    it was compared against.
    """
    if stop_reason == "contribution_immaterial":
        return {"threshold_applied": MIN_CONTRIBUTION_TO_DRILL}
    if stop_reason == "insufficient_rows":
        return {"threshold_applied": MIN_ROWS_TO_DRILL}
    if stop_reason == "max_depth_reached":
        # The depth this run applied, not the package default: the request can
        # lower it, and the recorded reason has to match what actually happened.
        return {"threshold_applied": max_tree_depth}
    if stop_reason == "branching_limit":
        return {"threshold_applied": branching_at(1)}
    return {}


def record_pattern(probe: Probe, result: RcaResult) -> None:
    """Why the change is called concentrated, broad-based or offsetting.

    Necessary rather than decorative: a broad-based verdict deliberately clears
    the driver lists, so without a recorded decision an empty result looks like a
    failure instead of a finding.
    """
    pattern = result.attribution.change_pattern
    primary = result.primary_drivers
    top = primary[0].contribution if primary and primary[0].contribution is not None else None

    if pattern is ChangePattern.BROAD_BASED:
        why = (
            "Every segment moved roughly in proportion to its size, so no single driver explains "
            "the change and none is named. An empty driver list here is the finding, not a gap."
        )
    elif pattern is ChangePattern.SINGLE_DRIVER and top is not None:
        why = (
            f"One primary driver accounts for {percent(top * 100, digits=0)} of the movement, "
            f"clearing the {percent(ranking.PARETO_TARGET * 100, digits=0)} concentration target."
        )
    elif pattern is ChangePattern.OFFSETTING:
        why = (
            "Segments moved substantially in both directions, so the net change understates how "
            "much actually moved."
        )
    elif pattern is ChangePattern.CONCENTRATED:
        why = (
            f"{len(primary)} segments together account for most of the movement, so the change is "
            "concentrated rather than broad."
        )
    else:
        why = "No segment pattern was identified for this change."

    probe.record(
        DecisionRecord(
            kind=DecisionKind.PATTERN_CLASSIFIED,
            subject=pattern.value,
            outcome="classified",
            reason_code=pattern.value,
            why=why,
            inputs={
                "pattern": pattern.value,
                "primary_count": len(primary),
                "top_contribution": top,
                "offsetting_count": len(result.offsetting_factors),
                "has_offsetting": result.attribution.has_offsetting,
                "pareto_target": result.attribution.pareto_target,
            },
        )
    )

    if pattern is ChangePattern.BROAD_BASED:
        probe.record(
            DecisionRecord(
                kind=DecisionKind.DRIVER_SUPPRESSED,
                subject="all drivers",
                outcome="suppressed",
                reason_code="broad_based_change",
                why=(
                    "Driver lists were cleared because naming a driver when every segment moved "
                    "in proportion would invent one."
                ),
                inputs={"pattern": pattern.value},
            )
        )


def record_low_support_suppression(probe: Probe, result: RcaResult) -> None:
    """Segments that are material but were demoted for having too few rows."""
    for _, nodes in result.dimension_results:
        for node in nodes:
            if not node.low_support or node.classification is not Classification.IMMATERIAL:
                continue
            if node.contribution is None or abs(node.contribution) < (
                result.attribution.min_material_contribution
            ):
                continue
            probe.record(
                DecisionRecord(
                    kind=DecisionKind.DRIVER_SUPPRESSED,
                    subject=f"{node.dimension} {segment_label(node)}",
                    outcome="suppressed",
                    reason_code="low_support",
                    why=(
                        f"Its share of the change is "
                        f"{percent(node.contribution * 100, digits=0)}, but it rests on "
                        f"{number(node.current_rows + node.previous_rows)} rows, so it is "
                        f"reported without being ranked as a driver."
                    ),
                    dimension=node.dimension,
                    depth=node.depth,
                    node_id=node.node_id,
                    inputs={
                        "contribution": node.contribution,
                        "rows": node.current_rows + node.previous_rows,
                        "support_reason": node.support_reason,
                    },
                )
            )


def record_all(probe: Probe, result: RcaResult, *, max_tree_depth: int) -> None:
    """Everything derivable from a finished result.

    The dimension choices are recorded by the engine as it makes them, because
    the runners-up are not on the result. Everything else is derived here, so the
    engine stays free of evidence-layer concerns.
    """
    record_period_resolution(probe, result)
    record_basis(probe, result)
    record_pattern(probe, result)
    record_segment_selection(probe, result)
    record_low_support_suppression(probe, result)
    record_stops(probe, result, max_tree_depth=max_tree_depth)
