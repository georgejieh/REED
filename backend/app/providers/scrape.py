"""Article scraper with Firecrawl primary and httpx fall back.

Used by the agent's `scrape_url` tool. Tries Firecrawl first
(1 credit per call, returns clean markdown), falls back to
direct httpx + trafilatura (free, but slow or blocked from
HF Spaces). The fall back exists so REED can still operate
when Firecrawl is exhausted or unreachable; it just becomes
slower and less reliable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
import trafilatura

from app.providers.tools import DEFAULT_TIMEOUT_SECONDS, MAX_REDIRECTS, ScrapeResult, _is_safe_url

logger = logging.getLogger(__name__)


_FIRECRAWL_BASE = "https://api.firecrawl.dev/v2"


@dataclass
class _FirecrawlScrapeClient:
    """Minimal Firecrawl /v2/scrape wrapper, lazy-initialized."""

    api_key: str
    _client: httpx.Client | None = None

    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=_FIRECRAWL_BASE,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=60.0,
            )
        return self._client


def _scrape_with_firecrawl(url: str, api_key: str) -> str:
    """Hit Firecrawl /v2/scrape and return markdown. Empty string on failure.

    Raises RuntimeError on auth failure (so the caller knows the key
    is wrong, not just that this URL failed).
    """
    client = _FirecrawlScrapeClient(api_key).client()
    try:
        response = client.post(
            "/scrape",
            json={
                "url": url,
                "formats": [{"type": "markdown"}],
                "onlyMainContent": True,
            },
        )
    except httpx.HTTPError as exc:
        logger.warning("firecrawl scrape http error for %s: %s", url, exc)
        return ""
    if response.status_code == 429:
        logger.warning("firecrawl scrape 429 for %s (quota or rate limit)", url)
        return ""
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"firecrawl auth failed ({response.status_code}): {response.text[:200]}"
        )
    if response.status_code != 200:
        logger.warning(
            "firecrawl scrape status %d for %s: %s",
            response.status_code,
            url,
            response.text[:200],
        )
        return ""
    data: dict[str, Any] = response.json()
    if not data.get("success"):
        logger.warning("firecrawl scrape failed: %s", data.get("error"))
        return ""
    return (data.get("data", {}).get("markdown") or "").strip()


def _scrape_with_httpx(url: str) -> str:
    """Fall back to direct httpx + trafilatura. Free but slow from HF Spaces."""
    try:
        with httpx.Client(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=httpx.Timeout(
                DEFAULT_TIMEOUT_SECONDS,
                connect=DEFAULT_TIMEOUT_SECONDS,
                read=DEFAULT_TIMEOUT_SECONDS,
                write=DEFAULT_TIMEOUT_SECONDS,
            ),
            headers={
                "User-Agent": "REED/0.1 (+https://github.com/georgejieh/REED)",
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("scrape http error for %s: %s", url, exc)
        return ""
    if response.status_code >= 400:
        return ""
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower() and "xml" not in content_type.lower():
        return ""
    html = response.text
    if not html:
        return ""
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
        with_metadata=False,
    ) or ""
    return text.strip()


def scrape_article(url: str) -> ScrapeResult:
    """Fetch the article text at `url`.

    Tries Firecrawl first when FIRECRAWL_API_KEY is set; falls back
    to direct httpx + trafilatura. SSRF protection runs first so
    neither path is reached for unsafe URLs.
    """
    if not url:
        return ScrapeResult(url=url, text="", ok=False, error="empty url")

    safe, reason = _is_safe_url(url)
    if not safe:
        return ScrapeResult(url=url, text="", ok=False, error=f"blocked: {reason}")

    api_key = os.environ.get("FIRECRAWL_API_KEY")
    text = ""
    if api_key:
        try:
            text = _scrape_with_firecrawl(url, api_key)
        except Exception as exc:
            logger.warning("firecrawl scrape raised for %s: %s; falling back", url, exc)
            text = ""
    if not text:
        text = _scrape_with_httpx(url)

    if not text:
        return ScrapeResult(
            url=url, text="", ok=False, error="extraction returned no text"
        )
    return ScrapeResult(url=url, text=text, ok=True)
