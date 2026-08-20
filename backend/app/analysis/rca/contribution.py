"""Contribution mathematics.

The requirement (PRD section on drivers) is explicit: do not rank by percentage
change. A segment's contribution is its share of the NET change, sign preserved,
so that a segment moving against the KPI is identifiable as an offsetting factor
rather than being reported as a top driver - which is precisely what the
pre-Phase-1 engine got wrong by dividing by gross movement.

Pure functions over ``SegmentTotals``. No SQL, no I/O.
"""

from app.analysis.rca.constants import (
    ABS_EPSILON,
    MIN_ROWS_FOR_MEAN_STABILITY,
    NET_TO_GROSS_MIN_RATIO,
)
from app.analysis.rca.models import (
    ADDITIVE,
    MEAN_LIKE,
    AttributionBasis,
    Classification,
    DriverNode,
    SegmentTotals,
    Totals,
    UnattributableReason,
)
from app.db.models.enums import Aggregation

# Aggregations with no valid additive decomposition. See ``basis_for``.
NON_DECOMPOSABLE: dict[Aggregation, UnattributableReason] = {
    Aggregation.MIN: UnattributableReason.ORDER_STATISTIC,
    Aggregation.MAX: UnattributableReason.ORDER_STATISTIC,
    Aggregation.MEDIAN: UnattributableReason.DISTRIBUTIONAL_STATISTIC,
    Aggregation.COUNT_DISTINCT: UnattributableReason.NON_ADDITIVE_SET_OVERLAP,
}


def _value(raw: float | None) -> float:
    return 0.0 if raw is None else raw


def percent_change(current: float | None, previous: float | None) -> tuple[float | None, str | None]:
    """Signed percent change, or None with a reason.

    The denominator is ``abs(previous)`` on purpose. With previous=-100 and
    current=-150 the naive ratio gives +50%, which reads as an improvement while
    the metric got worse. Dividing by the magnitude gives -50%, which reads
    correctly for negative-valued KPIs.
    """
    if previous is None or abs(previous) <= ABS_EPSILON:
        return None, "zero_baseline"
    return ((_value(current) - previous) / abs(previous)) * 100.0, None


def gross_movement(segments: list[SegmentTotals]) -> float:
    return sum(abs(_value(s.current_value) - _value(s.previous_value)) for s in segments)


def basis_for(
    aggregation: Aggregation,
    totals: Totals,
    segments: list[SegmentTotals],
    *,
    distinct_overlap_gap: tuple[int, int] | None = None,
) -> tuple[AttributionBasis, UnattributableReason | None]:
    """Decide how - or whether - contributions can be computed.

    ``distinct_overlap_gap`` is ``(previous, current)`` values of
    ``sum(per-group distinct) - total distinct``. Grouping partitions rows, not
    distinct key values, so a customer active in two regions is counted twice.
    When the gap is zero in both periods the dimension genuinely partitions the
    key set and COUNT_DISTINCT is additive after all - checked, never assumed.
    """
    if aggregation is Aggregation.COUNT_DISTINCT and distinct_overlap_gap is not None:
        if distinct_overlap_gap[0] == 0 and distinct_overlap_gap[1] == 0:
            return AttributionBasis.NET_CHANGE, None

    reason = NON_DECOMPOSABLE.get(aggregation)
    if reason is not None:
        return AttributionBasis.UNATTRIBUTABLE, reason

    delta = totals.absolute_change
    if delta is None:
        return AttributionBasis.UNATTRIBUTABLE, UnattributableReason.NO_PREVIOUS_PERIOD

    gross = gross_movement(segments)
    if abs(delta) <= ABS_EPSILON and gross <= ABS_EPSILON:
        # Nothing moved anywhere. Reported as no_change by the caller.
        return (
            AttributionBasis.MIX_RATE if aggregation is Aggregation.AVG else AttributionBasis.NET_CHANGE
        ), None

    if gross > ABS_EPSILON and abs(delta) / gross < NET_TO_GROSS_MIN_RATIO:
        # Large movements cancelling out. Dividing by the tiny net would report
        # contributions in the thousands of percent, so switch to shares of
        # gross movement and say so.
        return AttributionBasis.GROSS_MOVEMENT, None

    if aggregation is Aggregation.AVG:
        return AttributionBasis.MIX_RATE, None
    if aggregation in ADDITIVE:
        return AttributionBasis.NET_CHANGE, None
    return AttributionBasis.UNATTRIBUTABLE, UnattributableReason.ORDER_STATISTIC


def _avg_effects(
    segment: SegmentTotals, totals: Totals
) -> tuple[float, float]:
    """Centred Bennet decomposition of an average change into rate and mix.

    With weights w = n/N and group means m::

        rate = mean(w) * (m1 - m0)              the group's own average moved
        mix  = (mean(m) - mean(A)) * (w1 - w0)  its share of volume moved

    These are exact: ``mean(w)*dm + mean(m)*dw == w1*m1 - w0*m0``, and summing
    over groups telescopes to the total change because ``sum(dw) == 0``.

    Centring by the overall average matters. Uncentred, if every group has the
    same mean, shifting volume between groups leaves the total average untouched
    yet reports large equal-and-opposite per-group effects. Subtracting the mean
    level makes that case exactly zero, and costs nothing because ``sum(dw)`` is
    zero either way.
    """
    n0, n1 = segment.previous_count, segment.current_count
    total_n0, total_n1 = totals.previous_count, totals.current_count

    w0 = (n0 / total_n0) if total_n0 else 0.0
    w1 = (n1 / total_n1) if total_n1 else 0.0

    # A segment with no rows in a period has no average of its own there, so it
    # cannot have moved one: holding the mean flat puts its whole effect into
    # mix, which is the correct reading of "it changed the average by arriving".
    # Keyed on the count rather than the value, because a real average of 0.0 is
    # not the same as no average at all.
    m0 = None if n0 == 0 else segment.previous_value
    m1 = None if n1 == 0 else segment.current_value
    if m0 is None:
        m0 = m1 if m1 is not None else 0.0
    if m1 is None:
        m1 = m0

    a0 = totals.previous_value or 0.0
    a1 = totals.current_value or 0.0

    mean_w = (w0 + w1) / 2.0
    mean_m = (m0 + m1) / 2.0
    mean_a = (a0 + a1) / 2.0

    rate = mean_w * (m1 - m0)
    mix = (mean_m - mean_a) * (w1 - w0)
    return rate, mix


def _support_flags(
    segment: SegmentTotals, aggregation: Aggregation
) -> tuple[bool, str | None]:
    """Whether a segment is too thin to headline.

    Only mean-like aggregations get a row-count gate. For SUM and COUNT the
    decomposition is an accounting identity rather than an estimate: three rows
    that moved the total by 40% genuinely moved it by 40%, and suppressing that
    would hide the single large contract that really is the answer.
    """
    if aggregation not in MEAN_LIKE:
        return False, None
    if segment.total_rows < MIN_ROWS_FOR_MEAN_STABILITY:
        return True, "few_rows_for_mean"
    return False, None


def build_nodes(
    segments: list[SegmentTotals],
    totals: Totals,
    global_change: float | None,
    aggregation: Aggregation,
    basis: AttributionBasis,
    unattributable_reason: UnattributableReason | None,
    *,
    depth: int = 1,
    parent_path: tuple[tuple[str, str | None], ...] = (),
    parent_change: float | None = None,
) -> list[DriverNode]:
    """Turn one level's segment totals into nodes carrying full evidence.

    ``global_change`` is the denominator for every contribution at every depth.
    A child's share is deliberately NOT relative to its parent: a node holding
    100% of a parent that is itself 2% of the total would otherwise render as
    "100%" and read as the headline cause. The local view is carried separately
    as ``share_of_parent_change``.
    """
    gross = gross_movement(segments)
    level_change = totals.absolute_change
    denominator = global_change

    nodes: list[DriverNode] = []
    for segment in segments:
        current = segment.current_value
        previous = segment.previous_value
        change = _value(current) - _value(previous)
        pct, pct_reason = percent_change(current, previous)

        contribution: float | None = None
        rate: float | None = None
        mix: float | None = None

        if basis is AttributionBasis.UNATTRIBUTABLE:
            contribution = None
        elif basis is AttributionBasis.GROSS_MOVEMENT:
            contribution = (change / gross) if gross > ABS_EPSILON else None
        elif basis is AttributionBasis.MIX_RATE:
            rate, mix = _avg_effects(segment, totals)
            contribution = (
                ((rate + mix) / denominator)
                if denominator is not None and abs(denominator) > ABS_EPSILON
                else None
            )
        else:
            contribution = (
                (change / denominator)
                if denominator is not None and abs(denominator) > ABS_EPSILON
                else None
            )

        share_of_parent = None
        reference = parent_change if parent_change is not None else level_change
        if reference is not None and abs(reference) > ABS_EPSILON:
            share_of_parent = change / reference

        # What this segment's change would have been if it had simply tracked
        # its share of the baseline, and the surprise on top of that.
        previous_share = None
        if totals.previous_value not in (None, 0) and abs(totals.previous_value) > ABS_EPSILON:
            previous_share = _value(previous) / totals.previous_value
        current_share = None
        if totals.current_value not in (None, 0) and abs(totals.current_value) > ABS_EPSILON:
            current_share = _value(current) / totals.current_value

        expected = None
        excess = None
        if previous_share is not None and level_change is not None:
            expected = level_change * previous_share
            excess = change - expected

        low_support, support_reason = _support_flags(segment, aggregation)
        label = segment.value if segment.value is not None else "(none)"

        nodes.append(
            DriverNode(
                node_id="|".join(
                    [f"{d}={v if v is not None else '(none)'}" for d, v in parent_path]
                    + [f"{segment.dimension}={label}"]
                ),
                depth=depth,
                path=parent_path + ((segment.dimension, segment.value),),
                dimension=segment.dimension,
                value=segment.value,
                value_is_null=segment.value_is_null,
                current_value=current,
                previous_value=previous,
                absolute_change=change,
                percent_change=pct,
                percent_change_undefined_reason=pct_reason,
                contribution=contribution,
                contribution_basis=basis,
                unattributable_reason=unattributable_reason,
                share_of_parent_change=share_of_parent,
                rate_effect=rate,
                mix_effect=mix,
                current_count=segment.current_count,
                previous_count=segment.previous_count,
                current_rows=segment.current_rows,
                previous_rows=segment.previous_rows,
                current_share=current_share,
                previous_share=previous_share,
                expected_change=expected,
                excess_change=excess,
                is_new_segment=segment.is_new,
                is_lost_segment=segment.is_lost,
                low_support=low_support,
                support_reason=support_reason,
            )
        )

    return nodes


def contribution_sum(nodes: list[DriverNode]) -> float | None:
    """The invariant to watch.

    Should be 1.0. Drift means rows were lost - a dropped null group, or a
    truncated dimension whose residual bucket went missing. This is reported
    rather than normalised away, because re-normalising would hide the bug
    permanently.
    """
    values = [n.contribution for n in nodes if n.contribution is not None]
    if not values:
        return None
    return sum(values)


def explanatory_power(nodes: list[DriverNode], level_change: float | None) -> float | None:
    """How much this dimension deviates from "everything moved proportionally".

    E = sum |actual_j - expected_j| / |total change|, where expected is the
    change each cell would have seen had it tracked its baseline share.

    E == 0 means every cell moved in proportion to its size, so the dimension
    explains nothing about where the change came from. Ranking dimensions by
    their strongest single value instead would systematically favour
    high-cardinality dimensions that happen to contain one large segment.
    """
    if level_change is None or abs(level_change) <= ABS_EPSILON:
        return None
    deviation = sum(
        abs(n.excess_change) for n in nodes if n.excess_change is not None and not n.is_other_bucket
    )
    return deviation / abs(level_change)


def mark_residual(node: DriverNode) -> DriverNode:
    node.is_other_bucket = True
    node.classification = Classification.RESIDUAL
    return node
