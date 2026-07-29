from __future__ import annotations

from app.digests.models import DigestDraft
from app.intake.models import RssItem, SearchItem
from app.providers.base import Provider
from app.runtime.generation_contract import (
    build_generation_request,
    parse_generated_digest,
)


class DigestGenerator:
    def __init__(self, provider: Provider):
        self.provider = provider

    def generate(
        self,
        *,
        market_window: str,
        rss_items: tuple[RssItem, ...],
        search_items: tuple[SearchItem, ...] = (),
    ) -> DigestDraft:
        if not rss_items:
            raise ValueError("validated RSS evidence is required")
        request = build_generation_request(
            market_window=market_window,
            rss_items=rss_items,
            search_items=search_items,
        )
        content = self.provider.generate(request)
        return parse_generated_digest(
            content,
            market_window=market_window,
            permitted_source_ids={item.id for item in rss_items},
        )
