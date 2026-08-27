"""Step 1: the question, as a typed intent.

The only place a model's output becomes a value this system acts on, so it is
where the distrust lives. Three sanitisations, each one prompted by something the
model actually did rather than something it might do:

* **The string "null".** A schema-constrained small model emits ``"null"`` instead
  of a JSON null. Five of nine test questions leaked one before the few-shot
  examples were added, and a KPI hint of ``"null"`` would be matched against the
  KPI list like any other word.
* **The echoed question.** Asked for a metric name, the model returned the whole
  question as ``kpi_hint`` on five of nine. That matches no KPI and produces a
  spurious clarification, which is worse than no hint at all.
* **The hint that is not in the question.** Asked *"What was the biggest driver of
  the revenue change?"* the model answered ``dimension_hint: "product"`` - a real
  dimension, but one the question never mentioned. Left alone it would narrow the
  answer to a dimension the reader did not ask about. So a hint must appear in the
  question, near enough, or it is dropped.

The last one is the reason hints are described as *claims about the question*
throughout this package. A hint that cannot be found in the question is not a
reading of it.
"""

import re

from app.ai import keywords
from app.ai.constants import MAX_HINT_CHARS, NULL_LIKE_HINTS
from app.ai.models import Intent, IntentKind
from app.ai.prompts import intent as intent_prompt
from app.ai.providers import LlmProvider, Message
from app.core.config import settings
from app.core.exceptions import AppError, UpstreamError
from app.core.logging import get_logger

logger = get_logger(__name__)

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def clean_hint(raw: object, *, question: str, allow_absent: bool = False) -> str | None:
    """One hint, or None if it cannot be trusted.

    ``allow_absent`` exempts a hint from the verbatim check. Used for the period,
    which a model legitimately normalises - "last month" for "the previous month" -
    and which is only ever quoted back, never used to compute.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None

    text = raw.strip().strip("\"'").strip()
    if text.lower() in NULL_LIKE_HINTS:
        return None
    if len(text) > MAX_HINT_CHARS:
        # Almost certainly the question echoed back.
        return None

    if allow_absent:
        return text

    # Every word of the hint should appear in the question. Token-level rather than
    # substring, so "Singapore Airlines" still matches "why did Singapore Airlines
    # drop" while a plausible invention does not.
    asked = set(_tokens(question))
    hint_tokens = _tokens(text)
    if not hint_tokens:
        return None
    if not all(token in asked for token in hint_tokens):
        return None
    return text


def _kind(raw: object, fallback: IntentKind) -> IntentKind:
    try:
        return IntentKind(str(raw))
    except ValueError:
        # The schema enumerates the values, so this means the provider ignored the
        # schema. The keyword reading is a better answer than an error.
        return fallback


def understand(
    question: str,
    *,
    provider: LlmProvider,
    kpi_names: tuple[str, ...],
    dimensions: tuple[str, ...],
    has_previous: bool = False,
) -> Intent:
    """Ask the model, then keep only what survives sanitisation.

    Falls back to the keyword reading rather than failing when the model is
    unavailable: a question routed by rules and answered from a template is a far
    better outcome than a 502, and the analysis behind it is identical.
    """
    guess = keywords.classify(question)
    system = intent_prompt.build(
        kpi_names=kpi_names, dimensions=dimensions, has_previous=has_previous
    )
    messages = [Message("system", system), Message("user", f"Q: {question}")]

    raw: dict | None = None
    last_error: AppError | None = None
    for attempt in range(1 + max(0, settings.ai_intent_retries)):
        try:
            raw = provider.complete_json(messages, intent_prompt.INTENT_SCHEMA)
            break
        except UpstreamError as exc:
            # Retries are for a dropped connection: schema-constrained output was
            # valid on every attempt in testing. An unreachable daemon will not
            # become reachable, but the cost of finding out is one failed request.
            last_error = exc
            logger.warning(
                "ai_intent_attempt_failed",
                extra={"attempt": attempt + 1, "code": exc.code, "model": provider.model},
            )

    if raw is None:
        logger.warning(
            "ai_intent_fell_back_to_keywords",
            extra={
                "code": last_error.code if last_error else None,
                "kind": guess.kind.value,
                "model": provider.model,
            },
        )
        return Intent(
            kind=guess.kind,
            kpi_hint=guess.kpi_hint,
            period_hint=guess.period_hint,
            dimension_hint=guess.dimension_hint,
            segment_hint=guess.segment_hint,
            dropped_hints=("The question was read by keyword rules because the language "
                           "model was unavailable.",),
        )

    dropped: list[str] = []
    kind = _kind(raw.get("intent"), guess.kind)

    # Where the rules are confident and the model disagrees, record it. The model
    # still wins - it reads phrasing the rules cannot - but a systematic
    # disagreement is the signal that the prompt or the model has drifted.
    if guess.confident and guess.kind is not kind:
        logger.info(
            "ai_intent_disagreement",
            extra={"model_intent": kind.value, "keyword_intent": guess.kind.value},
        )

    def take(field: str, *, allow_absent: bool = False) -> str | None:
        raw_value = raw.get(field)
        cleaned = clean_hint(raw_value, question=question, allow_absent=allow_absent)
        if cleaned is None and isinstance(raw_value, str) and raw_value.strip():
            if raw_value.strip().lower() not in NULL_LIKE_HINTS:
                dropped.append(
                    f"Ignored a {field.replace('_hint', '')} of "
                    f"{raw_value.strip()[:MAX_HINT_CHARS]!r} because it does not appear in "
                    "the question."
                )
        return cleaned

    # The period is exempt from the verbatim check and falls back to the rules,
    # which recognise month names and relative phrases directly.
    period = take("period_hint", allow_absent=True) or guess.period_hint

    return Intent(
        kind=kind,
        kpi_hint=take("kpi_hint") or guess.kpi_hint,
        period_hint=period,
        dimension_hint=take("dimension_hint"),
        segment_hint=take("segment_hint") or guess.segment_hint,
        dropped_hints=tuple(dropped),
    )
