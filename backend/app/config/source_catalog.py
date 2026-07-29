from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.intake.policy import OutboundUrlPolicy


CATALOG_VERSION = "2026-07-28"


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
