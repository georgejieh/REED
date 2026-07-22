"""Read endpoint for registered sessions."""

from __future__ import annotations

from fastapi import APIRouter

from app.sessions.registry import all_sessions

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
def list_sessions() -> list[dict]:
    """Return the names and time windows of every registered session."""
    return [
        {"name": s.name, "time_window": s.time_window, "topic": s.topic}
        for s in all_sessions()
    ]
