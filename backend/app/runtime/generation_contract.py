from __future__ import annotations

import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from app.digests.models import DigestDraft, DigestDraftItem
from app.intake.models import RssItem, SearchItem


class GeneratedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    source_item_id: str = Field(min_length=1, max_length=500)
    market_sentiment: Literal["bullish", "bearish", "mixed", "neutral"]
    market_relevance: str = Field(min_length=1, max_length=500)
    tickers: list[str] = Field(max_length=8)

    @field_validator("headline", "summary", "source_item_id", "market_relevance")
    @classmethod
    def reject_blank_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("generated digest fields must not be blank")
        return stripped

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if any(
            not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", value)
            for value in normalized
        ):
            raise ValueError("ticker symbols must be concise exchange-style labels")
        if len(set(normalized)) != len(normalized):
            raise ValueError("ticker symbols must be unique")
        return normalized


class GeneratedDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    items: list[GeneratedItem] = Field(min_length=1, max_length=10)

    @field_validator("title", "summary")
    @classmethod
    def reject_blank_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("generated digest fields must not be blank")
        return stripped


class InvalidGeneration(RuntimeError):
    pass


def build_generation_request(
    *,
    market_window: str,
    rss_items: tuple[RssItem, ...],
    search_items: tuple[SearchItem, ...] = (),
) -> str:
    evidence = [
        {
            "id": item.id,
            "source_type": "rss",
            "outlet": item.outlet,
            "title": item.title,
            "url": item.canonical_url,
            "published_at": item.published_at.isoformat(),
            "summary": item.summary,
        }
        for item in rss_items
    ]
    supplemental = [
        {
            "source_type": "supplemental_search",
            "query_template": item.query_template,
            "rank": item.rank,
            "url": item.canonical_url,
            "title": item.title,
            "content": item.content,
        }
        for item in search_items
    ]
    contract = {
        "market_window": market_window,
        "instructions": [
            "Use supplied validated intake items as the only evidence.",
            "Return a concise market brief, not a feed recap.",
            "Write summary as a 2-4 sentence executive thesis explaining the dominant cross-market themes and uncertainty visible in the selected evidence.",
            "Select at most ten items with a material market, sector, rates, commodity, policy, trade, or company-impact transmission path.",
            "Omit lifestyle, local, celebrity, crime, weather, and human-interest items unless the supplied evidence establishes a material market impact.",
            "Retain geopolitics only when the supplied evidence supports a connection to energy, trade, supply chains, currencies, rates, sanctions, or listed companies.",
            "Use only RSS evidence ids in source_item_id fields.",
            "Set market_sentiment to bullish, bearish, mixed, or neutral for the likely near-term market implication, not as investment advice.",
            "Use market_relevance to state the evidence-grounded transmission path in one concise sentence.",
            "Include ticker symbols only for clearly relevant listed companies; exchange suffixes and class shares such as BRK.B or RY.TO are allowed, otherwise return an empty tickers list.",
            "Return one JSON object and no extra prose.",
            "Do not create queries, URLs, citations, tool calls, price targets, trade recommendations, or external claims.",
        ],
        "output_schema": {
            "title": "nonempty string",
            "summary": "nonempty string",
            "items": [
                {
                    "headline": "nonempty string",
                    "summary": "nonempty string",
                    "source_item_id": "id from rss_evidence",
                    "market_sentiment": "bullish|bearish|mixed|neutral",
                    "market_relevance": "nonempty evidence-grounded string",
                    "tickers": ["relevant listed-company ticker symbols, otherwise empty"],
                }
            ],
        },
        "rss_evidence": evidence,
        "supplemental_context": supplemental,
    }
    return json.dumps(contract, separators=(",", ":"), ensure_ascii=True)


def parse_generated_digest(
    content: str,
    *,
    market_window: str,
    permitted_source_ids: set[str],
) -> DigestDraft:
    if not content.strip():
        raise InvalidGeneration("generation content is empty")
    try:
        payload = json.loads(content)
        generated = GeneratedDigest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as error:
        raise InvalidGeneration("generation content is malformed") from error
    referenced = {item.source_item_id for item in generated.items}
    if not referenced.issubset(permitted_source_ids):
        raise InvalidGeneration(
            "generation references intake evidence that was not supplied"
        )
    return DigestDraft(
        id=str(uuid4()),
        market_window=market_window,
        title=generated.title,
        summary=generated.summary,
        items=[
            DigestDraftItem(
                headline=item.headline,
                summary=item.summary,
                source_item_id=item.source_item_id,
                market_sentiment=item.market_sentiment,
                market_relevance=item.market_relevance,
                tickers=item.tickers,
            )
            for item in generated.items
        ],
    )
