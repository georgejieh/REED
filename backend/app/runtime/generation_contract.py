from __future__ import annotations

import json
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

    @field_validator("headline", "summary", "source_item_id")
    @classmethod
    def reject_blank_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("generated digest fields must not be blank")
        return stripped


class GeneratedDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    items: list[GeneratedItem] = Field(min_length=1, max_length=50)

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
            "Use only RSS evidence ids in source_item_id fields.",
            "Return one JSON object and no extra prose.",
            "Do not create queries, URLs, citations, tool calls, or external claims.",
        ],
        "output_schema": {
            "title": "nonempty string",
            "summary": "nonempty string",
            "items": [
                {
                    "headline": "nonempty string",
                    "summary": "nonempty string",
                    "source_item_id": "id from rss_evidence",
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
            )
            for item in generated.items
        ],
    )
