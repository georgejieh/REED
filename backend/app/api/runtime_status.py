from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_repository, get_runtime
from app.digests.repository import DigestRepository
from app.digests.run import RunStatus
from app.runtime.scheduler import LeaseStatus
from app.runtime.service import RuntimeService


router = APIRouter(tags=["runtime"])


class RunStatusPublic(BaseModel):
    id: str
    status: RunStatus


class RuntimeStatusResponse(BaseModel):
    scheduler_active: bool
    scheduler_leader: bool
    scheduler_lease: LeaseStatus
    latest_run: RunStatusPublic | None


@router.get("/api/runtime/status", response_model=RuntimeStatusResponse)
@router.get(
    "/api/runtime-status",
    response_model=RuntimeStatusResponse,
    include_in_schema=False,
)
def runtime_status(
    repository: DigestRepository = Depends(get_repository),
    runtime: RuntimeService = Depends(get_runtime),
) -> RuntimeStatusResponse:
    run = repository.latest_run()
    scheduler = runtime.scheduler.status
    if run is None:
        return RuntimeStatusResponse(
            scheduler_active=scheduler.active,
            scheduler_leader=scheduler.leader,
            scheduler_lease=scheduler.lease_status,
            latest_run=None,
        )
    return RuntimeStatusResponse(
        scheduler_active=scheduler.active,
        scheduler_leader=scheduler.leader,
        scheduler_lease=scheduler.lease_status,
        latest_run=RunStatusPublic(
            id=run.id,
            status=run.status,
        )
    )
