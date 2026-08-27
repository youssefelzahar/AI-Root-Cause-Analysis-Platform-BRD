"""Step 5: the tool results, as the only numbers an answer may use. No model.

The bundle is the boundary. What is in it can appear in an answer; what is not
cannot, because the model is never shown anything else. That is a stronger
guarantee than any instruction in a prompt, and it is why the bundle is built here
from tool payloads rather than assembled inside the explanation call.

Every driver and every fact carries the ``evidence_id`` of the record it came from,
so a claim in the prose can be traced to a persisted row with its own provenance
and verbatim SQL. A number with no evidence id is one of the investigation's own
typed columns - the KPI values, the period labels - which are equally persisted.

This module also reconciles the *period claim*: the question may have named July
while the engine compared June against May, because periods are anchored on the
data's own latest timestamp. Where the two disagree the disagreement becomes an
assumption the answer must state.
"""

import uuid
from datetime import datetime

from app.ai.constants import TOOL_DETECT_ANOMALY, TOOL_DRILL_DOWN
from app.ai.models import (
    EvidenceBundle,
    GroundedDriver,
    GroundedFact,
    ResolvedContext,
    ToolResult,
)
from app.analysis.investigation.evidence import number, percent

# Evidence types whose claim is about one segment, so a driver can be matched to
# the record behind it.
_SEGMENT_EVIDENCE = ("contribution", "gone_segment", "new_segment", "offsetting_factor")

# Lower case, because every comparison against a question is lower cased.
MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def _payload(results: tuple[ToolResult, ...], tool: str) -> dict:
    for result in results:
        if result.tool == tool and result.ok:
            return result.payload
    return {}


def _evidence_index(evidence: list[dict]) -> dict[tuple[str | None, str | None], str]:
    """``(dimension, value) -> evidence_id`` for the segment-level claims.

    First match wins, and the types are searched in order, so a segment that has
    both a contribution record and a gone-segment record links to the
    contribution - the claim a reader wants when the number in the prose is a
    share.
    """
    index: dict[tuple[str | None, str | None], str] = {}
    for evidence_type in _SEGMENT_EVIDENCE:
        for record in evidence:
            if record.get("evidence_type") != evidence_type:
                continue
            key = (record.get("dimension"), record.get("dimension_value"))
            index.setdefault(key, str(record.get("id")))
    return index


def _driver(node: dict, index: dict[tuple[str | None, str | None], str]) -> GroundedDriver:
    return GroundedDriver(
        dimension=node.get("dimension") or "",
        value=node.get("value") or "",
        absolute_change=node.get("absolute_change"),
        contribution_percentage=node.get("contribution_percentage"),
        classification=node.get("classification") or "",
        rank=node.get("rank") or 0,
        is_new_segment=node.get("is_new_segment", False),
        is_lost_segment=node.get("is_lost_segment", False),
        evidence_id=index.get((node.get("dimension"), node.get("value"))),
    )


def _window_label(period: dict) -> str | None:
    """A half-open window, written so the exclusive end is visible.

    Built from ``start`` and ``end`` rather than from the engine's ``label``, which
    is the role name ``"current"`` or ``"previous"`` - useful inside the engine,
    useless in a sentence. Formatted the way the evidence records format it, so a
    period reads identically in an answer and in the claim behind it.
    """
    start, end = period.get("start"), period.get("end")
    if not start or not end:
        return None
    return f"{str(start)[:10]} to {str(end)[:10]} (exclusive)"


def _period_tokens(period: dict) -> set[str]:
    """The ways a reader might name this window.

    The ISO dates, plus the month name and year of each month the window covers, so
    a question saying "June" is recognised as matching ``2026-06-01 to 2026-07-01``.
    Without this every month-named question would produce a substitution note,
    including the ones that named the right month.

    Not date arithmetic on the reader's behalf - nothing here is computed *from*.
    It only decides whether to add a sentence saying the periods differ.
    """
    tokens: set[str] = set()
    start, end = period.get("start"), period.get("end")
    if not start or not end:
        return tokens
    try:
        first = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        last = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return tokens

    tokens.add(str(start)[:10])
    tokens.add(str(end)[:10])
    tokens.add(str(start)[:7])
    cursor = first
    # Bounded: the window is at most a year at the coarsest supported grain, and a
    # runaway would be a period bug rather than a long report.
    for _ in range(14):
        if cursor >= last:
            break
        tokens.add(MONTH_NAMES[cursor.month - 1])
        tokens.add(f"{MONTH_NAMES[cursor.month - 1]} {cursor.year}")
        tokens.add(str(cursor.year))
        tokens.add(f"q{(cursor.month - 1) // 3 + 1}")
        cursor = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12
            else cursor.replace(month=cursor.month + 1)
        )
    return tokens


def _period_assumption(
    claim: str | None,
    previous: dict,
    current: dict,
    previous_label: str | None,
    current_label: str | None,
) -> str | None:
    """Whether the question's period is the one that was analysed.

    The engine's resolved windows are the truth about what was compared. When the
    question named something they do not cover, the answer is told to say so -
    rather than the period being silently substituted, which is indistinguishable
    from a wrong answer.
    """
    if not claim:
        return None
    needle = claim.strip().lower()
    if not needle:
        return None

    covered = _period_tokens(current) | _period_tokens(previous)
    if any(token and token in needle for token in covered) or needle in covered:
        return None

    windows = (
        f"{current_label} against {previous_label}"
        if current_label and previous_label
        else "this dataset's own two most recent complete periods"
    )
    return (
        f"You asked about {claim!r}, but the comparison actually made was {windows}. The "
        "periods come from this dataset's own latest complete period and the KPI's "
        "comparison setting, so a period named in a question is reported against rather "
        "than analysed directly."
    )


def build(
    *,
    question: str,
    context: ResolvedContext,
    investigation: dict,
    evidence: list[dict],
    results: tuple[ToolResult, ...],
    extra_limitations: tuple[str, ...] = (),
) -> EvidenceBundle:
    """Everything the explanation is allowed to know."""
    kpi = _payload(results, "get_kpi_result")
    contribution = _payload(results, "contribution_analysis")
    drill = _payload(results, TOOL_DRILL_DOWN)
    anomaly = _payload(results, TOOL_DETECT_ANOMALY)
    meta = _payload(results, "get_investigation")

    index = _evidence_index(evidence)
    previous_window = kpi.get("previous_period") or {}
    current_window = kpi.get("current_period") or {}
    previous_label = _window_label(previous_window)
    current_label = _window_label(current_window)

    drivers = [
        _driver(node, index)
        for node in (contribution.get("primary_drivers") or [])
        + (contribution.get("secondary_drivers") or [])
    ]
    offsetting = [_driver(node, index) for node in (contribution.get("offsetting_factors") or [])]

    facts: list[GroundedFact] = []
    rows_scanned = meta.get("rows_scanned") or investigation.get("rows_scanned")
    if rows_scanned:
        facts.append(
            GroundedFact(
                label="rows scanned",
                value=float(rows_scanned),
                formatted=number(float(rows_scanned)),
            )
        )
    contribution_sum = contribution.get("contribution_sum")
    if contribution_sum is not None:
        facts.append(
            GroundedFact(
                label="share of the movement the full decomposition accounts for",
                value=contribution_sum * 100.0,
                formatted=percent(contribution_sum * 100.0),
            )
        )

    # Only genuinely-anomalous periods become facts. A quiet series has nothing to
    # say, and an "anomaly: none" line in a prompt invites the model to discuss it.
    for observation in (anomaly.get("anomalies") or [])[:3]:
        score = observation.get("anomaly_score")
        facts.append(
            GroundedFact(
                label=(
                    f"anomaly at {observation.get('period_start')} "
                    f"({observation.get('severity')}, {observation.get('direction')})"
                ),
                value=score,
                formatted=f"{number(score)} deviations from its trailing baseline",
            )
        )

    drill_path: list[str] = []
    for node in drill.get("path") or []:
        dimension = node.get("dimension")
        value = node.get("value")
        if dimension and value:
            drill_path.append(f"{dimension} {value}")

    assumptions = list(context.assumptions)
    period_note = _period_assumption(
        context.period_claim,
        previous_window,
        current_window,
        previous_label,
        current_label,
    )
    # ``resolve`` adds a generic note as soon as a question names a period, because
    # at that point the resolved windows are not known yet. They are known here, so
    # the generic note is always dropped: either it is replaced by one naming the
    # actual windows, or the question named the period that really was analysed and
    # there is nothing to caveat.
    assumptions = [a for a in assumptions if not a.startswith("The comparison windows")]
    if period_note:
        assumptions.insert(0, period_note)

    limitations = list(meta.get("limitations") or investigation.get("limitations") or [])
    limitations.extend(extra_limitations)
    if drill and not drill.get("found") and drill.get("detail"):
        limitations.append(drill["detail"])
    if anomaly and anomaly.get("ran") is False and anomaly.get("detail"):
        limitations.append(anomaly["detail"])

    return EvidenceBundle(
        question=question,
        kpi_name=kpi.get("kpi_name") or context.kpi_name or "the KPI",
        investigation_id=uuid.UUID(str(investigation.get("id"))),
        investigation_status=str(investigation.get("status")),
        analysis_state=str(investigation.get("analysis_state") or "unknown"),
        previous_period=previous_label,
        current_period=current_label,
        previous_value=kpi.get("previous_value"),
        current_value=kpi.get("current_value"),
        absolute_change=kpi.get("absolute_change"),
        percentage_change=kpi.get("percentage_change"),
        direction=kpi.get("direction"),
        severity=kpi.get("severity"),
        attribution_basis=contribution.get("basis"),
        change_pattern=contribution.get("change_pattern"),
        drivers=tuple(drivers),
        offsetting=tuple(offsetting),
        # Truthy only when the step ran: an empty payload means it did not, and an
        # empty driver list then says nothing about materiality.
        contribution_analysed=bool(contribution),
        drill_path=tuple(drill_path),
        drill_stop_reason=(drill.get("node") or {}).get("stop_reason_explained"),
        anomaly_summary=anomaly.get("summary") if anomaly.get("ran") else None,
        facts=tuple(facts),
        evidence_quality=meta.get("evidence_quality") or investigation.get("evidence_quality"),
        reconciliation_status=(
            contribution.get("reconciliation_status") or investigation.get("reconciliation_status")
        ),
        limitations=tuple(dict.fromkeys(limitations)),
        assumptions=tuple(dict.fromkeys(assumptions)),
    )
