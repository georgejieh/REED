"""Ollama provider.

The local variant uses http://localhost:11434 with no API key.
Both local and cloud speak the OpenAI-compatible API surface, so this
class delegates to OpenAIProvider.
"""

from __future__ import annotations

from app.providers.openai_provider import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    name = "ollama"

    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None):
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
        )
