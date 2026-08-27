"""The numeric guard: can this sentence be published?

The evidence layer made *never fabricate SQL* mechanical by asserting byte-identity
against the query trace. This is the same move for prose - every figure in a
generated answer must appear in the bundle, or the answer is replaced by a template.

The tests split into two halves, and both matter equally. A guard that misses an
invented number is useless; a guard that fires on a correct answer gets switched
off. The leniencies are as load-bearing as the checks.
"""

import uuid

import pytest

from app.ai import render, verify
from app.ai.explain import explain
from app.ai.models import EvidenceBundle, GroundedDriver, GroundedFact
from app.ai.providers.base import LlmProvider
from app.core.exceptions import UpstreamError

INVESTIGATION = uuid.uuid4()


def _bundle(**overrides) -> EvidenceBundle:
    values = {
        "question": "Why did revenue decrease?",
        "kpi_name": "Revenue",
        "investigation_id": INVESTIGATION,
        "investigation_status": "completed",
        "analysis_state": "attributable",
        "previous_period": "2026-05-01 to 2026-06-01 (exclusive)",
        "current_period": "2026-06-01 to 2026-07-01 (exclusive)",
        "previous_value": 1500.0,
        "current_value": 1200.0,
        "absolute_change": -300.0,
        "percentage_change": -20.0,
        "direction": "down",
        "severity": "high",
        "attribution_basis": "net_change",
        "drivers": (
            GroundedDriver(
                dimension="region",
                value="Cairo",
                absolute_change=-300.0,
                contribution_percentage=100.0,
                classification="primary",
                rank=1,
                evidence_id=str(uuid.uuid4()),
            ),
        ),
    }
    values.update(overrides)
    return EvidenceBundle(**values)


class ScriptedProvider(LlmProvider):
    """Returns exactly the prose a test wants to judge.

    The answer is requested under a schema, so the prose arrives as
    ``{"answer": ...}`` - which is also what makes a small model produce prose at
    all rather than its own deliberation.
    """

    def __init__(self, answer: str | None = None, error: Exception | None = None):
        self.answer = answer
        self.error = error

    @property
    def model(self) -> str:
        return "scripted"

    def complete_json(self, messages, schema, *, timeout_seconds=None, max_tokens=None):
        if self.error is not None:
            raise self.error
        return {"answer": self.answer or ""}

    def health(self):
        return {"ok": True, "model": "scripted", "message": "", "latency_ms": 0}


# --- the guard catches invention -----------------------------------------------


def test_an_invented_figure_is_caught() -> None:
    verdict = verify.check(
        "Revenue fell by 850 in the period, driven by Cairo.", _bundle()
    )
    assert not verdict.grounded
    assert "850" in verdict.ungrounded


def test_an_invented_percentage_is_caught() -> None:
    verdict = verify.check("Revenue fell 47.5%, with Cairo the main contributor.", _bundle())
    assert not verdict.grounded


def test_an_invented_figure_among_correct_ones_is_still_caught() -> None:
    """The dangerous case: mostly right, so it reads as trustworthy."""
    verdict = verify.check(
        "Revenue fell from 1,500 to 1,200, a drop of 300. Cairo accounts for 100%, "
        "and Giza added 640.",
        _bundle(),
    )
    assert not verdict.grounded
    assert "640" in verdict.ungrounded


# --- the guard permits correct answers -----------------------------------------


def test_the_measured_figures_pass() -> None:
    verdict = verify.check(
        "Revenue decreased from 1,500 to 1,200, a change of -300 (-20%). "
        "region Cairo contributed 100% of the movement.",
        _bundle(),
    )
    assert verdict.grounded
    assert verdict.checked >= 4


def test_rounding_for_readability_passes() -> None:
    """The model is asked to write for a reader, so 23.077 becoming 23.1% must pass."""
    bundle = _bundle(percentage_change=-23.076923, absolute_change=-15.0, previous_value=65.0,
                     current_value=50.0, drivers=())
    assert verify.check("Value For Money fell 23.1%, from 65 to 50.", bundle).grounded


def test_a_share_written_as_a_fraction_passes() -> None:
    """0.8 for a bundle holding 80.0 is the same claim in different units."""
    assert verify.check("Cairo accounts for 1.0 of the change.", _bundle()).grounded


def test_small_integers_in_ordinary_prose_are_not_checked() -> None:
    """"the top 3 drivers", "both periods" - demanding a source would reject correct answers."""
    assert verify.check(
        "The 2 periods were compared and the top 1 driver explains the move.", _bundle()
    ).grounded


def test_a_year_inside_a_period_label_is_not_treated_as_a_measurement() -> None:
    """The answer is told to quote the periods, which contain years."""
    assert verify.check(
        "Comparing 2026-06-01 to 2026-07-01 against the month before, revenue fell 300.",
        _bundle(),
    ).grounded


def test_a_negative_written_as_a_positive_passes() -> None:
    """The evidence records -300; "decreased by 300" is the same fact in words."""
    assert verify.check("Revenue decreased by 300.", _bundle()).grounded


def test_prose_with_no_figures_at_all_passes() -> None:
    verdict = verify.check("Revenue fell, and one region accounts for most of it.", _bundle())
    assert verdict.grounded
    assert verdict.checked == 0


def test_a_fact_in_the_bundle_is_quotable() -> None:
    bundle = _bundle(
        facts=(GroundedFact(label="rows scanned", value=4321.0, formatted="4,321"),)
    )
    assert verify.check("The analysis scanned 4,321 rows.", bundle).grounded


# --- what happens to a rejected answer ----------------------------------------


def test_a_rejected_answer_is_replaced_by_the_template() -> None:
    """The generated text is discarded, not shown with a warning: a number that was
    never measured has no place on the page."""
    result = explain(
        _bundle(), provider=ScriptedProvider("Revenue fell by 850 because of Cairo.")
    )
    assert result.is_template
    assert "850" not in result.answer
    assert any("quoted figures" in note for note in result.limitations)


def test_an_unavailable_model_still_produces_an_answer() -> None:
    result = explain(
        _bundle(), provider=ScriptedProvider(error=UpstreamError("down", code="LLM_UNAVAILABLE"))
    )
    assert result.is_template
    assert "Revenue" in result.answer
    assert any("LLM_UNAVAILABLE" in note for note in result.limitations)


def test_an_empty_answer_falls_back_too() -> None:
    result = explain(_bundle(), provider=ScriptedProvider("   "))
    assert result.is_template


def test_a_grounded_answer_is_kept_verbatim() -> None:
    prose = "Revenue decreased from 1,500 to 1,200. Cairo contributed 100% of that move."
    result = explain(_bundle(), provider=ScriptedProvider(prose))
    assert result.answer == prose
    assert not result.is_template


# --- the vocabulary guard ------------------------------------------------------


@pytest.mark.parametrize(
    "prose",
    [
        "Revenue fell 300, caused by Cairo.",
        "Revenue fell 300 due to Cairo.",
        "Revenue fell 300 because of Cairo.",
        "Cairo is responsible for the 300 decline.",
        "Revenue fell 300 as a result of Cairo.",
    ],
)
def test_prose_claiming_causation_is_replaced(prose: str) -> None:
    """The house rule, made mechanical. A contribution is arithmetic about a
    decomposition, and asserting it is a cause overstates every number above it."""
    result = explain(_bundle(), provider=ScriptedProvider(prose))
    assert result.is_template
    assert any("causation" in note for note in result.limitations)


def test_the_word_driver_is_not_treated_as_a_causal_claim() -> None:
    """This codebase calls its ranked segments drivers throughout, so banning the
    word would reject the platform's own vocabulary."""
    prose = "Revenue fell 300. The primary driver is Cairo, which drove 100% of the move."
    result = explain(_bundle(), provider=ScriptedProvider(prose))
    assert not result.is_template


def test_because_inside_a_word_is_not_a_causal_claim() -> None:
    assert verify.causal_phrases("Revenue fell 300, and the cause is unclear.") == ()


# --- the template itself -------------------------------------------------------


def test_the_template_states_only_measured_numbers() -> None:
    """It is built from the bundle, so it must pass its own guard."""
    bundle = _bundle()
    assert verify.check(render.template_answer(bundle), bundle).grounded


def test_the_template_never_claims_causation() -> None:
    """The vocabulary rule applies to code-generated prose too."""
    answer = render.template_answer(_bundle()).lower()
    assert "caused" not in answer
    assert "contribut" in answer


def test_the_template_carries_the_causation_caveat() -> None:
    assert "not proven causation" in render.template_answer(_bundle())


def test_the_template_handles_an_uncomparable_kpi() -> None:
    bundle = _bundle(
        absolute_change=None, previous_value=None, current_value=None,
        percentage_change=None, drivers=(), analysis_state="no_previous_period",
    )
    answer = render.template_answer(bundle)
    assert "could not be compared" in answer
    assert verify.check(answer, bundle).grounded


def test_the_template_reports_a_gone_segment_on_rows_not_a_collapse() -> None:
    bundle = _bundle(
        drivers=(
            GroundedDriver(
                dimension="airline",
                value="Singapore Airlines",
                absolute_change=-12.0,
                contribution_percentage=80.0,
                classification="primary",
                rank=1,
                is_lost_segment=True,
            ),
        )
    )
    assert "no rows at all in the current period" in render.template_answer(bundle)


def test_the_template_mentions_an_offsetting_segment() -> None:
    bundle = _bundle(
        offsetting=(
            GroundedDriver(
                dimension="region",
                value="Aswan",
                absolute_change=75.0,
                contribution_percentage=-25.0,
                classification="offsetting",
                rank=1,
            ),
        )
    )
    answer = render.template_answer(bundle)
    assert "Aswan" in answer
    assert "offsetting" in answer


def test_the_bundle_is_the_only_thing_the_prompt_sees() -> None:
    """No dataset, no SQL, no request - which is what makes invention hard rather
    than merely discouraged."""
    from app.ai.prompts import explain as explain_prompt

    text = explain_prompt.build(_bundle())
    assert "Revenue" in text
    assert "sales.csv" not in text
    assert "SELECT" not in text.upper()


# --- an absence that was never checked for -------------------------------------


def test_an_unchecked_contribution_is_not_reported_as_no_drivers() -> None:
    """An empty driver list means one of two very different things, and only one of
    them is a finding. Saying "none were material" when the step never ran reports
    an absence nobody looked for."""
    unchecked = _bundle(drivers=(), contribution_analysed=False)
    text = render.bundle_as_text(unchecked)
    assert "not analysed for this question" in text
    assert "none were material" not in text

    checked = _bundle(drivers=(), contribution_analysed=True)
    assert "none were material enough to name" in render.bundle_as_text(checked)


def test_the_template_only_claims_no_drivers_when_it_looked() -> None:
    unchecked = render.template_answer(_bundle(drivers=(), contribution_analysed=False))
    assert "material enough to name" not in unchecked

    checked = render.template_answer(_bundle(drivers=(), contribution_analysed=True))
    assert "material enough to name" in checked
