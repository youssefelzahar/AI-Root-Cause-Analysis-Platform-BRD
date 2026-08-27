"""``contribution_analysis`` - who moved the KPI, and by how much of it.

Reads the classified driver lists the ranking already produced. Two things travel
with the numbers because an answer is wrong without them:

The **basis**. A contribution means something different under each one, and under
``gross_movement`` it is a share of total absolute movement rather than of the net
change - because large movements cancelled and dividing by the tiny net would report
contributions in the thousands of percent.

The **caveat that these lists need not sum to 100%**. They are a selection; the
complete decomposition is what reconciles, and that verdict is reported separately.
"""

from typing import Any

from app.ai.tools.base import ToolContext, ToolSpec

DESCRIPTION = (
    "The ranked drivers behind the movement with each one's share of it, the "
    "segments that moved the other way, and the basis those shares are computed on."
)


def _driver(node: dict[str, Any]) -> dict[str, Any]:
    contribution = node.get("contribution")
    return {
        "dimension": node.get("dimension"),
        "value": node.get("value"),
        "value_is_null": node.get("value_is_null", False),
        "previous_value": node.get("previous_value"),
        "current_value": node.get("current_value"),
        "absolute_change": node.get("absolute_change"),
        # Percentage points of the movement, so an answer never has to multiply.
        "contribution_percentage": None if contribution is None else contribution * 100.0,
        "classification": node.get("classification"),
        "rank": node.get("rank", 0),
        "is_new_segment": node.get("is_new_segment", False),
        "is_lost_segment": node.get("is_lost_segment", False),
        "low_support": node.get("low_support", False),
        "support_reason": node.get("support_reason"),
        # What the change would have been at this segment's baseline share, and the
        # surprise on top - the difference between "it shrank because everything
        # shrank" and "it shrank on its own".
        "expected_change": node.get("expected_change"),
        "excess_change": node.get("excess_change"),
    }


def run(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    result = context.result
    attribution = result.get("attribution") or {}
    investigation = context.investigation

    return {
        "basis": attribution.get("basis"),
        "unattributable_reason": attribution.get("unattributable_reason"),
        "change_pattern": attribution.get("change_pattern"),
        "has_offsetting": attribution.get("has_offsetting", False),
        "primary_drivers": [_driver(n) for n in (result.get("primary_drivers") or [])],
        "secondary_drivers": [_driver(n) for n in (result.get("secondary_drivers") or [])],
        "offsetting_factors": [_driver(n) for n in (result.get("offsetting_factors") or [])],
        # The verdict on the *complete* decomposition, which is a different
        # question from whether the lists above sum to anything.
        "reconciliation_status": investigation.get("reconciliation_status"),
        "contribution_sum": investigation.get("contribution_sum"),
        "selection_caveat": (
            "These lists are a selection of the material segments and are not expected to "
            "sum to 100%. The complete decomposition, including every immaterial segment and "
            "the grouped remainder, is what the reconciliation verdict covers."
        ),
    }


SPEC = ToolSpec(
    name="contribution_analysis",
    description=DESCRIPTION,
    arguments={},
    run=run,
)
