"""Factory that returns the right provider class for the configured name."""

from __future__ import annotations

import logging

from app.config import AppConfig
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_compatible_provider import OpenAICompatibleProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.openrouter_provider import OpenRouterProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


def get_provider(config: AppConfig) -> LLMProvider:
    provider_name = config.provider.value
    cls = _PROVIDER_CLASSES.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unknown provider '{provider_name}'. "
            f"Valid options: {sorted(_PROVIDER_CLASSES)}"
        )
    api_key = config.api_keys.get(provider_name)
    if cls is OpenAICompatibleProvider and not config.base_url:
        raise ValueError("openai_compatible provider requires base_url in settings.yaml")
    logger.info(
        "initialising provider=%s model=%s base_url=%s",
        provider_name,
        config.model,
        config.base_url,
    )
    return cls(model=config.model, api_key=api_key, base_url=config.base_url)
