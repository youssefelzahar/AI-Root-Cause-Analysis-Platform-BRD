"""The tool registry: an allow-list of read and analyse operations.

Six tools, two computations. The distinction is worth stating plainly because it
is the layer's central design fact:

``run_investigation`` computes **every** dimension's depth-1 breakdown, picks the
dimension that best separates the movement, and drills only that path - all in one
pass, on one temp table, in about seven statements. ``_drill`` is module-private,
takes twelve positional arguments and mutates nodes in place. There is no
per-dimension entry point and no per-segment one.

So ``get_kpi_result``, ``dimension_analysis``, ``contribution_analysis`` and
``drill_down`` are **projections** of one investigation rather than four analyses.
That is cheaper than four passes, and - more importantly - four views of one
computation cannot contradict each other, whereas four computations can.
``detect_anomaly`` is the exception: it answers a question the investigation only
partly evidences, so it really does call ``anomaly_service``.

Nothing that writes is here. Not a KPI definition, not a delete, not SQL. One route
in this API is misnamed - ``DELETE /api/rca/investigations/{dataset_id}`` destroys
the dataset's active KPI definition - and an allow-list is what guarantees no
amount of model creativity finds it.
"""

from app.ai.tools import (
    anomaly_tool,
    contribution_tool,
    dimension_tool,
    drilldown_tool,
    investigation_tool,
    kpi_tool,
)
from app.ai.tools.base import ToolContext, ToolSpec

TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        kpi_tool.SPEC,
        dimension_tool.SPEC,
        contribution_tool.SPEC,
        drilldown_tool.SPEC,
        anomaly_tool.SPEC,
        investigation_tool.INVESTIGATION_SPEC,
        investigation_tool.EVIDENCE_SPEC,
    )
}

__all__ = ["TOOL_REGISTRY", "ToolContext", "ToolSpec", "describe"]


def describe() -> list[dict[str, object]]:
    """The registry as data, for a client that wants to show what can be asked.

    Sorted by name so the list is stable across restarts - an unordered dict would
    reshuffle the UI on every deploy.
    """
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "arguments": [
                {"name": argument, "required": required}
                for argument, required in sorted(spec.arguments.items())
            ],
        }
        for spec in sorted(TOOL_REGISTRY.values(), key=lambda s: s.name)
    ]
