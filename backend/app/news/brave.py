"""Brave Search news backend."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.news.search import RatelimitException, SearchProvider, SearchResult, backoff_sleeps

logger = logging.getLogger(__name__)


class BraveProvider(SearchProvider):
    name = "brave"

    BASE_URL = "https://api.search.brave.com/res/v1/news/search"

    def __init__(self, *, api_key: str, rate_limit_per_minute: int = 12):
        super().__init__(rate_limit_per_minute=rate_limit_per_minute)
        if not api_key:
            raise ValueError("BraveProvider requires BRAVE_API_KEY")
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
            timeout=10.0,
        )

    def search(
        self,
        query: str,
        *,
        timelimit: str = "d",
        max_results: int = 20,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {"q": query, "count": min(max_results, 20)}
        if timelimit == "d":
            params["freshness"] = "pd"
        elif timelimit == "w":
            params["freshness"] = "pw"
        elif timelimit == "m":
            params["freshness"] = "pm"

        last_exc: Exception | None = None
        for attempt, sleep_s in enumerate([0, *backoff_sleeps()]):
            if sleep_s:
                import time
                time.sleep(sleep_s)
            try:
                response = self._client.get("", params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("brave request failed (attempt %s): %s", attempt, exc)
                continue
            if response.status_code == 429:
                raise RatelimitException(f"brave 429 (attempt {attempt})")
            if response.status_code != 200:
                last_exc = RuntimeError(
                    f"brave status {response.status_code}: {response.text[:200]}"
                )
                continue
            payload = response.json()
            break
        else:
            raise last_exc or RuntimeError("brave search failed without response")

        results: list[SearchResult] = []
        for hit in payload.get("results", []):
            results.append(
                SearchResult(
                    title=str(hit.get("title", "")).strip(),
                    url=str(hit.get("url", "")).strip(),
                    snippet=str(hit.get("description", "")).strip(),
                    source=str(hit.get("meta_url", {}).get("hostname", "")).strip() or None,
                    published_at=str(hit.get("age", "")).strip() or None,
                )
            )
        return results
