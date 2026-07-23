"""REED backend entry point.

Use `uv run uvicorn app.main:app` to run the server.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.api import build_app
from app.api.deps import get_store
from app.digests.dataset_mirror import DatasetMirrorStore
from app.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = get_store()
    if isinstance(store, DatasetMirrorStore):
        try:
            store.rehydrate()
        except Exception:
            logger.exception("rehydrate failed during startup")
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = build_app()
app.router.lifespan_context = lifespan
