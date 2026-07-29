from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.auth import require_mutation_auth
from app.api.deps import (
    get_pipeline,
    get_repository,
    get_runtime,
    get_settings_store,
)
from app.config.configuration import MarketWindow
from app.config.settings_store import SettingsStore
from app.digests.repository import DigestRepository
from app.runtime.pipeline import PipelineFailure, RuntimePipeline
from app.runtime.scheduler import ClaimHeartbeat
from app.runtime.service import RuntimeService


router = APIRouter(prefix="/api/admin", tags=["administration"])
MutationAuth = Annotated[None, Depends(require_mutation_auth)]


class ManualRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_window: MarketWindow


class ManualRunResponse(BaseModel):
    run_id: str
    status: str
    published_digest_id: str


class CatalogValidationItem(BaseModel):
    source_id: str
    valid: bool
    item_count: int


class CatalogValidationResponse(BaseModel):
    catalog_version: str
    validated_at: str
    valid: bool
    results: list[CatalogValidationItem]


@router.post(
    "/runs",
    response_model=ManualRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def run_manually(
    request: ManualRunRequest,
    _auth: MutationAuth,
    pipeline: RuntimePipeline = Depends(get_pipeline),
    repository: DigestRepository = Depends(get_repository),
    settings_store: SettingsStore = Depends(get_settings_store),
) -> ManualRunResponse:
    configuration = settings_store.load()
    if not configuration.setup_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="runtime configuration is incomplete",
        )
    if request.market_window not in configuration.market_windows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="market window is not enabled",
        )
    requested_at = pipeline.clock()
    claim_ttl = timedelta(
        seconds=configuration.scheduler.claim_ttl_seconds
    )
    run = repository.create_manual_run(
        market_window=request.market_window.value,
        requested_at=requested_at,
        claim_expiry=requested_at + claim_ttl,
    )
    context = repository.get_run_context(run.id)
    owner = context.run_request_id
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="manual run claim is unavailable",
        )
    try:
        with ClaimHeartbeat(
            repository=repository,
            run_id=run.id,
            owner=owner,
            fence_generation=run.fence_generation,
            clock=pipeline.clock,
            claim_ttl=claim_ttl,
        ):
            digest = pipeline.execute(run.id)
    except PipelineFailure as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "run_id": error.run_id,
                "status": "failed",
                "message": error.diagnostic,
            },
        ) from error
    return ManualRunResponse(
        run_id=run.id,
        status="published",
        published_digest_id=digest.id,
    )


@router.post(
    "/rss-catalog/validate",
    response_model=CatalogValidationResponse,
)
def validate_rss_catalog(
    _auth: MutationAuth,
    runtime: RuntimeService = Depends(get_runtime),
) -> dict[str, object]:
    return runtime.validate_source_catalog()
