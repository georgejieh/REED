"""Public exports for the sessions package.

Importing this package registers all built-in sessions via their
respective module imports.
"""

from app.sessions.registry import SESSIONS, SessionDef, all_sessions, get, register

# Register built-in sessions.
from app.sessions.close import CLOSE  # noqa: E402
from app.sessions.early_market import EARLY_MARKET  # noqa: E402
from app.sessions.midday import MIDDAY  # noqa: E402
from app.sessions.pre_market import PRE_MARKET  # noqa: E402
from app.sessions.weekend_recap import WEEKEND_RECAP  # noqa: E402

register(CLOSE)
register(EARLY_MARKET)
register(MIDDAY)
register(PRE_MARKET)
register(WEEKEND_RECAP)

__all__ = [
    "SESSIONS",
    "SessionDef",
    "all_sessions",
    "get",
    "register",
]
