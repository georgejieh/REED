from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IntakeItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    url: str
    source_name: str
    published_at: datetime
    feed_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None
    summary: str = ""
    validation_outcome: str = "valid"


class DigestDraftItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    headline: str
    summary: str
    source_item_id: str


class DigestDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    market_window: str
    title: str
    summary: str
    items: list[DigestDraftItem] = Field(min_length=1)


class PublishedDigestItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    headline: str
    summary: str
    source_name: str
    source_url: str
    published_at: datetime | None = None


class PublishedDigest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source_run_id: str
    market_window: str
    title: str
    summary: str
    published_at: datetime
    items: list[PublishedDigestItem]
