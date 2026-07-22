"""Read endpoints for the digest store."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_store
from app.digests.models import Digest
from app.digests.store import DigestStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/digests", tags=["digests"])


@router.get("", response_model=list[Digest])
def list_digests(
    limit: int = 20,
    store: DigestStore = Depends(get_store),
) -> list[Digest]:
    """Return the most recent digests, newest first."""
    return store.list(limit=limit)


@router.get("/{digest_id}", response_model=Digest)
def get_digest(
    digest_id: str,
    store: DigestStore = Depends(get_store),
) -> Digest:
    """Return one digest by id."""
    digest = store.get(digest_id)
    if digest is None:
        raise HTTPException(status_code=404, detail=f"digest {digest_id} not found")
    return digest
