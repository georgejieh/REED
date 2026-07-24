"""Tiered search provider that falls back on rate-limit or quota errors.

The factory builds this when settings.yaml has a non-empty
fallback_providers list. The primary provider is tried first; on
RatelimitException, the next provider in the list is tried, and so
on. Non-rate-limit errors (e.g., malformed response, network
timeout) are NOT caught here, because the caller already retries
those at the per-call level in pipeline.py.

Usage: see get_search_provider() in factory.py.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.news.search import RatelimitException, SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class TieredSearchProvider(SearchProvider):
    """Wraps an ordered list of providers with fall back on quota errors."""

    name = "tiered"

    def __init__(self, providers: Sequence[SearchProvider]):
        if not providers:
            raise ValueError("TieredSearchProvider requires at least one provider")
        super().__init__(
            rate_limit_per_minute=providers[0].rate_limit_per_minute
        )
        self._providers = list(providers)

    @property
    def providers(self) -> list[SearchProvider]:
        return list(self._providers)

    def search(
        self,
        query: str,
        *,
        timelimit: str = "d",
        max_results: int = 20,
    ) -> list[SearchResult]:
        last_error: Exception | None = None
        for idx, provider in enumerate(self._providers):
            try:
                hits = provider.search(
                    query, timelimit=timelimit, max_results=max_results
                )
                if idx > 0:
                    logger.info(
                        "search fell back to %s (after %d earlier providers failed)",
                        provider.name,
                        idx,
                    )
                return hits
            except RatelimitException as exc:
                last_error = exc
                logger.warning(
                    "provider %s rate-limited on query %r: %s; trying next",
                    provider.name,
                    query[:80],
                    exc,
                )
                continue
        assert last_error is not None
        raise last_error
