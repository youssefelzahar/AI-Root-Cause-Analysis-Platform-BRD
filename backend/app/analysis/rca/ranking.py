"""Classify segments into primary drivers, secondary drivers and offsetting factors.

Direction first, then Pareto. A segment moving the same way as the KPI is a
driver; one moving against it is an offsetting factor. That distinction is the
whole point of using signed net contributions, and it is what the pre-Phase-1
engine could not express.
"""

from app.analysis.rca.constants import (
    ABS_EPSILON,
    CRITICAL_THRESHOLD,
    HIGH_THRESHOLD,
    MAX_PRIMARY_DRIVERS,
    MEDIUM_THRESHOLD,
    MIN_EXPLANATORY_POWER,
    MIN_MATERIAL_CONTRIBUTION,
    PARETO_TARGET,
)
from app.analysis.rca.models import ChangePattern, Classification, DriverNode


def severity(percent: float | None) -> str:
    """Unchanged from the pre-Phase-1 engine so the UI vocabulary still fits."""
    if percent is None:
        return "low"
    magnitude = abs(percent)
    if magnitude >= CRITICAL_THRESHOLD:
        return "critical"
    if magnitude >= HIGH_THRESHOLD:
        return "high"
    if magnitude >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def direction(change: float | None) -> str:
    if change is None:
        return "unknown"
    if abs(change) <= ABS_EPSILON:
        return "flat"
    return "up" if change > 0 else "down"


def classify(
    nodes: list[DriverNode],
    *,
    max_drivers: int = MAX_PRIMARY_DRIVERS,
) -> tuple[list[DriverNode], list[DriverNode], list[DriverNode]]:
    """Split one sibling set into (primary, secondary, offsetting).

    Pareto over the sign-aligned set with a materiality floor, rather than
    either pure form:

    * A fixed threshold alone returns *zero* drivers when a change is spread
      over twenty segments at 5% each - a false negative indistinguishable from
      "nothing explains this".
    * Cumulative Pareto alone is meaningless over signed contributions (a
      running sum of +3.0, -2.0, +0.5 never converges) and manufactures drivers
      out of uniform noise.

    Ranking and mutation happen in place: every node ends with exactly one
    classification, assigned within its own sibling set.

    Ties break on ``node_id`` so the result does not depend on the order the
    caller happened to pass: it is always a string and unique within a sibling
    set, which ``value`` is not on either count. Descending magnitude with an
    ascending tie-break needs two passes rather than ``reverse=True``.
    """
    scored = [n for n in nodes if n.contribution is not None and not n.is_other_bucket]
    aligned = sorted(
        sorted((n for n in scored if n.contribution > 0), key=lambda n: n.node_id),
        key=lambda n: n.contribution,
        reverse=True,
    )
    opposed = sorted(
        sorted((n for n in scored if n.contribution < 0), key=lambda n: n.node_id),
        key=lambda n: abs(n.contribution),
        reverse=True,
    )

    material = [n for n in aligned if n.contribution >= MIN_MATERIAL_CONTRIBUTION]

    # A segment too thin to trust its own average should not headline - but only
    # when there is a better-supported candidate to headline instead. If every
    # material segment is thin, suppressing all of them reports "nothing
    # explains this" when something demonstrably does, which is worse than
    # naming them with the low_support flag the UI can caveat.
    supported = [n for n in material if not n.low_support]
    eligible = supported if supported else material

    primary: list[DriverNode] = []
    cumulative = 0.0
    for node in eligible:
        primary.append(node)
        cumulative += node.contribution
        if cumulative >= PARETO_TARGET or len(primary) >= max_drivers:
            break

    chosen = {id(n) for n in primary}
    secondary = [n for n in material if id(n) not in chosen][:max_drivers]
    offsetting = [n for n in opposed if abs(n.contribution) >= MIN_MATERIAL_CONTRIBUTION][
        :max_drivers
    ]

    for node in primary:
        node.classification = Classification.PRIMARY
    for node in secondary:
        node.classification = Classification.SECONDARY
    for node in offsetting:
        node.classification = Classification.OFFSETTING

    # Rank by movement magnitude, so an offsetting factor can outrank a weaker
    # driver - the field answers "what moved most here", not "which driver is
    # first". Only scored segments are ranked: the residual bucket holds every
    # unlisted segment's movement at once, so ranking it would put "(other)"
    # above every real driver. Anything unranked keeps rank 0.
    for index, node in enumerate(
        sorted(
            sorted(scored, key=lambda n: n.node_id),
            key=lambda n: abs(n.contribution or 0.0),
            reverse=True,
        ),
        start=1,
    ):
        node.rank = index

    return primary, secondary, offsetting


def change_pattern(
    primary: list[DriverNode],
    offsetting: list[DriverNode],
    best_explanatory_power: float | None,
) -> ChangePattern:
    """Describe the shape of the change so the UI can narrate it honestly."""
    if best_explanatory_power is not None and best_explanatory_power < MIN_EXPLANATORY_POWER:
        # Every cell moved in proportion to its size. Naming drivers here would
        # be inventing them; saying "broad-based" is the true finding.
        return ChangePattern.BROAD_BASED
    if not primary:
        return ChangePattern.NONE
    aligned_mass = sum(abs(n.contribution or 0.0) for n in primary)
    opposed_mass = sum(abs(n.contribution or 0.0) for n in offsetting)
    if aligned_mass > 0 and opposed_mass / aligned_mass > 0.5:
        return ChangePattern.OFFSETTING
    if len(primary) == 1 and (primary[0].contribution or 0.0) >= 0.60:
        return ChangePattern.SINGLE_DRIVER
    return ChangePattern.CONCENTRATED
