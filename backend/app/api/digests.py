from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_repository
from app.digests.models import PublishedDigest
from app.digests.repository import DigestRepository


router = APIRouter(prefix="/api/digests", tags=["digests"])


@router.get("", response_model=list[PublishedDigest])
def list_digests(
    limit: int = Query(default=20, ge=1, le=100),
    repository: DigestRepository = Depends(get_repository),
) -> list[PublishedDigest]:
    return repository.list_published(limit)


@router.get("/latest", response_model=PublishedDigest)
def get_latest_digest(
    repository: DigestRepository = Depends(get_repository),
) -> PublishedDigest:
    digests = repository.list_published(limit=1)
    if not digests:
        raise HTTPException(status_code=404, detail="digest not found")
    return digests[0]


@router.get("/{digest_id}", response_model=PublishedDigest)
def get_digest(
    digest_id: str,
    repository: DigestRepository = Depends(get_repository),
) -> PublishedDigest:
    digest = repository.get_published(digest_id)
    if digest is None:
        raise HTTPException(status_code=404, detail="digest not found")
    return digest
