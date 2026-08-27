"""Reading a question, and distrusting what the model says about it.

No database and no HTTP client: these drive ``understand`` with a stub provider, so
every case is a property of the sanitisation rather than of a model's mood.

Each test here corresponds to something the real model actually did against
``qwen3:4b``. That is the point of the module - these are not hypotheticals.
"""

from typing import Any

import pytest

from app.ai import keywords
from app.ai.intent import clean_hint, understand
from app.ai.models import IntentKind
from app.ai.prompts.intent import FEW_SHOT, INTENT_SCHEMA
from app.ai.providers.base import LlmProvider, Message
from app.core.exceptions import UpstreamError


class StubProvider(LlmProvider):
    """Returns a canned object, or raises, so the caller is what is under test."""

    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls = 0
        self.last_messages: list[Message] = []

    @property
    def model(self) -> str:
        return "stub"

    def complete_json(self, messages, schema, *, timeout_seconds=None, max_tokens=None):
        self.calls += 1
        self.last_messages = messages
        if self.error is not None:
            raise self.error
        return dict(self.payload or {})

    def health(self):
        return {"ok": True, "model": "stub", "message": "", "latency_ms": 0}


def _intent(**overrides: Any) -> dict[str, Any]:
    payload = {
        "intent": "ROOT_CAUSE_ANALYSIS",
        "kpi_hint": None,
        "period_hint": None,
        "dimension_hint": None,
        "segment_hint": None,
    }
    payload.update(overrides)
    return payload


def _understand(question: str, payload: dict[str, Any]):
    return understand(
        question,
        provider=StubProvider(payload),
        kpi_names=("Revenue",),
        dimensions=("region", "product", "segment"),
    )


# --- the prompt itself --------------------------------------------------------


def test_every_intent_has_a_few_shot_example() -> None:
    """An intent with no example is the one the model gets wrong.

    Measured: without examples the model classified 4 of 9 questions correctly;
    with one per intent, 9 of 9. So coverage is a property worth asserting rather
    than a style preference.
    """
    covered = {IntentKind(example[1].split('"intent":"')[1].split('"')[0]) for example in FEW_SHOT}
    assert covered == set(IntentKind)


def test_the_schema_requires_every_hint_field_explicitly() -> None:
    """A small model omits an optional key far more often than it nulls a required one."""
    assert set(INTENT_SCHEMA["required"]) == {
        "intent",
        "kpi_hint",
        "period_hint",
        "dimension_hint",
        "segment_hint",
    }
    for field in ("kpi_hint", "period_hint", "dimension_hint", "segment_hint"):
        assert INTENT_SCHEMA["properties"][field]["type"] == ["string", "null"]


def test_the_schema_asks_for_no_self_reported_confidence() -> None:
    """The model answered "high" on every question, including the wrong ones.

    A confidence nobody can trust is worse than none, because it invites the
    caller to trust it.
    """
    assert "confidence" not in INTENT_SCHEMA["properties"]


# --- hint sanitisation --------------------------------------------------------


@pytest.mark.parametrize("raw", ["null", "NULL", "none", "None", "", "  ", "n/a", "-"])
def test_a_null_like_hint_becomes_none(raw: str) -> None:
    """The model emits the string "null" rather than a JSON null.

    Five of nine test questions leaked one. Left alone, a KPI hint of "null" gets
    matched against the KPI list like any other word.
    """
    assert clean_hint(raw, question="why did revenue drop") is None


def test_a_hint_that_echoes_the_whole_question_is_dropped() -> None:
    """Asked for a metric name, the model returned the question on five of nine."""
    question = "What was the biggest driver of the revenue change this quarter?"
    assert clean_hint(question, question=question) is None


def test_a_hint_that_is_not_in_the_question_is_dropped() -> None:
    """Asked about "the revenue change", the model volunteered dimension "product".

    A real dimension, but one the question never mentioned - and acting on it would
    narrow the answer to something the reader did not ask about.
    """
    assert clean_hint("product", question="What was the biggest driver of the revenue change?") is None


def test_a_multi_word_hint_copied_from_the_question_survives() -> None:
    assert clean_hint("Singapore Airlines", question="why did Singapore Airlines drop?") == (
        "Singapore Airlines"
    )


def test_a_period_hint_may_be_normalised_rather_than_copied() -> None:
    """The period is only ever quoted back, never computed from, so paraphrase is safe."""
    assert clean_hint("last month", question="compare with the previous month", allow_absent=True) == (
        "last month"
    )


def test_a_dropped_hint_is_reported_rather_than_silently_discarded() -> None:
    intent = _understand(
        "What was the biggest driver of the revenue change?",
        _intent(intent="CONTRIBUTION_ANALYSIS", dimension_hint="product"),
    )
    assert intent.dimension_hint is None
    assert any("product" in note for note in intent.dropped_hints)


# --- intents ------------------------------------------------------------------


def test_the_models_intent_is_used_when_it_is_valid() -> None:
    intent = _understand("Why did revenue decrease?", _intent(kpi_hint="revenue"))
    assert intent.kind is IntentKind.ROOT_CAUSE_ANALYSIS
    assert intent.kpi_hint == "revenue"


def test_an_intent_outside_the_enum_falls_back_to_the_keyword_reading() -> None:
    """Means the provider ignored the schema, so the rules are the better answer."""
    intent = _understand("Which region contributed most?", _intent(intent="NONSENSE"))
    assert intent.kind is IntentKind.CONTRIBUTION_ANALYSIS


def test_an_unavailable_model_still_yields_an_intent() -> None:
    """The whole reason the keyword classifier exists.

    A question routed by rules and answered from a template beats a 502 - the
    analysis underneath is identical either way.
    """
    provider = StubProvider(error=UpstreamError("down", code="LLM_UNAVAILABLE"))
    intent = understand(
        "Why did revenue decrease in July?",
        provider=provider,
        kpi_names=("Revenue",),
        dimensions=("region",),
    )
    assert intent.kind is IntentKind.ROOT_CAUSE_ANALYSIS
    assert intent.period_hint == "July"
    assert any("keyword" in note for note in intent.dropped_hints)


def test_an_unavailable_model_is_retried_before_giving_up() -> None:
    provider = StubProvider(error=UpstreamError("down", code="LLM_UNAVAILABLE"))
    understand("Why did revenue drop?", provider=provider, kpi_names=(), dimensions=())
    # One initial attempt plus the configured retries.
    assert provider.calls == 3


def test_the_available_kpis_and_dimensions_reach_the_prompt() -> None:
    provider = StubProvider(_intent())
    understand(
        "Why did revenue drop?",
        provider=provider,
        kpi_names=("Revenue", "Orders"),
        dimensions=("region", "channel"),
    )
    system = provider.last_messages[0].content
    assert "Revenue, Orders" in system
    assert "region, channel" in system


# --- the keyword classifier ---------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Why did revenue decrease in July?", IntentKind.ROOT_CAUSE_ANALYSIS),
        ("Which region contributed most?", IntentKind.CONTRIBUTION_ANALYSIS),
        ("What happened in Cairo?", IntentKind.DRILL_DOWN),
        ("Was July anomalous?", IntentKind.ANOMALY_ANALYSIS),
        ("Explain the July revenue anomaly.", IntentKind.ANOMALY_ANALYSIS),
        ("Break revenue down by region.", IntentKind.DIMENSION_ANALYSIS),
        ("Summarise the investigation.", IntentKind.INVESTIGATION_SUMMARY),
        ("What was the biggest driver of the revenue change?", IntentKind.CONTRIBUTION_ANALYSIS),
        ("How much revenue was there?", IntentKind.KPI_ANALYSIS),
    ],
)
def test_the_keyword_rules_read_the_common_questions(question: str, expected: IntentKind) -> None:
    guess = keywords.classify(question)
    assert guess.kind is expected
    assert guess.confident


def test_an_anomaly_question_beats_the_why_cue() -> None:
    """"Why was July anomalous" is an anomaly question, so cue order matters."""
    assert keywords.classify("Why was July anomalous?").kind is IntentKind.ANOMALY_ANALYSIS


def test_an_unreadable_question_defaults_to_the_superset_recipe() -> None:
    """Root cause covers the other recipes, so a bad guess still answers usefully."""
    guess = keywords.classify("hmm")
    assert guess.kind is IntentKind.ROOT_CAUSE_ANALYSIS
    assert not guess.confident


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Why did revenue drop in July?", "July"),
        ("Why did revenue drop in July 2026?", "July 2026"),
        ("Compare revenue with last month.", "last month"),
        ("How did Q3 go?", "Q3"),
        ("What about 2026-07?", "2026-07"),
        ("Why did revenue drop?", None),
    ],
)
def test_a_named_period_is_captured_verbatim(question: str, expected: str | None) -> None:
    """Verbatim because it is quoted back. Normalising it would be date arithmetic."""
    assert keywords.period_in(question) == expected


def test_what_happened_in_a_month_is_not_read_as_a_segment() -> None:
    """"What happened in July" names a period, not a Cairo-style segment."""
    guess = keywords.classify("What happened in July?")
    assert guess.segment_hint is None
