"""Public exports for the sessions package.

Importing this package registers all built-in sessions via their
respective module imports.
"""

from app.sessions.registry import SESSIONS, SessionDef, all_sessions, get, register

# Register built-in sessions.
from app.sessions.pre_market import PRE_MARKET  # noqa: E402

register(PRE_MARKET)

__all__ = [
    "SESSIONS",
    "SessionDef",
    "all_sessions",
    "get",
    "register",
]
