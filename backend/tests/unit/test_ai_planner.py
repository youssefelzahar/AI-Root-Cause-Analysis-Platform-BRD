"""Plan recipes, and the safeguards around running them.

The recipes are the AI layer's answer to "create an analysis plan": the model
chooses among them, it does not write one. So what is worth asserting is that every
intent has one, that they name real tools, and that the lattice property holds -
because a small model does misclassify, and the cost of that has to be a thinner
answer rather than a wrong one.
"""

import uuid

from app.ai.constants import (
    PLAN_RECIPES,
    TOOL_DRILL_DOWN,
    TOOL_GET_KPI_RESULT,
)
from app.ai.executor import execute
from app.ai.models import (
    Intent,
    IntentKind,
    Plan,
    ResolvedContext,
    ToolCall,
)
from app.ai.planner import plan as build_plan
from app.ai.tools import TOOL_REGISTRY, ToolContext
from app.core.config import settings


def _context(**overrides) -> ResolvedContext:
    values = {
        "dataset_id": uuid.uuid4(),
        "dataset_name": "sales.csv",
        "kpi_definition_id": uuid.uuid4(),
        "kpi_name": "Revenue",
    }
    values.update(overrides)
    return ResolvedContext(**values)


# --- the recipes --------------------------------------------------------------


def test_every_intent_has_a_recipe() -> None:
    assert set(PLAN_RECIPES) == set(IntentKind)


def test_every_recipe_names_only_registered_tools() -> None:
    """A recipe naming a tool that does not exist would fail at request time."""
    for intent, recipe in PLAN_RECIPES.items():
        for tool in recipe:
            assert tool in TOOL_REGISTRY, f"{intent.value} names unknown tool {tool}"


def test_no_recipe_exceeds_the_step_cap() -> None:
    for intent, recipe in PLAN_RECIPES.items():
        assert len(recipe) <= settings.ai_max_plan_steps, intent.value


def test_no_recipe_exceeds_the_tool_call_cap() -> None:
    """The cap is a backstop against a planner bug, so no honest plan may reach it."""
    for intent, recipe in PLAN_RECIPES.items():
        assert len(recipe) <= settings.ai_max_tool_calls, intent.value


def test_root_cause_is_a_superset_of_the_narrower_recipes() -> None:
    """The lattice property: a misclassified intent degrades rather than misleads.

    Measured reason for caring - the model classified 4 of 9 questions correctly
    before the prompt carried examples, and even at 9 of 9 a wrong reading has to be
    survivable.
    """
    root = set(PLAN_RECIPES[IntentKind.ROOT_CAUSE_ANALYSIS])
    for narrower in (
        IntentKind.KPI_ANALYSIS,
        IntentKind.CONTRIBUTION_ANALYSIS,
        IntentKind.DRILL_DOWN,
        IntentKind.DIMENSION_ANALYSIS,
    ):
        assert set(PLAN_RECIPES[narrower]) <= root, narrower.value


def test_every_recipe_starts_from_the_headline_movement_or_a_stored_run() -> None:
    """An answer with no KPI change in it is not an answer to any of these questions."""
    for intent, recipe in PLAN_RECIPES.items():
        assert recipe[0] in {TOOL_GET_KPI_RESULT, "get_investigation"}, intent.value


# --- planning -----------------------------------------------------------------


def test_a_plan_carries_the_recipe_for_its_intent() -> None:
    built = build_plan(Intent(IntentKind.CONTRIBUTION_ANALYSIS), _context())
    assert [call.tool for call in built.calls] == list(
        PLAN_RECIPES[IntentKind.CONTRIBUTION_ANALYSIS]
    )


def test_the_planner_supplies_arguments_the_model_never_touches() -> None:
    built = build_plan(
        Intent(IntentKind.DRILL_DOWN, segment_hint="Cairo"),
        _context(segment="Cairo", dimension="region"),
    )
    drill = next(call for call in built.calls if call.tool == TOOL_DRILL_DOWN)
    assert drill.arguments == {"segment": "Cairo"}


def test_a_follow_up_naming_no_segment_drops_the_drill_step() -> None:
    """Rather than calling it with a null argument it would have to reject."""
    built = build_plan(Intent(IntentKind.FOLLOW_UP_ANALYSIS), _context())
    assert TOOL_DRILL_DOWN not in [call.tool for call in built.calls]


def test_an_unnamed_dimension_means_every_dimension() -> None:
    """Which is what the engine computed anyway - narrowing is a selection."""
    built = build_plan(Intent(IntentKind.DIMENSION_ANALYSIS), _context())
    dimension = next(call for call in built.calls if call.tool == "dimension_analysis")
    assert dimension.arguments == {"dimension": None}


# --- the safeguards -----------------------------------------------------------


def test_a_plan_longer_than_the_cap_is_truncated_rather_than_run() -> None:
    calls = tuple(
        ToolCall(tool=TOOL_GET_KPI_RESULT) for _ in range(settings.ai_max_tool_calls + 3)
    )
    results, limitations = execute(
        Plan(intent=IntentKind.ROOT_CAUSE_ANALYSIS, calls=calls),
        ToolContext(investigation={"id": uuid.uuid4(), "result": {}}),
    )
    assert len(results) == settings.ai_max_tool_calls
    assert any("Stopped after" in note for note in limitations)


def test_an_unknown_tool_is_reported_rather_than_dispatched() -> None:
    """The allow-list is enforced where dispatch happens, not only where names are written."""
    results, _ = execute(
        Plan(
            intent=IntentKind.ROOT_CAUSE_ANALYSIS,
            calls=(ToolCall(tool="drop_everything"),),
        ),
        ToolContext(investigation={"id": uuid.uuid4(), "result": {}}),
    )
    assert results[0].ok is False
    assert "not a known analysis tool" in results[0].detail


def test_an_argument_the_tool_does_not_accept_fails_that_step_only() -> None:
    results, _ = execute(
        Plan(
            intent=IntentKind.ROOT_CAUSE_ANALYSIS,
            calls=(
                ToolCall(tool=TOOL_GET_KPI_RESULT, arguments={"sql": "DROP TABLE datasets"}),
                ToolCall(tool=TOOL_GET_KPI_RESULT),
            ),
        ),
        ToolContext(investigation={"id": uuid.uuid4(), "result": {}}),
    )
    assert results[0].ok is False
    # The plan continues: one bad step must not lose the rest.
    assert results[1].ok is True


def test_a_tool_that_crashes_does_not_lose_the_other_steps() -> None:
    """A projection over an unexpected payload shape is a defect, not a lost answer."""
    results, _ = execute(
        Plan(
            intent=IntentKind.ROOT_CAUSE_ANALYSIS,
            calls=(ToolCall(tool=TOOL_GET_KPI_RESULT), ToolCall(tool="drill_down")),
        ),
        # `tree` of the wrong type, which the drill tool will choke on.
        ToolContext(investigation={"id": uuid.uuid4(), "result": {}, "tree": 7}),
    )
    assert results[0].ok is True
    assert len(results) == 2


# --- the registry -------------------------------------------------------------


def test_the_registry_exposes_no_write_operation() -> None:
    """One route in this API deletes a KPI definition under an investigation-shaped
    name, so the allow-list is what guarantees no model creativity finds it."""
    forbidden = ("delete", "create", "update", "drop", "execute", "sql", "write", "remove")
    for name in TOOL_REGISTRY:
        assert not any(word in name for word in forbidden), name


def test_every_tool_describes_itself_for_a_client() -> None:
    for spec in TOOL_REGISTRY.values():
        assert spec.description.strip()
        assert spec.run is not None
