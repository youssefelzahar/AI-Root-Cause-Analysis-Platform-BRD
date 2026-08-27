"""A deterministic provider, so the suite runs with no model installed.

Ships in ``app`` rather than in ``tests`` for one reason: it is selected by
``AI_PROVIDER``, and a setting that can only be satisfied by test-only code is a
setting that breaks the moment someone sets it. Refused outside development by
``get_llm_provider``, the same way the placeholder encryption key is.

It is not a mock. It classifies intent with the keyword rules in ``keywords`` and
writes its explanation from the bundle summary it is handed, so a test exercising
the real pipeline gets a plausible answer without asserting on a model's prose.
The prose it produces is deliberately plain - a test that depends on wording is a
test that breaks when the prompt improves.
"""

from typing import Any

from app.ai import keywords
from app.ai.providers.base import LlmProvider, Message

# Marks a generated answer as coming from this provider, so a developer who has
# forgotten AI_PROVIDER=fake is set can see why the prose looks mechanical.
FAKE_MODEL = "fake"


def _labelled(text: str) -> dict[str, str]:
    """The ``label: value`` lines of the evidence block, as a dict.

    Only top-level lines: an indented one is a driver, which ``_first_driver``
    handles, and folding the two together would put a segment's numbers under a
    key like "  - region Cairo".
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith(" ") or ":" not in line:
            continue
        label, _, value = line.partition(":")
        fields.setdefault(label.strip(), value.strip())
    return fields


def _first_driver(text: str) -> str | None:
    """The strongest driver's label, from the first indented bullet."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and "|" in stripped:
            return stripped[2:].split("|")[0].strip()
    return None


def _prose(payload: str) -> str:
    """Two or three sentences, built from the labelled evidence lines.

    Quotes only what the evidence block already said, so this provider can never be
    the reason the numeric guard fires - a test that sees the guard fire is seeing a
    real bug, not a fake artefact.

    Deliberately does not echo the question. An earlier version returned the whole
    user message, which meant a question containing the word "caused" produced an
    answer containing it, and the vocabulary rule appeared to be broken by the test
    harness rather than by the code.
    """
    fields = _labelled(payload)
    kpi = fields.get("KPI", "The KPI")
    change = fields.get("absolute change")

    sentences = [f"{kpi} moved by {change}." if change else f"{kpi} was analysed."]
    if fields.get("previous value") and fields.get("current value"):
        sentences.append(
            f"It went from {fields['previous value']} to {fields['current value']}."
        )
    driver = _first_driver(payload)
    if driver:
        sentences.append(f"The largest contributor is {driver}.")
    return " ".join(sentences)


class FakeProvider(LlmProvider):
    """Canned but not constant: the output follows the input."""

    def __init__(self, model: str = FAKE_MODEL) -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete_json(
        self,
        messages: list[Message],
        schema: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Fill the schema's required keys from the last user message.

        Two schemas are asked for: the intent, and the answer. Which one is in play
        is read off the schema's own keys rather than passed in, so this provider
        needs no knowledge of its caller.
        """
        payload = next((m.content for m in reversed(messages) if m.role == "user"), "")
        required = list(schema.get("required") or ())

        if "answer" in required:
            return {"answer": _prose(payload)}

        guess = keywords.classify(payload)
        fields: dict[str, Any] = {
            "intent": guess.kind.value,
            "kpi_hint": guess.kpi_hint,
            "period_hint": guess.period_hint,
            "dimension_hint": guess.dimension_hint,
            "segment_hint": guess.segment_hint,
        }
        # Keys the schema wants but the classifier does not know about come back
        # null, which exercises the caller's sanitisation rather than bypassing it.
        return {key: fields.get(key) for key in (required or fields)}

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "model": self._model,
            "message": "The fake provider is always available.",
            "latency_ms": 0,
        }
