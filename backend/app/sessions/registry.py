"""Session registry. Each named session defines a query set, prompt,
and output schema. Sessions are dispatched by name from the CLI,
scheduler, and trigger endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionDef:
    name: str
    topic: str
    time_window: str
    system_prompt: str
    user_prompt_template: str
    output_schema: dict[str, Any] = field(default_factory=dict)


SESSIONS: dict[str, SessionDef] = {}


def register(session: SessionDef) -> None:
    SESSIONS[session.name] = session


def get(name: str) -> SessionDef | None:
    return SESSIONS.get(name)


def all_sessions() -> list[SessionDef]:
    return list(SESSIONS.values())
