"""Ollama provider supporting both local Ollama and Ollama Cloud.

The local variant uses http://localhost:11434 with no API key.
The cloud variant uses https://ollama.com with an OLLAMA_API_KEY.

Both variants speak the OpenAI-compatible API surface, so this class
delegates to OpenAIProvider.
"""

from __future__ import annotations

from app.providers.base import LLMProvider
from app.providers.openai_provider import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    name = "ollama"

    DEFAULT_LOCAL_BASE_URL = "http://localhost:11434"
    DEFAULT_CLOUD_BASE_URL = "https://ollama.com"

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None):
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url or self.DEFAULT_LOCAL_BASE_URL,
        )
