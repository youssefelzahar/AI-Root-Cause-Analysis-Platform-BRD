"""The Ollama provider, driven through an injected transport.

No network and no Ollama: ``httpx.MockTransport`` intercepts at the transport layer,
so what is asserted on is the request this provider actually builds - the schema in
``format``, ``think: false``, ``temperature: 0`` - rather than a stubbed function's
arguments. That matters because those three settings are the difference between
reliable structured output and a parser.

No new dependency was needed. ``httpx`` is already pinned for the test client, and
``MockTransport`` is part of it, which is why ``respx`` is absent.
"""

import json

import httpx
import pytest

from app.ai.providers import get_llm_provider, reset_ai_cache
from app.ai.providers.base import Message
from app.ai.providers.ollama import OllamaProvider
from app.core.config import settings
from app.core.exceptions import AppError, UpstreamError, UpstreamTimeoutError

SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string"}},
    "required": ["intent"],
}
MESSAGES = [Message("system", "you label questions"), Message("user", "Q: why did revenue drop?")]


def _provider(handler) -> OllamaProvider:
    return OllamaProvider(
        base_url="http://model.invalid:11434",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )


def _chat_reply(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"message": {"role": "assistant", "content": content}, "eval_count": 42},
    )


# --- the request this provider builds -----------------------------------------


def test_the_json_schema_is_sent_so_the_model_is_constrained() -> None:
    """Without it the output was valid JSON but the wrong shape often enough to
    need a parser - which is the thing this design removes."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _chat_reply('{"intent":"ROOT_CAUSE_ANALYSIS"}')

    _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert captured["format"] == SCHEMA


def test_thinking_is_disabled_and_sampling_is_deterministic() -> None:
    """A sampled classification is a flaky test waiting to happen, and the thinking
    block doubles latency for no benefit to a label."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _chat_reply('{"intent":"KPI_ANALYSIS"}')

    _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert captured["think"] is False
    assert captured["options"]["temperature"] == 0
    assert captured["stream"] is False


def test_the_configured_model_is_the_one_requested() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _chat_reply('{"intent":"KPI_ANALYSIS"}')

    _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert captured["model"] == "test-model"
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]


def test_a_token_budget_is_passed_through() -> None:
    """An unbounded generation on a local model is the one way a request outlives
    its timeout for a reason nobody can see."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _chat_reply('{"intent":"KPI_ANALYSIS"}')

    _provider(handler).complete_json(MESSAGES, SCHEMA, max_tokens=123)
    assert captured["options"]["num_predict"] == 123


# --- failures, each mapped to a typed error -----------------------------------


def test_a_timeout_becomes_an_upstream_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(UpstreamTimeoutError) as caught:
        _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert caught.value.code == "LLM_TIMEOUT"
    assert caught.value.status_code == 504


def test_an_unreachable_daemon_becomes_an_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(UpstreamError) as caught:
        _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert caught.value.code == "LLM_UNAVAILABLE"
    assert caught.value.status_code == 502


def test_a_server_error_is_reported_with_a_truncated_body() -> None:
    """An Ollama error can quote the whole request back."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * 5000)

    with pytest.raises(UpstreamError) as caught:
        _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert caught.value.code == "LLM_REQUEST_REJECTED"
    assert len(caught.value.details["message"]) <= 300


def test_output_that_is_not_json_is_an_invalid_output_error() -> None:
    """By this point the schema was the model's contract, so a violation of it is
    the provider's failure rather than the caller's parsing chore."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_reply("I think the answer is probably Cairo.")

    with pytest.raises(UpstreamError) as caught:
        _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert caught.value.code == "LLM_INVALID_OUTPUT"


def test_json_that_is_not_an_object_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_reply('["ROOT_CAUSE_ANALYSIS"]')

    with pytest.raises(UpstreamError) as caught:
        _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert caught.value.code == "LLM_INVALID_OUTPUT"


def test_an_empty_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_reply("   ")

    with pytest.raises(UpstreamError) as caught:
        _provider(handler).complete_json(MESSAGES, SCHEMA)
    assert caught.value.code == "LLM_INVALID_OUTPUT"


# --- timeouts -----------------------------------------------------------------


def test_a_requested_timeout_is_clamped_to_the_operator_ceiling() -> None:
    """Same shape as sql_service: min(requested or default, max)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout", {}).get("read")
        return _chat_reply('{"intent":"KPI_ANALYSIS"}')

    _provider(handler).complete_json(MESSAGES, SCHEMA, timeout_seconds=99_999)
    assert seen["timeout"] == settings.ai_max_timeout_seconds


# --- health -------------------------------------------------------------------


def test_health_reports_reachable_when_the_model_is_installed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})

    result = _provider(handler).health()
    assert result["ok"] is True
    assert result["model"] == "test-model"


def test_health_names_the_pull_command_when_the_model_is_missing() -> None:
    """The most common setup failure, and the message is the fix."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})

    result = _provider(handler).health()
    assert result["ok"] is False
    assert result["error_code"] == "LLM_MODEL_NOT_INSTALLED"
    assert "ollama pull test-model" in result["message"]


def test_health_returns_a_result_rather_than_raising_when_unreachable() -> None:
    """A model daemon being down is a normal, renderable state - the same judgement
    connectors.sqlserver.test_connection makes."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = _provider(handler).health()
    assert result["ok"] is False
    assert result["error_code"] == "LLM_UNAVAILABLE"


# --- provider selection -------------------------------------------------------


def test_an_unknown_provider_is_a_server_error_not_a_silent_fallback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "gpt-hopeful")
    reset_ai_cache()
    with pytest.raises(AppError) as caught:
        get_llm_provider()
    assert caught.value.code == "INVALID_AI_PROVIDER"
    reset_ai_cache()


def test_the_fake_provider_is_refused_outside_development(monkeypatch) -> None:
    """Canned prose answering real business questions, with nobody knowing why."""
    monkeypatch.setattr(settings, "ai_provider", "fake")
    monkeypatch.setattr(settings, "app_env", "production")
    reset_ai_cache()
    with pytest.raises(AppError) as caught:
        get_llm_provider()
    assert caught.value.code == "INVALID_AI_PROVIDER"
    reset_ai_cache()


def test_the_suite_runs_against_the_fake_provider() -> None:
    reset_ai_cache()
    assert get_llm_provider().model == "fake"
