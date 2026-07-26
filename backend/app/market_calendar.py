"""Shared US market holiday calendar for REED.

Used by both the in-process scheduler (scheduler.py) and the HTTP trigger
(trigger.py) so the holiday-skip behavior is consistent across both firing
paths. The GHA cron path on HF Space goes through the HTTP trigger, so the
holiday gate must live here, not in scheduler.py only.
"""

from __future__ import annotations

import logging
from datetime import datetime

from exchange_calendars import get_calendar

logger = logging.getLogger(__name__)

_NYSE = get_calendar("XNYS")


def is_us_market_holiday(now: datetime) -> bool:
    """Return True if `now` (assumed US/Eastern or UTC; date is what matters) is a US market holiday.

    US market holidays are days when NYSE is closed. Pre-market and post-market
    sessions on half-days are treated as open days (NYSE.is_session is True).
    """
    try:
        day = now.date().isoformat()
        return not _NYSE.is_session(day)
    except Exception as exc:
        logger.warning("holiday check failed: %s", exc)
        return False
