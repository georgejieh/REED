"""DuckDuckGo news search via the ddgs PyPI package."""

from __future__ import annotations

import logging
from typing import Any

from app.news.search import SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class DdgsProvider(SearchProvider):
    name = "ddgs"

    def search(
        self,
        query: str,
        *,
        timelimit: str = "d",
        max_results: int = 20,
    ) -> list[SearchResult]:
        from ddgs import DDGS
        try:
            with DDGS() as ddgs:
                hits: list[dict[str, Any]] = list(
                    ddgs.news(query, timelimit=timelimit, max_results=max_results)
                )
        except Exception as exc:
            text = str(exc).lower()
            if "rate" in text or "limit" in text:
                raise RatelimitException(str(exc)) from exc
            logger.warning("ddgs search failed: %s", exc)
            return []
        results: list[SearchResult] = []
        for hit in hits:
            try:
                results.append(
                    SearchResult(
                        title=str(hit.get("title", "")).strip(),
                        url=str(hit.get("url", "")).strip(),
                        snippet=str(hit.get("body", "")).strip(),
                        source=str(hit.get("source", "")).strip() or None,
                        published_at=str(hit.get("date", "")).strip() or None,
                    )
                )
            except Exception as exc:
                logger.debug("skipping malformed ddgs hit: %s", exc)
        return results


# Imported here to keep the class-attribute symbol resolution local.
from app.news.search import RatelimitException
