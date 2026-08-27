"""``get_kpi_result`` - the headline movement.

A projection of the investigation, not a computation. Every field is copied from
the persisted row or its findings payload.
"""

from typing import Any

from app.ai.tools.base import ToolContext, ToolSpec

DESCRIPTION = (
    "The KPI's value in each compared period, the change between them, and which "
    "two windows were actually compared."
)


def run(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    result = context.result
    kpi = result.get("kpi") or {}
    periods = result.get("periods") or {}
    current = periods.get("current") or {}
    previous = periods.get("previous") or {}

    return {
        "kpi_name": kpi.get("name"),
        "aggregation": kpi.get("aggregation"),
        "previous_value": kpi.get("previous_value"),
        "current_value": kpi.get("current_value"),
        "absolute_change": kpi.get("absolute_change"),
        "percentage_change": kpi.get("percent_change"),
        # Why a percentage is absent is itself a finding - a previous value of
        # zero makes one undefined rather than infinite.
        "percent_change_undefined_reason": kpi.get("percent_change_undefined_reason"),
        "direction": kpi.get("direction"),
        "severity": kpi.get("severity"),
        "comparison": kpi.get("comparison"),
        "grain": kpi.get("grain") or periods.get("grain"),
        "previous_period": {
            "label": previous.get("label"),
            "start": previous.get("start"),
            "end": previous.get("end"),
            "row_count": previous.get("row_count"),
        },
        "current_period": {
            "label": current.get("label"),
            "start": current.get("start"),
            "end": current.get("end"),
            "row_count": current.get("row_count"),
        },
        # The anchor is the data's own latest timestamp, never the wall clock. It
        # is reported because it is the answer to "why these periods?".
        "anchor": periods.get("anchor"),
        "strategy": periods.get("strategy"),
        "excluded_partial_period": periods.get("excluded_partial_period"),
        "analysis_state": context.investigation.get("analysis_state"),
    }


SPEC = ToolSpec(
    name="get_kpi_result",
    description=DESCRIPTION,
    arguments={},
    run=run,
)
