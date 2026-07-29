from __future__ import annotations

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.digests import router as digests_router
from app.api.health import router as health_router
from app.api.runtime_status import router as runtime_status_router
from app.api.security import router as security_router
from app.api.sessions import router as sessions_router
from app.api.wizard import router as wizard_router


def include_routes(app: FastAPI) -> None:
    app.include_router(admin_router)
    app.include_router(health_router)
    app.include_router(digests_router)
    app.include_router(runtime_status_router)
    app.include_router(security_router)
    app.include_router(sessions_router)
    app.include_router(wizard_router)


__all__ = ["include_routes"]
