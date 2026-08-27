"""Turning a bundle into text: for the prompt, and for the fallback answer.

Number formatting is imported from ``app.analysis.investigation.evidence`` rather
than reimplemented. Not only to avoid duplication - it means a figure in an AI
answer is written exactly as the evidence record writes it, so "23.1%" in the prose
and "23.1%" in the claim are the same string. The numeric check downstream compares
values rather than text, but a reader comparing the two should not see 23.08 in one
place and 23.1 in the other.

The template answer here is what a reader gets when the model is unavailable or its
prose failed verification. It is deliberately plain and deliberately complete: the
whole point of the structured pipeline is that a useful answer does not require a
working LLM.
"""

from app.ai.constants import CAUSAL_CLAIM_CAVEAT, NULL_VALUE_LABEL
from app.ai.models import EvidenceBundle, GroundedDriver
from app.analysis.investigation.evidence import number, percent, signed


def segment_label(driver: GroundedDriver) -> str:
    return driver.value or NULL_VALUE_LABEL


def _driver_line(driver: GroundedDriver) -> str:
    parts = [f"{driver.dimension} {segment_label(driver)}: {signed(driver.absolute_change)}"]
    if driver.contribution_percentage is not None:
        parts.append(f"{percent(driver.contribution_percentage, digits=0)} of the movement")
    parts.append(driver.classification)
    if driver.is_lost_segment:
        parts.append("no rows at all in the current period")
    elif driver.is_new_segment:
        parts.append("no rows at all in the previous period")
    return " | ".join(parts)


def bundle_as_text(bundle: EvidenceBundle) -> str:
    """The evidence, as labelled lines.

    Lines rather than JSON. A small model reproduces a number more faithfully from
    ``absolute change: -300`` than from nested braces, and the field names double
    as the vocabulary the answer should use.
    """
    lines: list[str] = [f"KPI: {bundle.kpi_name}"]

    if bundle.previous_period and bundle.current_period:
        lines.append(f"previous period: {bundle.previous_period}")
        lines.append(f"current period: {bundle.current_period}")
    lines.append(f"previous value: {number(bundle.previous_value)}")
    lines.append(f"current value: {number(bundle.current_value)}")
    lines.append(f"absolute change: {signed(bundle.absolute_change)}")
    if bundle.percentage_change is not None:
        lines.append(f"percentage change: {percent(bundle.percentage_change)}")
    if bundle.direction:
        lines.append(f"direction: {bundle.direction}")
    if bundle.severity:
        lines.append(f"severity: {bundle.severity}")
    if bundle.change_pattern:
        lines.append(f"change pattern: {bundle.change_pattern}")
    if bundle.attribution_basis:
        lines.append(f"contribution basis: {bundle.attribution_basis}")

    if bundle.drivers:
        lines.append("drivers, strongest first:")
        lines.extend(f"  - {_driver_line(d)}" for d in bundle.drivers)
    elif bundle.contribution_analysed:
        lines.append("drivers: none were material enough to name")
    else:
        # Said explicitly, because silence here reads as "there were none".
        lines.append("drivers: not analysed for this question")

    if bundle.offsetting:
        lines.append("segments that moved against the KPI:")
        lines.extend(f"  - {_driver_line(d)}" for d in bundle.offsetting)

    if bundle.drill_path:
        lines.append("drill-down path: " + " -> ".join(bundle.drill_path))
    if bundle.drill_stop_reason:
        lines.append(f"drill-down stopped because: {bundle.drill_stop_reason}")
    if bundle.anomaly_summary:
        lines.append(f"anomaly detection: {bundle.anomaly_summary}")

    for fact in bundle.facts:
        lines.append(f"{fact.label}: {fact.formatted}")

    if bundle.evidence_quality:
        lines.append(f"evidence quality: {bundle.evidence_quality}")
    if bundle.reconciliation_status:
        lines.append(f"contribution reconciliation: {bundle.reconciliation_status}")
    for assumption in bundle.assumptions:
        lines.append(f"assumption: {assumption}")
    for limitation in bundle.limitations:
        lines.append(f"limitation: {limitation}")

    return "\n".join(lines)


def template_answer(bundle: EvidenceBundle) -> str:
    """A complete answer built by code, with no model involved.

    Used when the LLM is unavailable, or when its prose quoted a number the
    evidence does not contain. Every sentence is assembled from bundle fields, so
    it cannot state anything the engines did not measure - the same guarantee the
    generated path only achieves by being checked.
    """
    sentences: list[str] = []

    if bundle.absolute_change is None:
        sentences.append(
            f"{bundle.kpi_name} could not be compared across the two periods "
            f"({bundle.analysis_state.replace('_', ' ')})."
        )
    else:
        movement = "did not change" if bundle.direction == "flat" else (
            "decreased" if bundle.absolute_change < 0 else "increased"
        )
        headline = (
            f"{bundle.kpi_name} {movement} from {number(bundle.previous_value)} to "
            f"{number(bundle.current_value)}, a change of {signed(bundle.absolute_change)}"
        )
        if bundle.percentage_change is not None:
            headline += f" ({percent(bundle.percentage_change)})"
        sentences.append(headline + ".")

    if bundle.previous_period and bundle.current_period:
        sentences.append(
            f"That compares {bundle.current_period} against {bundle.previous_period}."
        )

    if bundle.drivers:
        top = bundle.drivers[0]
        share = (
            f" and accounts for {percent(top.contribution_percentage, digits=0)} of the movement"
            if top.contribution_percentage is not None
            else ""
        )
        lifecycle = ""
        if top.is_lost_segment:
            lifecycle = ", a segment with no rows at all in the current period"
        elif top.is_new_segment:
            lifecycle = ", a segment with no rows at all in the previous period"
        sentences.append(
            f"The largest contributor is {top.dimension} {segment_label(top)}, which moved "
            f"{signed(top.absolute_change)}{share}{lifecycle}."
        )
        if len(bundle.drivers) > 1:
            others = ", ".join(
                f"{d.dimension} {segment_label(d)} ({signed(d.absolute_change)})"
                for d in bundle.drivers[1:4]
            )
            sentences.append(f"Also contributing: {others}.")
    elif bundle.contribution_analysed and bundle.absolute_change is not None:
        # Only claimed when the contribution step ran. Otherwise this would report
        # an absence nobody checked for.
        sentences.append("No individual segment was material enough to name as a driver.")

    if bundle.offsetting:
        first = bundle.offsetting[0]
        sentences.append(
            f"Moving the other way, {first.dimension} {segment_label(first)} changed "
            f"{signed(first.absolute_change)}, offsetting part of the movement."
        )

    if len(bundle.drill_path) > 1:
        sentences.append("Within that, the change concentrates in " + " -> ".join(bundle.drill_path) + ".")

    if bundle.anomaly_summary:
        sentences.append(bundle.anomaly_summary)

    sentences.append(CAUSAL_CLAIM_CAVEAT)
    for assumption in bundle.assumptions:
        sentences.append(assumption)
    return " ".join(sentences)
