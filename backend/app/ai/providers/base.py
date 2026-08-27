"""The LLM provider interface.

One generation method, because everything this layer asks a model for is a typed
object - the intent, and the answer. Anything richer (streaming, native tool
calling, embeddings) would be an interface the agent does not use, and an unused
abstraction is one that is wrong by the time something needs it.

There was a ``complete_text`` here, and it was removed after measurement rather
than on principle. Asked for a paragraph in free text, ``qwen3:4b`` wrote its whole
deliberation - restating the rules it had been given, arguing with itself, and
running into the token limit before reaching the answer. The numeric guard
correctly rejected every one of those. Asked for the *same* paragraph as
``{"answer": "..."}`` under a schema, it wrote three clean sentences that passed,
identically on every attempt.

So schema-constrained generation is not a nicety for structured data here. It is
what makes a small local model usable for prose at all.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Message:
    """One turn handed to the model.

    Deliberately not the provider's own wire format: Ollama and OpenAI agree on
    ``{"role", "content"}`` today, and a provider that disagrees can map this in
    its own adapter rather than forcing every caller to.
    """

    role: str
    content: str


class LlmProvider(ABC):
    """What the AI analyst needs from a language model.

    Implementations must map their transport failures onto ``UpstreamError`` and
    ``UpstreamTimeoutError``, so a dead model daemon reads the same way as a dead
    SQL Server: a 502 or 504 with a typed code, never a 500 traceback.
    """

    @property
    @abstractmethod
    def model(self) -> str:
        """The model actually being used, for the response and the log line."""

    @abstractmethod
    def complete_json(
        self,
        messages: list[Message],
        schema: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """A single object conforming to ``schema``.

        Raises ``UpstreamError`` if the response is not parseable as JSON, because
        by this point the schema was the model's contract and a violation of it is
        the provider's failure, not the caller's.
        """

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Whether the model is reachable, as a result rather than an exception.

        The shape follows ``connectors.sqlserver.test_connection``:
        ``{"ok", "model", "message", "latency_ms"}`` plus ``"error_code"`` when it
        is not ok. A provider being down is a normal, renderable state - the AI
        surface is meant to say so and still show the analysis.
        """
