"""In-process scheduler for the five REED sessions.

Uses APScheduler's BackgroundScheduler with cron triggers in
US/Eastern. US market holidays are skipped when
`scheduler.skip_holidays` is True, via exchange_calendals.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from exchange_calendars import get_calendar

from app.api.deps import get_config, get_store
from app.config import AppConfig
from app.digests.generator import generate_digest
from app.providers.factory import get_provider

logger = logging.getLogger(__name__)

SCHEDULE: dict[str, dict[str, str | int]] = {
    "pre_market": {"hour": 8, "minute": 0, "day_of_week": "mon-fri"},
    "early_market": {"hour": 9, "minute": 45, "day_of_week": "mon-fri"},
    "midday": {"hour": 12, "minute": 30, "day_of_week": "mon-fri"},
    "weekend_recap": {"hour": 7, "minute": 0, "day_of_week": "mon"},
    "close": {"hour": 16, "minute": 15, "day_of_week": "mon-fri"},
}

_NYSE = get_calendar("XNYS")


def _is_holiday(now: datetime) -> bool:
    """Return True if `now` (assumed US/Eastern) is a US market holiday."""
    try:
        day = now.date().isoformat()
        return not _NYSE.is_session(day)
    except Exception as exc:
        logger.warning("holiday check failed: %s", exc)
        return False


def _run_session(session: str) -> None:
    """Scheduler job: run generate_digest for one session."""
    cfg = get_config()
    if not cfg.scheduler.enabled:
        return
    if cfg.scheduler.skip_holidays and _is_holiday(datetime.now()):
        logger.info("skipping %s (US market holiday)", session)
        return

    store = get_store()
    try:
        provider = get_provider(cfg)
    except Exception as exc:
        logger.warning("provider init failed for scheduled run: %s", exc)
        return

    try:
        digest = generate_digest(
            session=session,
            config=cfg,
            provider=provider,
            store=store,
        )
        logger.info("scheduled run %s -> digest %s", session, digest.id)
    except Exception as exc:
        logger.warning("scheduled run %s failed: %s", session, exc)


def _build_scheduler(config: AppConfig) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=config.scheduler.timezone)
    enabled = set(config.sessions.enabled)
    for session, params in SCHEDULE.items():
        if session not in enabled:
            logger.info("session %s not enabled, skipping", session)
            continue
        trigger = CronTrigger(
            hour=int(params["hour"]),
            minute=int(params["minute"]),
            day_of_week=str(params["day_of_week"]),
            timezone=config.scheduler.timezone,
        )
        sched.add_job(
            _run_session,
            trigger=trigger,
            args=[session],
            id=session,
            replace_existing=True,
        )
        logger.info("scheduled %s at %02d:%02d %s", session, int(params["hour"]), int(params["minute"]), str(params["day_of_week"]))
    return sched


_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """Start the in-process scheduler if not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    cfg = get_config()
    if not cfg.scheduler.enabled:
        logger.info("scheduler disabled in config, not starting")
        return
    _scheduler = _build_scheduler(cfg)
    _scheduler.start()
    logger.info("scheduler started with %d jobs", len(_scheduler.get_jobs()))


def stop_scheduler() -> None:
    """Stop the scheduler if running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")
    _scheduler = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
