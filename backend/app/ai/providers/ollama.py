"""Ollama, over its HTTP chat API.

The outbound-IO boundary for this layer, modelled on ``connectors.sqlserver``:
narrow interface, failures mapped to typed ``UpstreamError`` codes, and upstream
error text truncated before it is surfaced or logged.

Three request settings are load-bearing rather than incidental:

* ``format`` carries the JSON Schema. Ollama constrains generation to it, which is
  what makes a typed intent reliable on a small local model - without it the
  output was valid JSON but the wrong shape often enough to need a parser.
* ``think: false`` disables the thinking block on reasoning models. The intent call
  does not benefit from it and it doubles latency.
* ``temperature: 0``. Two identical questions should produce the same intent, and a
  sampled classification is a flaky test waiting to happen.
"""

import json
import time
from typing import Any

import httpx

from app.ai.providers.base import LlmProvider, Message
from app.core.config import settings
from app.core.exceptions import UpstreamError, UpstreamTimeoutError
from app.core.logging import get_logger

logger = get_logger(__name__)

# An Ollama error body can quote the offending request back, so it is truncated
# rather than stored or surfaced whole - the same reasoning as
# ``sqlserver.sanitize_db_error``.
MAX_ERROR_CHARS = 300

# Enough for a business paragraph and a caveat. The explanation is meant to be
# read, not scrolled, and an unbounded generation on a local model is the one way
# a request outlives its timeout for a reason nobody can see.
DEFAULT_MAX_TOKENS = 700


def _clamp(timeout_seconds: int | None) -> int:
    """Requested timeout, bounded by the operator's ceiling.

    Same shape as ``sql_service``: ``min(requested or default, max)``.
    """
    requested = timeout_seconds or settings.ai_request_timeout_seconds
    return min(requested, settings.ai_max_timeout_seconds)


class OllamaProvider(LlmProvider):
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_model
        # The seam a test drives, via httpx.MockTransport. An injected transport
        # rather than a patched module function, so the request this provider
        # actually builds - headers, JSON body, the schema in `format` - is what
        # gets asserted on.
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    # -- requests

    def _client(self, timeout_seconds: float) -> httpx.Client:
        """A client per call rather than one held open.

        The layer makes at most two model calls per request, so pooling buys
        nothing measurable, and a long-lived client would need a lifespan hook and
        a shutdown path for that non-benefit.
        """
        return httpx.Client(timeout=timeout_seconds, transport=self._transport)

    def _post(self, path: str, payload: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
        """One call, with every failure mode mapped to a typed error."""
        url = f"{self._base_url}{path}"
        try:
            with self._client(timeout_seconds) as client:
                response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(
                f"The language model did not respond within {timeout_seconds}s.",
                code="LLM_TIMEOUT",
                details={"model": self._model, "timeout_seconds": timeout_seconds},
            ) from exc
        except httpx.HTTPError as exc:
            # Unreachable, refused, DNS, TLS. All the same thing to a caller: the
            # model is not there.
            raise UpstreamError(
                "The language model is not reachable.",
                code="LLM_UNAVAILABLE",
                details={"model": self._model, "base_url": self._base_url},
            ) from exc

        if response.status_code >= 400:
            raise UpstreamError(
                f"The language model rejected the request ({response.status_code}).",
                code="LLM_REQUEST_REJECTED",
                details={
                    "model": self._model,
                    "status": response.status_code,
                    "message": response.text[:MAX_ERROR_CHARS],
                },
            )

        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                "The language model returned a response that is not JSON.",
                code="LLM_INVALID_OUTPUT",
                details={"model": self._model},
            ) from exc

    def _chat(
        self,
        messages: list[Message],
        *,
        timeout_seconds: int,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            # Reasoning models otherwise emit a thinking block this layer has no
            # use for, at roughly double the latency.
            "think": False,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "options": {
                "temperature": 0,
                "num_predict": max_tokens or DEFAULT_MAX_TOKENS,
            },
        }
        if schema is not None:
            payload["format"] = schema

        started = time.perf_counter()
        body = self._post("/api/chat", payload, timeout_seconds=timeout_seconds)
        elapsed = int((time.perf_counter() - started) * 1000)

        content = (body.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise UpstreamError(
                "The language model returned an empty response.",
                code="LLM_INVALID_OUTPUT",
                details={"model": self._model},
            )

        # The question and the generated text are deliberately absent: the
        # redacting filter does not scrub free prose, and a question can name a
        # customer.
        logger.info(
            "llm_call",
            extra={
                "model": self._model,
                "duration_ms": elapsed,
                "structured": schema is not None,
                "eval_count": body.get("eval_count"),
            },
        )
        return content

    # -- the interface

    def complete_json(
        self,
        messages: list[Message],
        schema: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        content = self._chat(
            messages,
            timeout_seconds=_clamp(timeout_seconds),
            schema=schema,
            max_tokens=max_tokens,
        )
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise UpstreamError(
                "The language model did not honour the requested JSON schema.",
                code="LLM_INVALID_OUTPUT",
                details={"model": self._model},
            ) from exc
        if not isinstance(parsed, dict):
            raise UpstreamError(
                "The language model returned JSON that is not an object.",
                code="LLM_INVALID_OUTPUT",
                details={"model": self._model, "type": type(parsed).__name__},
            )
        return parsed

    def health(self) -> dict[str, Any]:
        """Reachability as a dict, never an exception.

        ``/api/tags`` rather than a generation: it answers whether the daemon is
        up and whether this model is actually installed, which are the two ways
        this fails, without paying for a model load.
        """
        started = time.perf_counter()
        try:
            with self._client(5) as client:
                response = client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            installed = [m.get("name") for m in response.json().get("models", [])]
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "model": self._model,
                "error_code": "LLM_UNAVAILABLE",
                "message": str(exc)[:MAX_ERROR_CHARS],
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        except ValueError:
            return {
                "ok": False,
                "model": self._model,
                "error_code": "LLM_INVALID_OUTPUT",
                "message": "The model daemon returned a response that is not JSON.",
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }

        latency = int((time.perf_counter() - started) * 1000)
        # Ollama reports "qwen3:4b"; a configured "qwen3" should still match, so
        # the comparison is on the name before the tag.
        present = any(
            name == self._model or (name or "").split(":")[0] == self._model.split(":")[0]
            for name in installed
        )
        if not present:
            return {
                "ok": False,
                "model": self._model,
                "error_code": "LLM_MODEL_NOT_INSTALLED",
                "message": f"Pull it with: ollama pull {self._model}",
                "latency_ms": latency,
                "installed": installed,
            }
        return {
            "ok": True,
            "model": self._model,
            "message": "The language model is reachable.",
            "latency_ms": latency,
        }
