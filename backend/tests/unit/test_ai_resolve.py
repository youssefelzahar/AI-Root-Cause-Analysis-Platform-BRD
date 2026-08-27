"""The KPI ladder, and what it refuses to guess.

Pure string work over a list of KPI definitions - no database, no model. The rungs
are the interesting part: the difference between *substituting and saying so* and
*substituting silently* is the difference between a usable answer and a wrong one.
"""

import uuid

from app.ai.models import HintSource, Intent, IntentKind, KpiChoice
from app.ai.resolve import match_dimension, match_kpis, resolve

DATASET = uuid.uuid4()


def _choice(name: str, column: str = "revenue", *, active: bool = True) -> KpiChoice:
    return KpiChoice(
        kpi_definition_id=uuid.uuid4(),
        name=name,
        measure_column=column,
        aggregation="SUM",
        is_active=active,
    )


def _resolve(intent: Intent, choices: tuple[KpiChoice, ...], **kwargs):
    return resolve(
        intent,
        dataset_id=DATASET,
        dataset_name="sales.csv",
        choices=choices,
        dimensions=kwargs.pop("dimensions", ("region", "product", "segment")),
        **kwargs,
    )


# --- matching -----------------------------------------------------------------


def test_an_exact_name_match_wins_over_a_substring_one() -> None:
    """Otherwise "Revenue" is ambiguous against "Revenue Growth Rate".

    The exact hit is obviously the answer, and returning both would ask the user a
    question whose answer is already known.
    """
    choices = (_choice("Revenue"), _choice("Revenue Growth Rate", "growth"))
    matched = match_kpis("revenue", choices)
    assert [c.name for c in matched] == ["Revenue"]


def test_the_measure_column_is_matched_as_well_as_the_name() -> None:
    matched = match_kpis("value_for_money", (_choice("Value For Money", "value_for_money"),))
    assert len(matched) == 1


def test_matching_ignores_case_and_punctuation() -> None:
    assert len(match_kpis("value for money", (_choice("Value-For-Money", "vfm"),))) == 1


def test_a_dimension_hint_resolves_to_the_real_column_name() -> None:
    """Never the hint itself, so a hallucinated dimension cannot reach an argument."""
    assert match_dimension("Region", ("region", "product")) == "region"
    assert match_dimension("regions", ("region", "product")) == "region"
    assert match_dimension("country", ("region", "product")) is None


# --- the ladder ---------------------------------------------------------------


def test_a_hint_matching_one_definition_selects_it() -> None:
    revenue = _choice("Revenue")
    context = _resolve(Intent(IntentKind.ROOT_CAUSE_ANALYSIS, kpi_hint="revenue"), (revenue,))
    assert context.kpi_definition_id == revenue.kpi_definition_id
    assert context.kpi_source is HintSource.QUESTION
    assert not context.blocked


def test_a_hint_matching_several_definitions_asks_rather_than_picks() -> None:
    """Picking here would answer a different question with no way for a reader to tell."""
    choices = (_choice("Sales Revenue", "rev"), _choice("Sales Volume", "vol", active=False))
    context = _resolve(Intent(IntentKind.ROOT_CAUSE_ANALYSIS, kpi_hint="sales"), choices)
    assert context.blocked
    assert context.clarification.code == "AMBIGUOUS_KPI"
    assert set(context.clarification.options) == {"Sales Revenue", "Sales Volume"}
    # Nothing was chosen, so nothing can be analysed by accident.
    assert context.kpi_definition_id is None


def test_a_hint_matching_nothing_falls_back_to_the_active_kpi_and_says_so() -> None:
    """Answering and stating the substitution beats refusing."""
    revenue = _choice("Revenue")
    context = _resolve(Intent(IntentKind.ROOT_CAUSE_ANALYSIS, kpi_hint="profit"), (revenue,))
    assert context.kpi_definition_id == revenue.kpi_definition_id
    assert context.kpi_source is HintSource.ACTIVE_DEFAULT
    assert any("profit" in note and "Revenue" in note for note in context.assumptions)


def test_a_dataset_with_no_kpi_asks_for_one() -> None:
    context = _resolve(Intent(IntentKind.ROOT_CAUSE_ANALYSIS), ())
    assert context.blocked
    assert context.clarification.code == "NO_KPI_CONFIGURED"


def test_a_dataset_whose_definitions_are_all_superseded_asks_for_one() -> None:
    context = _resolve(Intent(IntentKind.ROOT_CAUSE_ANALYSIS), (_choice("Revenue", active=False),))
    assert context.blocked
    assert context.clarification.code == "NO_ACTIVE_KPI"


def test_no_hint_at_all_uses_the_active_definition_without_a_caveat() -> None:
    context = _resolve(Intent(IntentKind.ROOT_CAUSE_ANALYSIS), (_choice("Revenue"),))
    assert context.kpi_definition_id is not None
    assert context.assumptions == ()


# --- periods, dimensions and follow-ups ---------------------------------------


def test_a_named_period_is_recorded_as_a_claim_not_a_parameter() -> None:
    """The engine anchors on the data's own latest timestamp, so this is only ever
    something to reconcile against the resolved windows and report on."""
    context = _resolve(
        Intent(IntentKind.ROOT_CAUSE_ANALYSIS, period_hint="July"), (_choice("Revenue"),)
    )
    assert context.period_claim == "July"
    assert any("comparison windows" in note for note in context.assumptions)


def test_a_dimension_the_dataset_does_not_have_is_reported_not_applied() -> None:
    context = _resolve(
        Intent(IntentKind.DIMENSION_ANALYSIS, dimension_hint="country"), (_choice("Revenue"),)
    )
    assert context.dimension is None
    assert any("country" in note for note in context.assumptions)


def test_a_segment_hint_is_carried_through_unvalidated() -> None:
    """Whether "Cairo" exists is a property of the analysed data, not the schema,
    so the drill-down tool checks the tree and reports honestly when it is absent."""
    context = _resolve(
        Intent(IntentKind.DRILL_DOWN, segment_hint="Cairo"), (_choice("Revenue"),)
    )
    assert context.segment == "Cairo"


def test_a_follow_up_with_nothing_to_follow_up_on_says_so() -> None:
    context = _resolve(Intent(IntentKind.FOLLOW_UP_ANALYSIS), (_choice("Revenue"),))
    assert any("no earlier investigation" in note for note in context.assumptions)


def test_a_carried_definition_is_preferred_over_the_active_one() -> None:
    """A conversation about a superseded KPI keeps analysing that KPI."""
    active = _choice("Revenue")
    carried = _choice("Orders", "orders", active=False)
    context = _resolve(
        Intent(IntentKind.FOLLOW_UP_ANALYSIS),
        (active, carried),
        carried_kpi_definition_id=carried.kpi_definition_id,
    )
    assert context.kpi_definition_id == carried.kpi_definition_id
    assert context.kpi_source is HintSource.CARRIED_OVER
