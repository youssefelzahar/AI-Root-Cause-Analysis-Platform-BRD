"""Provider selection.

Same shape as ``services.storage_service``: a setting names the implementation, an
``lru_cache`` factory builds it, an unknown name is a 500 rather than a silent
fallback, and a reset hook lets a test swap it after overriding settings.
"""

from functools import lru_cache

from app.ai.providers.base import LlmProvider, Message
from app.ai.providers.fake import FakeProvider
from app.ai.providers.ollama import OllamaProvider
from app.core.config import settings
from app.core.exceptions import AppError

__all__ = ["LlmProvider", "Message", "get_llm_provider", "reset_ai_cache"]


@lru_cache
def get_llm_provider() -> LlmProvider:
    provider = settings.ai_provider.lower()
    if provider == "ollama":
        return OllamaProvider()
    if provider == "fake":
        # A canned provider in production would answer business questions with
        # mechanical prose and nobody would know why, so it is refused the same
        # way the placeholder encryption key is.
        if not settings.is_development:
            raise AppError(
                'AI_PROVIDER="fake" is only allowed when APP_ENV is development.',
                code="INVALID_AI_PROVIDER",
                status_code=500,
            )
        return FakeProvider()
    raise AppError(
        f"Unknown AI provider: {settings.ai_provider}",
        code="INVALID_AI_PROVIDER",
        status_code=500,
    )


def reset_ai_cache() -> None:
    """Test hook - drops the cached provider after settings are overridden."""
    get_llm_provider.cache_clear()
