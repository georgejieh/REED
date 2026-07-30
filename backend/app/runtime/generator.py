from __future__ import annotations

from app.digests.models import DigestDraft
from app.intake.models import RssItem, SearchItem
from app.providers.base import Provider
from app.runtime.generation_contract import (
    InvalidGeneration,
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
        permitted_source_ids = {item.id for item in rss_items}
        content = self.provider.generate(request)
        try:
            return parse_generated_digest(
                content,
                market_window=market_window,
                permitted_source_ids=permitted_source_ids,
            )
        except InvalidGeneration as first_error:
            # JSON mode is advisory for several compatible providers. Ask once
            # more using the same bounded evidence, selecting the repair
            # instruction only from the safe failure category. Rejected model
            # output is never replayed into the prompt or persisted.
            repair_request = (
                f"{request}\n\n"
                "The previous response was rejected because it did not satisfy "
                "the required JSON contract. Generate a new response now. "
                "Return exactly one JSON object matching output_schema, with "
                "every required field present, source_item_id values taken from "
                "rss_evidence, and no Markdown or commentary. "
                f"Failure category: {first_error.category}."
            )
            repaired_content = self.provider.generate(repair_request)
            return parse_generated_digest(
                repaired_content,
                market_window=market_window,
                permitted_source_ids=permitted_source_ids,
                retry_exhausted=True,
            )
