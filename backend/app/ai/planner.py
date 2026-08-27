"""Step 3: the intent, as a fixed sequence of tool calls. No model involved.

The specification asks the AI to "create an analysis plan". It does - by choosing
among recipes, not by writing one. Two reasons, and both are properties of what is
underneath rather than preferences:

The analysis is a single pass. One ``run_investigation`` computes every dimension's
breakdown and the whole drill-down tree together, on one temp table, in about seven
statements. There is nothing for a model-authored plan to optimise; a plan that
asked for the dimension breakdown *without* the tree would still compute the tree.

And a recipe is inspectable. A reader can see what a question will do before it
runs, and a test can assert it. A plan the model writes is a plan that differs
between two runs of the same question, which is the opposite of what an
evidence-backed platform is for.

Arguments are built here from the resolved context, so the model never supplies a
tool argument at all. The registry validates them anyway - a planner bug and a
model hallucination deserve the same failure.
"""

from app.ai.constants import (
    PLAN_RECIPES,
    TOOL_DIMENSION_ANALYSIS,
    TOOL_DRILL_DOWN,
    TOOL_GET_EVIDENCE,
)
from app.ai.models import Intent, IntentKind, Plan, ResolvedContext, ToolCall
from app.core.config import settings

# Evidence types worth quoting in an explanation, in the order a reader needs them.
# Deliberately not every type: the six validation records and the query trace are
# what make the investigation *checkable*, but pasting them into a prompt would
# spend the context on material the answer must not quote anyway.
EXPLANATORY_EVIDENCE_TYPES = (
    "kpi_change",
    "comparison",
    "contribution",
    "drill_down",
    "gone_segment",
    "new_segment",
    "offsetting_factor",
    "anomaly",
)


def plan(intent: Intent, context: ResolvedContext) -> Plan:
    """The calls this question will make, in order."""
    recipe = PLAN_RECIPES[intent.kind]

    # A follow-up that named no segment has nothing to drill into, so the step is
    # dropped rather than called with a null argument it would have to reject.
    if intent.kind is IntentKind.FOLLOW_UP_ANALYSIS and not context.segment:
        recipe = tuple(tool for tool in recipe if tool != TOOL_DRILL_DOWN)

    calls: list[ToolCall] = []
    for tool in recipe:
        calls.append(ToolCall(tool=tool, arguments=_arguments(tool, context)))

    # The cap is a backstop against a recipe growing past what the executor will
    # run, which would fail at request time instead of here.
    if len(calls) > settings.ai_max_plan_steps:
        calls = calls[: settings.ai_max_plan_steps]

    return Plan(intent=intent.kind, calls=tuple(calls))


def _arguments(tool: str, context: ResolvedContext) -> dict:
    """What this tool needs, taken only from the resolved context."""
    if tool == TOOL_DIMENSION_ANALYSIS:
        # None means "every configured dimension", which is what the engine
        # computed anyway. Narrowing happens only when the question named one.
        return {"dimension": context.dimension}
    if tool == TOOL_DRILL_DOWN:
        return {"segment": context.segment}
    if tool == TOOL_GET_EVIDENCE:
        return {"evidence_types": list(EXPLANATORY_EVIDENCE_TYPES)}
    return {}
