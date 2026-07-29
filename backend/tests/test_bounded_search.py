from __future__ import annotations

import json

import pytest

from app.config.configuration import SearchConfiguration
from app.intake.policy import OutboundResponse, UnsafeOutboundUrl
from app.intake.searxng import SearxngEnricher


class SearchTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def request(self, method: str, url: str, **_: object) -> OutboundResponse:
        self.urls.append(url)
        if "/search?" in url:
            body = json.dumps(
                {
                    "results": [
                        {"url": "https://news.example.com/one", "title": "One"},
                        {"url": "https://news.example.com/two", "title": "Two"},
                    ]
                }
            ).encode()
            return OutboundResponse(200, {"content-type": "application/json"}, body, url)
        return OutboundResponse(
            200,
            {"content-type": "text/html"},
            b"<html><body><h1>One</h1><p>Article text.</p></body></html>",
            url,
        )


def test_disabled_search_performs_no_egress() -> None:
    transport = SearchTransport()

    result = SearxngEnricher(transport).enrich(
        SearchConfiguration(),
        market_window="pre_market",
    )

    assert result.items == ()
    assert transport.urls == []


def test_search_enforces_query_result_article_and_byte_bounds() -> None:
    transport = SearchTransport()
    configuration = SearchConfiguration(
        enabled=True,
        endpoint="https://search.example.com",
        query_templates=("market open", "ignored second query"),
        max_queries_per_run=1,
        max_results_per_query=1,
        max_articles_to_parse=1,
        max_article_bytes=128,
    )

    result = SearxngEnricher(transport).enrich(
        configuration,
        market_window="pre_market",
    )

    assert len(result.items) == 1
    assert result.items[0].rank == 1
    assert result.items[0].query_template == "market open"
    assert result.items[0].canonical_url == "https://news.example.com/one"
    assert result.items[0].byte_count <= 128
    assert len(transport.urls) == 2


def test_unsafe_search_endpoint_is_rejected_before_egress() -> None:
    transport = SearchTransport()
    configuration = SearchConfiguration(
        enabled=True,
        endpoint="http://169.254.169.254",
        query_templates=("market open",),
    )

    with pytest.raises(UnsafeOutboundUrl):
        SearxngEnricher(transport).enrich(
            configuration,
            market_window="pre_market",
        )

    assert transport.urls == []
