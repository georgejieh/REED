from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.runtime.scheduler import MARKET_WINDOW_SCHEDULES


router = APIRouter(tags=["sessions"])


class SessionPublic(BaseModel):
    id: str
    hour: int
    minute: int
    weekdays: list[int]


@router.get("/api/sessions", response_model=list[SessionPublic])
def list_sessions() -> list[SessionPublic]:
    return [
        SessionPublic(
            id=identifier,
            hour=schedule.hour,
            minute=schedule.minute,
            weekdays=list(schedule.weekdays),
        )
        for identifier, schedule in MARKET_WINDOW_SCHEDULES.items()
    ]
