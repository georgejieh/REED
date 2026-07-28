"""Read endpoints for the digest store."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_store
from app.digests.models import Digest, Generation, MarketSnapshotMeta, Source, Story
from app.digests.store import DigestStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/digests", tags=["digests"])


class GenerationPublic(BaseModel):
    """Public view of Generation; diagnostic fields are excluded."""

    provider: str
    model: str
    agent_turns: int
    tool_calls: int
    scraped_urls: int
    fallback_used: bool
    duration_ms: int


class DigestPublic(BaseModel):
    """Public view of a Digest for read API responses."""

    id: str | None = None
    session: str
    as_of: datetime
    headline: str
    executive_summary: str
    market_snapshot: dict[str, str]
    market_snapshot_meta: MarketSnapshotMeta
    stories: list[Story] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    watch_next_session: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    generation: GenerationPublic


@router.get("", response_model=list[DigestPublic])
def list_digests(
    limit: int = 20,
    store: DigestStore = Depends(get_store),
) -> list[Digest]:
    """Return the most recent digests, newest first."""
    return store.list(limit=limit)


@router.get("/{digest_id}", response_model=DigestPublic)
def get_digest(
    digest_id: str,
    store: DigestStore = Depends(get_store),
) -> Digest:
    """Return one digest by id."""
    digest = store.get(digest_id)
    if digest is None:
        raise HTTPException(status_code=404, detail=f"digest {digest_id} not found")
    return digest
