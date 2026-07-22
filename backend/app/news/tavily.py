"""Tavily search backend."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.news.search import RatelimitException, SearchProvider, SearchResult, backoff_sleeps

logger = logging.getLogger(__name__)


class TavilyProvider(SearchProvider):
    name = "tavily"

    BASE_URL = "https://api.tavily.com/search"

    def __init__(self, *, api_key: str, rate_limit_per_minute: int = 12):
        super().__init__(rate_limit_per_minute=rate_limit_per_minute)
        if not api_key:
            raise ValueError("TavilyProvider requires TAVILY_API_KEY")
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )

    def search(
        self,
        query: str,
        *,
        timelimit: str = "d",
        max_results: int = 20,
    ) -> list[SearchResult]:
        days = {"d": 1, "w": 7, "m": 30}.get(timelimit, 1)
        body: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": min(max_results, 20),
            "topic": "news",
            "days": days,
        }

        last_exc: Exception | None = None
        for attempt, sleep_s in enumerate([0, *backoff_sleeps()]):
            if sleep_s:
                import time
                time.sleep(sleep_s)
            try:
                response = self._client.post("", json=body)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("tavily request failed (attempt %s): %s", attempt, exc)
                continue
            if response.status_code == 429:
                raise RatelimitException(f"tavily 429 (attempt {attempt})")
            if response.status_code != 200:
                last_exc = RuntimeError(
                    f"tavily status {response.status_code}: {response.text[:200]}"
                )
                continue
            payload = response.json()
            break
        else:
            raise last_exc or RuntimeError("tavily search failed without response")

        results: list[SearchResult] = []
        for hit in payload.get("results", []):
            results.append(
                SearchResult(
                    title=str(hit.get("title", "")).strip(),
                    url=str(hit.get("url", "")).strip(),
                    snippet=str(hit.get("content", "")).strip(),
                    source=None,
                    published_at=str(hit.get("publishedDate", "")).strip() or None,
                )
            )
        return results
