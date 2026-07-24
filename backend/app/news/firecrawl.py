"""Firecrawl news search and scrape backend.

Firecrawl combines search and scrape in one call: the /v2/search
endpoint accepts a query and returns results with scraped markdown
content attached. This eliminates the two-step "search then scrape"
pattern that the DDG/trafilatura pipeline uses, and it works from
HF Spaces (where DDG and direct scraping are unreliable).

Free tier: 500 credits per month on the basic plan; 1000 on the
Hacker plan. Each /v2/search call with 5 results costs 5 credits.
A REED session uses 1-3 calls; 5 sessions/day * 3 calls = 15
credits/day, 450/month. Comfortably under either plan.

When the monthly cap is hit, Firecrawl returns HTTP 429 with a
specific error code. The TieredSearchProvider catches that and
falls back to the next configured provider (typically Brave).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.news.search import RatelimitException, SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class FirecrawlProvider(SearchProvider):
    name = "firecrawl"

    BASE_URL = "https://api.firecrawl.dev/v2/search"

    def __init__(self, *, api_key: str, rate_limit_per_minute: int = 12):
        super().__init__(rate_limit_per_minute=rate_limit_per_minute)
        if not api_key:
            raise ValueError("FirecrawlProvider requires FIRECRAWL_API_KEY")
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=60.0,
        )

    def search(
        self,
        query: str,
        *,
        timelimit: str = "d",
        max_results: int = 5,
    ) -> list[SearchResult]:
        """Search Firecrawl and return scraped markdown for each hit.

        Firecrawl returns up to `max_results` URLs with full article
        markdown attached. We map that into the SearchResult shape;
        the full markdown goes in `snippet` (the LLM reads it as
        context), and the URL is in `url` so the agent can also use
        `scrape_url` if it wants to re-fetch.
        """
        if timelimit == "d":
            tlimit_param = "qdr:d"
        elif timelimit == "w":
            tlimit_param = "qdr:w"
        elif timelimit == "m":
            tlimit_param = "qdr:m"
        else:
            tlimit_param = None

        payload: dict[str, Any] = {
            "query": query,
            "limit": min(max_results, 25),
            "sources": [
                {
                    "type": "news",
                    "tbs": tlimit_param if tlimit_param is not None else "qdr:d",
                }
            ],
            "scrapeOptions": {
                "formats": [{"type": "markdown"}],
                "onlyMainContent": True,
            },
        }

        try:
            response = self._client.post("", json=payload)
        except httpx.HTTPError as exc:
            raise RatelimitException(f"firecrawl request failed: {exc}") from exc

        if response.status_code == 429:
            body = response.text[:200]
            raise RatelimitException(f"firecrawl 429: {body}")
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"firecrawl auth failed ({response.status_code}): {response.text[:200]}"
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"firecrawl status {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        if not data.get("success"):
            error_msg = data.get("error", "unknown firecrawl error")
            if "quota" in error_msg.lower() or "credit" in error_msg.lower() or "limit" in error_msg.lower():
                raise RatelimitException(f"firecrawl quota: {error_msg}")
            raise RuntimeError(f"firecrawl: {error_msg}")

        # Firecrawl v2 returns hits nested under the source type.
        # With sources=[{"type": "news"}], hits are in data.web (a
        # common key for web and news results both). Some responses
        # also expose data.news; we try both.
        hits = data.get("data", {}).get("web") or data.get("data", {}).get("news") or []
        if not hits and isinstance(data.get("data"), list):
            hits = data.get("data", [])
        results: list[SearchResult] = []
        for hit in hits:
            markdown = (hit.get("markdown") or "").strip()
            snippet = markdown[:2000] if markdown else (hit.get("description") or "").strip()
            results.append(
                SearchResult(
                    title=str(hit.get("title", "")).strip(),
                    url=str(hit.get("url", "")).strip(),
                    snippet=snippet,
                    source=str(hit.get("source", "") or "").strip() or None,
                    published_at=None,
                )
            )
        return results

    def scrape_single(self, url: str) -> str:
        """Scrape a single URL and return its markdown content.

        Costs 1 Firecrawl credit per call. Used by the agent
        loop when the LLM asks for full article text (the
        `scrape_url` tool). Returns an empty string on failure
        so the caller can fall back to the next provider.
        """
        try:
            response = self._client.post(
                "/scrape",
                json={
                    "url": url,
                    "formats": [{"type": "markdown"}],
                    "onlyMainContent": True,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("firecrawl scrape_single request failed for %s: %s", url, exc)
            return ""
        if response.status_code == 429:
            logger.warning("firecrawl scrape_single rate-limited for %s", url)
            return ""
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"firecrawl auth failed ({response.status_code}): {response.text[:200]}"
            )
        if response.status_code != 200:
            logger.warning(
                "firecrawl scrape_single status %d for %s: %s",
                response.status_code,
                url,
                response.text[:200],
            )
            return ""
        data = response.json()
        if not data.get("success"):
            logger.warning("firecrawl scrape_single failed: %s", data.get("error"))
            return ""
        return (data.get("data", {}).get("markdown") or "").strip()
