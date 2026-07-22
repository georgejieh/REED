"""Factory that returns the right SearchProvider for the configured name."""

from __future__ import annotations

import logging

from app.config import AppConfig
from app.news.brave import BraveProvider
from app.news.ddgs import DdgsProvider
from app.news.search import SearchProvider
from app.news.tavily import TavilyProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES: dict[str, type[SearchProvider]] = {
    "ddgs": DdgsProvider,
    "brave": BraveProvider,
    "tavily": TavilyProvider,
}


def get_search_provider(config: AppConfig) -> SearchProvider:
    name = config.search.provider.value
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown search provider '{name}'. "
            f"Valid options: {sorted(_PROVIDER_CLASSES)}"
        )
    rate_limit = config.search.rate_limit_per_minute
    if cls is BraveProvider:
        api_key = config.api_keys.get("brave")
        if not api_key:
            raise ValueError("BraveProvider requires BRAVE_API_KEY in .env")
        return cls(api_key=api_key, rate_limit_per_minute=rate_limit)
    if cls is TavilyProvider:
        api_key = config.api_keys.get("tavily")
        if not api_key:
            raise ValueError("TavilyProvider requires TAVILY_API_KEY in .env")
        return cls(api_key=api_key, rate_limit_per_minute=rate_limit)
    return cls(rate_limit_per_minute=rate_limit)
