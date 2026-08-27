"""``drill_down`` - where the change concentrates, or why a branch ends.

Walks the persisted evidence-linked tree. The honest behaviour here is the whole
point of the tool: only the winning dimension's material nodes were expanded, at
most three then two then two wide, so a named segment frequently has **no children**
and carries a ``stop_reason`` instead.

Reporting that reason is a real answer. *"Aswan moved against the KPI, so it was
never broken down further"* tells a reader something true; inventing a breakdown, or
silently returning the deepest path that does exist, does not. This is why the tool
distinguishes "found and expanded", "found and stopped", and "not in the tree".
"""

from typing import Any

from app.ai.tools.base import ToolContext, ToolSpec
from app.analysis.investigation.constants import STOP_REASON_SENTENCES

DESCRIPTION = (
    "The hierarchy under the strongest driver, or - for a named segment - its own "
    "breakdown, or the reason the analysis did not break it down further."
)


def _node(node: dict[str, Any]) -> dict[str, Any]:
    reason = node.get("stop_reason")
    return {
        "dimension": node.get("dimension"),
        "value": node.get("value"),
        "depth": node.get("depth"),
        "absolute_change": node.get("absolute_change"),
        "contribution_percentage": (
            None if node.get("contribution") is None else node["contribution"] * 100.0
        ),
        "share_of_parent_change": node.get("share_of_parent_change"),
        "classification": node.get("classification"),
        # A pure split carries the whole segment down to one child value, which is
        # why a path can repeat the same number at every level.
        "is_pure_split": node.get("is_pure_split", False),
        "child_dimension": node.get("child_dimension"),
        "stop_reason": reason,
        "stop_reason_explained": (
            STOP_REASON_SENTENCES.get(reason, reason.replace("_", " ")) if reason else None
        ),
        "evidence_ids": node.get("evidence_ids") or [],
        "child_count": len(node.get("children") or []),
    }


def _find(node: dict[str, Any], needle: str) -> dict[str, Any] | None:
    """The node for a segment value, anywhere in the tree.

    Case-insensitive because the value came out of a question, not a database.
    """
    value = node.get("value")
    if isinstance(value, str) and value.strip().lower() == needle:
        return node
    for child in node.get("children") or []:
        found = _find(child, needle)
        if found is not None:
            return found
    return None


def _deepest_path(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The chain of strongest children from here down.

    Follows rank rather than magnitude: the ranking has already decided which child
    is the primary one at each level, and re-deciding here could disagree with the
    tree the UI renders.
    """
    path = [node]
    current = node
    while current.get("children"):
        current = sorted(
            current["children"],
            key=lambda child: (child.get("rank") or 99, -abs(child.get("contribution") or 0.0)),
        )[0]
        path.append(current)
    return path


def run(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    tree = context.investigation.get("tree")
    segment = arguments.get("segment") or context.segment

    if not tree:
        return {
            "found": False,
            "detail": "This investigation produced no drill-down hierarchy.",
            "path": [],
        }

    if segment:
        node = _find(tree, segment.strip().lower())
        if node is None:
            # Not a failure: the segment may be immaterial, or may belong to a
            # dimension the drill-down never descended.
            return {
                "found": False,
                "requested_segment": segment,
                "detail": (
                    f"{segment!r} is not in the drill-down hierarchy. Only the dimension that "
                    "best separated the movement was expanded, and only its material segments."
                ),
                "path": [],
            }
        chain = _deepest_path(node)
        return {
            "found": True,
            "requested_segment": segment,
            "expanded": bool(node.get("children")),
            "path": [_node(item) for item in chain],
            "node": _node(node),
        }

    # No segment named: report the strongest branch from the root down.
    root_children = tree.get("children") or []
    if not root_children:
        return {
            "found": False,
            "detail": "The hierarchy has no branches below the KPI itself.",
            "path": [],
        }
    strongest = sorted(
        root_children,
        key=lambda child: (child.get("rank") or 99, -abs(child.get("contribution") or 0.0)),
    )[0]
    chain = _deepest_path(strongest)
    return {
        "found": True,
        "root_dimension": tree.get("child_dimension"),
        "expanded": bool(strongest.get("children")),
        "path": [_node(item) for item in chain],
        "node": _node(strongest),
    }


SPEC = ToolSpec(
    name="drill_down",
    description=DESCRIPTION,
    # Optional: absent means "the strongest branch", which is the common case.
    arguments={"segment": False},
    run=run,
)
