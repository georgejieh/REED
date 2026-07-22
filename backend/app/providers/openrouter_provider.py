"""OpenRouter provider using the OpenAI-compatible API."""

from __future__ import annotations

from app.providers.openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter is OpenAI-API-compatible with a fixed base URL."""

    name = "openrouter"

    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None):
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
        )
