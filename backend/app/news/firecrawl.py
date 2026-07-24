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
            timeout=20.0,
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
            "limit": min(max_results, 10),
            "scrapeOptions": {
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
        }
        if tlimit_param is not None:
            payload["tbs"] = tlimit_param

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

        results: list[SearchResult] = []
        for hit in data.get("data", []):
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
