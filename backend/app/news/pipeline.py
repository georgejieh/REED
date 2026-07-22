"""News search pipeline.

Orchestrates a session's seed queries against the configured search
provider, deduplicates the results, and caps the URL list at a
session-appropriate limit. The output is a list of URLs that the
agent runner can scrape and feed into the model.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.news.search import RatelimitException, SearchProvider, SearchResult

logger = logging.getLogger(__name__)

TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
    }
)

OUTLET_QUERIES: dict[str, list[str]] = {
    "pre_market": [
        "S&P 500 futures site:finance.yahoo.com",
        "pre-market movers site:finance.yahoo.com",
        "US market futures site:www.cnbc.com",
        "pre-market trading site:www.marketwatch.com",
        "overnight news site:apnews.com",
        "economic calendar today site:www.investing.com",
    ],
    "early_market": [
        "opening print site:www.cnbc.com",
        "sector rotation site:www.marketwatch.com",
        "first hour volume site:finance.yahoo.com",
        "opening bell site:apnews.com",
    ],
    "midday": [
        "midday market update site:www.cnbc.com",
        "lunch lull sectors site:www.marketwatch.com",
        "midday movers site:finance.yahoo.com",
    ],
    "close": [
        "market close summary site:www.cnbc.com",
        "biggest winners losers site:www.marketwatch.com",
        "after hours earnings site:finance.yahoo.com",
        "closing print site:apnews.com",
    ],
    "weekend_recap": [
        "weekly recap site:apnews.com",
        "sector winners losers week site:www.marketwatch.com",
        "upcoming week calendar site:www.cnbc.com",
    ],
}


def _normalize_url(url: str) -> str:
    """Drop fragment and tracking query params, normalize scheme/host case."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if parsed.port and parsed.port not in (80, 443):
        host = f"{host}:{parsed.port}"
    netloc = host
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(query_pairs)
    return urlunparse(
        (scheme, netloc, parsed.path, parsed.params, query, "")
    )


def dedupe_urls(urls: Sequence[str]) -> list[str]:
    """Drop duplicate URLs (after normalization) preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        norm = _normalize_url(url)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def seed_search(
    session: str,
    *,
    provider: SearchProvider,
    max_urls: int = 10,
    queries: Sequence[str] | None = None,
    timelimit: str = "d",
) -> list[str]:
    """Run the session's seed queries and return a deduped URL list.

    `queries` overrides the per-session default queries when supplied
    (used by tests). `max_urls` caps the total.
    """
    chosen = list(queries) if queries else OUTLET_QUERIES.get(session, [])
    if not chosen:
        logger.warning("no seed queries for session %s", session)
        return []

    all_urls: list[str] = []
    for query in chosen:
        try:
            hits: list[SearchResult] = provider.search(
                query, timelimit=timelimit, max_results=5
            )
        except RatelimitException:
            logger.warning("rate-limited on query %r, stopping seed search", query)
            break
        except Exception as exc:
            logger.warning("seed search query %r failed: %s", query, exc)
            continue
        for hit in hits:
            if hit.url:
                all_urls.append(hit.url)

    deduped = dedupe_urls(all_urls)
    if len(deduped) > max_urls:
        deduped = deduped[:max_urls]
    return deduped


def run_pipeline(
    queries: Sequence[str],
    *,
    provider: SearchProvider,
    max_urls: int = 20,
    timelimit: str = "d",
) -> list[str]:
    """Run the full pipeline: search -> dedupe -> cap.

    Use this when the agent runner wants to run an ad-hoc query set
    rather than a session's default seed queries.
    """
    if not queries:
        return []
    all_urls: list[str] = []
    for query in queries:
        try:
            hits = provider.search(query, timelimit=timelimit, max_results=10)
        except RatelimitException:
            logger.warning("rate-limited on query %r, stopping", query)
            break
        except Exception as exc:
            logger.warning("pipeline query %r failed: %s", query, exc)
            continue
        for hit in hits:
            if hit.url:
                all_urls.append(hit.url)
    deduped = dedupe_urls(all_urls)
    if len(deduped) > max_urls:
        deduped = deduped[:max_urls]
    return deduped
