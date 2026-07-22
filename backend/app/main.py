"""REED backend entry point.

Use `uv run uvicorn app.main:app` to run the server.
"""

from app.api import build_app

app = build_app()
