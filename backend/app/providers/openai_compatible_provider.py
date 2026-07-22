"""OpenAI-compatible provider for any endpoint that speaks the OpenAI API.

This covers Together, Groq, Fireworks, DeepInfra, Gemini (via OpenAI-
compat endpoint), Mistral, Cohere, xAI, Perplexity, vLLM, llama.cpp,
LM Studio, llamafile, and any future OpenAI-compatible endpoint.

The operator supplies base_url via the wizard or settings.yaml.
"""

from __future__ import annotations

from app.providers.openai_provider import OpenAIProvider


class OpenAICompatibleProvider(OpenAIProvider):
    name = "openai_compatible"

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None):
        if not base_url:
            raise ValueError(
                "openai_compatible provider requires base_url; "
                "set it in settings.yaml or pass --base-url to the wizard."
            )
        super().__init__(model=model, api_key=api_key, base_url=base_url)
