from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.intake.policy import OutboundUrlPolicy


CATALOG_VERSION = "2026-07-29"


@dataclass(frozen=True)
class RssSource:
    id: str
    name: str
    url: str


@dataclass(frozen=True)
class CatalogValidationResult:
    source_id: str
    valid: bool
    item_count: int


class InvalidSourceSelection(ValueError):
    pass


class SourceCatalog:
    def __init__(
        self,
        version: str = CATALOG_VERSION,
        sources: tuple[RssSource, ...] | None = None,
        policy: OutboundUrlPolicy | None = None,
    ):
        self.version = version
        self.sources = sources or (
            RssSource(
                id="federal-reserve",
                name="Federal Reserve Press Releases",
                url="https://www.federalreserve.gov/feeds/press_all.xml",
            ),
            RssSource(
                id="sec-press-releases",
                name="SEC Press Releases",
                url="https://www.sec.gov/news/pressreleases.rss",
            ),
            RssSource(
                id="bea-news-releases",
                name="Bureau of Economic Analysis News Releases",
                url="https://apps.bea.gov/rss/rss.xml",
            ),
            RssSource(
                id="marketwatch-top-stories",
                name="MarketWatch Top Stories",
                url="https://feeds.content.dowjones.io/public/rss/mw_topstories",
            ),
            RssSource(
                id="cnbc-markets",
                name="CNBC Markets",
                url="https://www.cnbc.com/id/10000664/device/rss/rss.html",
            ),
            RssSource(
                id="yahoo-finance-top-stories",
                name="Yahoo Finance Top Stories",
                url="https://finance.yahoo.com/news/rssindex",
            ),
            RssSource(
                id="nyt-economy",
                name="The New York Times Economy",
                url="https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
            ),
            RssSource(
                id="bbc-business",
                name="BBC News Business",
                url="https://feeds.bbci.co.uk/news/business/rss.xml",
            ),
            RssSource(
                id="bbc-world",
                name="BBC News World",
                url="https://feeds.bbci.co.uk/news/world/rss.xml",
            ),
            RssSource(
                id="guardian-business",
                name="The Guardian Business",
                url="https://www.theguardian.com/business/rss",
            ),
            RssSource(
                id="npr-business",
                name="NPR Business",
                url="https://feeds.npr.org/1017/rss.xml",
            ),
        )
        identifiers = [source.id for source in self.sources]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("source identifiers must be unique")
        url_policy = policy or OutboundUrlPolicy()
        for source in self.sources:
            url_policy.parse(source.url)

    def validate_selection(self, source_ids: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(source_ids)) != len(source_ids):
            raise InvalidSourceSelection("duplicate source selections are not allowed")
        known = {source.id for source in self.sources}
        unknown = sorted(set(source_ids) - known)
        if unknown:
            raise InvalidSourceSelection(
                f"unknown RSS source: {', '.join(unknown)}"
            )
        return source_ids

    def validate_feeds(
        self,
        transport: object,
    ) -> tuple[CatalogValidationResult, ...]:
        from app.intake.rss import (
            MAX_FEED_BYTES,
            REQUEST_TIMEOUT_SECONDS,
            _parse_feed,
        )

        retrieved_at = datetime.now(UTC)
        results: list[CatalogValidationResult] = []
        for source in self.sources:
            try:
                response = transport.request(
                    "GET",
                    source.url,
                    headers={
                        "accept": (
                            "application/rss+xml, application/atom+xml, "
                            "application/xml, text/xml"
                        ),
                        "user-agent": "REED/0.1",
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    max_bytes=MAX_FEED_BYTES,
                    follow_redirects=True,
                )
                parsed = _parse_feed(response, source, retrieved_at)
                item_count = sum(
                    1
                    for item, timestamp_state in parsed
                    if item is not None and timestamp_state == "valid"
                )
                valid = item_count > 0
            except Exception:
                item_count = 0
                valid = False
            results.append(
                CatalogValidationResult(
                    source_id=source.id,
                    valid=valid,
                    item_count=item_count,
                )
            )
        return tuple(results)
