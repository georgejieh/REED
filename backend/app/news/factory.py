"""Factory that returns the right SearchProvider for the configured name.

If settings.yaml has a non-empty `fallback_providers` list, the
factory wraps the primary and fall-back providers in a
TieredSearchProvider. Providers whose API key env var is missing
are skipped (with a warning) so an operator can configure partial
fall back without breaking startup.
"""

from __future__ import annotations

import logging
import os

from app.config import AppConfig
from app.news.brave import BraveProvider
from app.news.ddgs import DdgsProvider
from app.news.firecrawl import FirecrawlProvider
from app.news.search import SearchProvider
from app.news.tavily import TavilyProvider
from app.news.tiered import TieredSearchProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES: dict[str, type[SearchProvider]] = {
    "ddgs": DdgsProvider,
    "brave": BraveProvider,
    "tavily": TavilyProvider,
    "firecrawl": FirecrawlProvider,
}

_PROVIDER_KEY_NAMES: dict[str, str] = {
    "brave": "brave",
    "tavily": "tavily",
    "firecrawl": "firecrawl",
}


def _instantiate(
    name: str,
    rate_limit: int,
    api_keys: dict[str, str],
) -> SearchProvider | None:
    """Build a single provider. Returns None if the API key is missing.

    ddgs has no key and is always built successfully.
    """
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        logger.warning("unknown search provider %r; skipping", name)
        return None
    key_name = _PROVIDER_KEY_NAMES.get(name)
    if key_name is not None and not api_keys.get(key_name):
        # ddgs is keyless; for others, missing key means skip silently
        if name != "ddgs":
            logger.info(
                "search provider %r skipped: %s_API_KEY not set",
                name,
                key_name.upper(),
            )
            return None
    if cls is BraveProvider:
        return cls(api_key=api_keys["brave"], rate_limit_per_minute=rate_limit)
    if cls is TavilyProvider:
        return cls(api_key=api_keys["tavily"], rate_limit_per_minute=rate_limit)
    if cls is FirecrawlProvider:
        return cls(api_key=api_keys["firecrawl"], rate_limit_per_minute=rate_limit)
    return cls(rate_limit_per_minute=rate_limit)


def get_search_provider(config: AppConfig) -> SearchProvider:
    """Return the configured search provider, with fall back if listed.

    The primary provider is built first. If `fallback_providers` is
    non-empty, each listed provider is built in order and the result
    is wrapped in a TieredSearchProvider. Providers whose API key is
    missing are skipped.
    """
    primary_name = config.search.provider.value
    rate_limit = config.search.rate_limit_per_minute
    api_keys = config.api_keys

    primary = _instantiate(primary_name, rate_limit, api_keys)
    if primary is None:
        raise ValueError(
            f"primary search provider {primary_name!r} could not be built "
            f"(missing API key). Valid providers: {sorted(_PROVIDER_CLASSES)}"
        )

    fallbacks: list[SearchProvider] = []
    for fb_name in config.search.fallback_providers:
        fb = _instantiate(fb_name, rate_limit, api_keys)
        if fb is not None:
            fallbacks.append(fb)

    if not fallbacks:
        return primary
    return TieredSearchProvider([primary, *fallbacks])
