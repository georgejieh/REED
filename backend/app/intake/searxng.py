from __future__ import annotations

import json
import time
from urllib.parse import urlencode, urljoin, urlsplit

from app.config.configuration import SearchConfiguration
from app.intake.article_parser import extract_article_text
from app.intake.models import SearchItem, SearchResult
from app.intake.policy import OutboundUrlPolicy, UnsafeOutboundUrl
from app.intake.rss import canonicalize_url


class SearxngEnricher:
    def __init__(self, transport: object):
        self.transport = transport

    def enrich(
        self,
        configuration: SearchConfiguration,
        *,
        market_window: str,
    ) -> SearchResult:
        if not configuration.enabled:
            return SearchResult(items=(), state="disabled")
        assert configuration.endpoint is not None
        endpoint = urlsplit(configuration.endpoint)
        local_endpoint = _is_loopback_endpoint(configuration.endpoint)
        OutboundUrlPolicy(allow_loopback=local_endpoint).parse(
            configuration.endpoint
        )
        if endpoint.query:
            raise UnsafeOutboundUrl(
                "supplemental search endpoint query strings are not allowed"
            )
        if local_endpoint:
            if endpoint.scheme != "http":
                raise UnsafeOutboundUrl(
                    "local supplemental search endpoint must use loopback HTTP"
                )
        elif endpoint.scheme != "https":
            raise UnsafeOutboundUrl(
                "remote supplemental search endpoint must use HTTPS"
            )
        if (
            not local_endpoint
            and endpoint.port is not None
            and endpoint.port != 443
        ):
            raise UnsafeOutboundUrl(
                "remote supplemental search endpoint port is not allowed"
            )
        started = time.monotonic()
        candidates: list[tuple[str, int, str, str]] = []
        seen: set[str] = set()
        for query in configuration.query_templates[
            : configuration.max_queries_per_run
        ]:
            if (
                time.monotonic() - started
                >= configuration.total_search_budget_seconds
            ):
                break
            search_url = urljoin(
                configuration.endpoint.rstrip("/") + "/",
                "search",
            )
            search_url = f"{search_url}?{urlencode({'q': query, 'format': 'json'})}"
            remaining = (
                configuration.total_search_budget_seconds
                - (time.monotonic() - started)
            )
            response = self.transport.request(
                "GET",
                search_url,
                headers={"accept": "application/json", "user-agent": "REED/0.1"},
                timeout=min(configuration.request_timeout_seconds, remaining),
                max_bytes=512 * 1024,
                follow_redirects=True,
                allow_loopback=local_endpoint,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError("supplemental search returned a non-success status")
            payload = json.loads(response.body)
            results = payload.get("results")
            if not isinstance(results, list):
                raise ValueError("supplemental search response is malformed")
            for rank, result in enumerate(
                results[: configuration.max_results_per_query],
                start=1,
            ):
                if not isinstance(result, dict):
                    continue
                url = result.get("url")
                title = result.get("title")
                if not isinstance(url, str) or not isinstance(title, str):
                    continue
                canonical = canonicalize_url(url)
                if canonical in seen:
                    continue
                seen.add(canonical)
                candidates.append((query, rank, canonical, title.strip()))

        items: list[SearchItem] = []
        for query, rank, url, title in candidates[
            : configuration.max_articles_to_parse
        ]:
            if (
                time.monotonic() - started
                >= configuration.total_search_budget_seconds
            ):
                break
            remaining = (
                configuration.total_search_budget_seconds
                - (time.monotonic() - started)
            )
            response = self.transport.request(
                "GET",
                url,
                headers={"accept": "text/html, text/plain", "user-agent": "REED/0.1"},
                timeout=min(configuration.request_timeout_seconds, remaining),
                max_bytes=configuration.max_article_bytes,
                follow_redirects=True,
            )
            if response.status_code < 200 or response.status_code >= 300:
                continue
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type and "text/plain" not in content_type:
                continue
            content = extract_article_text(response.body)
            items.append(
                SearchItem(
                    query_template=query,
                    rank=rank,
                    canonical_url=canonicalize_url(response.final_url),
                    title=title[:500],
                    content=content,
                    parser_outcome="parsed" if content else "empty",
                    byte_count=len(response.body),
                )
            )
        return SearchResult(
            items=tuple(items),
            state="complete",
        )


def _is_loopback_endpoint(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
