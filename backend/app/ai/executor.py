"""Step 4: run the plan. No model involved.

The safeguards §14 asks for live here, and they are backstops rather than working
limits: the plan is a fixed recipe of at most five steps, so hitting a cap means
the planner is wrong, not that the budget is tight. That is why a breach is logged
as a warning and reported as a limitation - it is a bug signal, not an expected
outcome.

A tool that raises is recorded and the plan continues. A projection failing on one
slice of an investigation should not lose the other four: the explanation is better
with a gap it can see than absent because of one.
"""

import time

from app.ai.models import Plan, ToolResult
from app.ai.tools import TOOL_REGISTRY, ToolContext
from app.core.config import settings
from app.core.exceptions import AppError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)


def execute(plan: Plan, context: ToolContext) -> tuple[tuple[ToolResult, ...], tuple[str, ...]]:
    """Every step's result, plus anything a cap prevented.

    Returns rather than raises on a step failure, so the caller always has
    something to ground an answer in.
    """
    results: list[ToolResult] = []
    limitations: list[str] = []
    started = time.perf_counter()

    for call in plan.calls:
        if len(results) >= settings.ai_max_tool_calls:
            limitations.append(
                f"Stopped after {settings.ai_max_tool_calls} analysis steps, so "
                f"{len(plan.calls) - len(results)} planned step(s) did not run."
            )
            logger.warning(
                "ai_tool_call_cap_reached",
                extra={"intent": plan.intent.value, "planned": len(plan.calls)},
            )
            break

        elapsed = time.perf_counter() - started
        if elapsed > settings.ai_max_execution_seconds:
            limitations.append(
                f"Stopped after {settings.ai_max_execution_seconds}s, so "
                f"{len(plan.calls) - len(results)} planned step(s) did not run."
            )
            logger.warning(
                "ai_execution_deadline_reached",
                extra={"intent": plan.intent.value, "completed": len(results)},
            )
            break

        results.append(_run_one(call.tool, call.arguments, context))

    return tuple(results), tuple(limitations)


def _run_one(tool: str, arguments: dict, context: ToolContext) -> ToolResult:
    spec = TOOL_REGISTRY.get(tool)
    step_started = time.perf_counter()

    if spec is None:
        # Only reachable from a planner bug: recipes name constants, and the model
        # never chooses a tool. Kept because the allow-list has to be enforced
        # where dispatch happens, not only where names are written.
        logger.warning("ai_unknown_tool", extra={"tool": tool})
        return ToolResult(
            tool=tool,
            ok=False,
            duration_ms=0,
            detail=f"{tool} is not a known analysis tool.",
        )

    try:
        spec.validate(arguments)
        payload = spec.run(context, arguments)
    except ValidationError as exc:
        return ToolResult(
            tool=tool,
            ok=False,
            duration_ms=int((time.perf_counter() - step_started) * 1000),
            detail=exc.message,
        )
    except AppError as exc:
        logger.warning("ai_tool_failed", extra={"tool": tool, "code": exc.code})
        return ToolResult(
            tool=tool,
            ok=False,
            duration_ms=int((time.perf_counter() - step_started) * 1000),
            detail=exc.message,
        )
    except Exception:
        # A projection over an unexpected payload shape. Logged with a traceback
        # because it is a real defect, but it must not lose the other steps.
        logger.exception("ai_tool_crashed", extra={"tool": tool})
        return ToolResult(
            tool=tool,
            ok=False,
            duration_ms=int((time.perf_counter() - step_started) * 1000),
            detail=f"{tool} could not read this investigation.",
        )

    return ToolResult(
        tool=tool,
        ok=True,
        duration_ms=int((time.perf_counter() - step_started) * 1000),
        payload=payload,
    )
