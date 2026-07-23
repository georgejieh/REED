"""API layer entry point."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.digests import router as digests_router
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.api.snapshot import router as snapshot_router
from app.api.trigger import router as trigger_router


def build_app() -> FastAPI:
    app = FastAPI(title="REED", version="0.1.0")
    app.include_router(health_router)
    app.include_router(digests_router)
    app.include_router(sessions_router)
    app.include_router(snapshot_router)
    app.include_router(trigger_router)
    return app


__all__ = ["build_app"]
