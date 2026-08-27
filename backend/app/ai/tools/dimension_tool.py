"""``dimension_analysis`` - how one dimension's segments moved.

Reads ``result.dimension_results``, which holds **every** configured dimension's
depth-1 breakdown, not only the one the drill-down descended. So narrowing to a
named dimension is a selection over data that already exists - there is no
per-dimension query to run, and running one would be a second code path that could
disagree with the first.

When no dimension is named, every one is returned along with its explanatory power,
which is what lets an answer say *which* dimension separates the movement best.
"""

from typing import Any

from app.ai.tools.base import ToolContext, ToolSpec

DESCRIPTION = (
    "The segments of one dimension and how each moved between the two periods, or "
    "every configured dimension with how well it explains the movement."
)

# A dimension can hold up to 50 values plus a residual bucket. Past a handful the
# rest are immaterial by construction - the ranking already decided that - and a
# long tail in a prompt crowds out the finding.
MAX_SEGMENTS = 8


def _segment(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": node.get("value"),
        "value_is_null": node.get("value_is_null", False),
        "previous_value": node.get("previous_value"),
        "current_value": node.get("current_value"),
        "absolute_change": node.get("absolute_change"),
        "percentage_change": node.get("percent_change"),
        "contribution": node.get("contribution"),
        "classification": node.get("classification"),
        "rank": node.get("rank", 0),
        "is_new_segment": node.get("is_new_segment", False),
        "is_lost_segment": node.get("is_lost_segment", False),
        "is_other_bucket": node.get("is_other_bucket", False),
        # A segment resting on very few rows is reported, not hidden, but the fact
        # that it is thin travels with it.
        "low_support": node.get("low_support", False),
    }


def run(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    result = context.result
    wanted = arguments.get("dimension") or context.dimension
    entries = result.get("dimension_results") or []
    summaries = {
        summary.get("dimension"): summary
        for summary in (result.get("dimensions_analysed") or [])
    }

    selected = [e for e in entries if e.get("dimension") == wanted] if wanted else entries
    if wanted and not selected:
        # Asked for a dimension the analysis does not have. Saying so is the honest
        # answer; falling back to all of them silently would answer a different
        # question.
        return {
            "requested_dimension": wanted,
            "found": False,
            "available_dimensions": [e.get("dimension") for e in entries],
            "dimensions": [],
        }

    dimensions = []
    for entry in selected:
        name = entry.get("dimension")
        summary = summaries.get(name) or {}
        segments = sorted(
            entry.get("segments") or [],
            key=lambda node: abs(node.get("contribution") or 0.0),
            reverse=True,
        )
        dimensions.append(
            {
                "dimension": name,
                # Not a share of anything and able to exceed 100%: it measures how
                # far the segments deviated from moving in proportion to their size.
                "explanatory_power": summary.get("explanatory_power"),
                "segment_count": summary.get("segment_count"),
                "truncated": summary.get("truncated", False),
                "excluded_reason": summary.get("excluded_reason"),
                "segments": [_segment(node) for node in segments[:MAX_SEGMENTS]],
                "segments_omitted": max(0, len(segments) - MAX_SEGMENTS),
            }
        )

    return {
        "requested_dimension": wanted,
        "found": True,
        "dimensions": dimensions,
    }


SPEC = ToolSpec(
    name="dimension_analysis",
    description=DESCRIPTION,
    # Optional: absent means every dimension, which is what was computed anyway.
    arguments={"dimension": False},
    run=run,
)
