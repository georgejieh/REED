"""REED backend entry point.

Use `uv run uvicorn app.main:app` to run the server.
"""

from contextlib import asynccontextmanager

from app.api import build_app
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app):
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = build_app()
app.router.lifespan_context = lifespan
