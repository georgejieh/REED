"""Token-protected manual trigger endpoint.

POST /api/trigger/{session} runs generate_digest synchronously and
returns the new digest id. Used by Hugging Face Spaces cron and any
operator who wants to run a session out of schedule.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.deps import get_config, get_store
from app.config import AppConfig
from app.digests.generator import generate_digest
from app.digests.store import DigestStore
from app.providers.factory import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trigger", tags=["trigger"])


def _parse_as_of(as_of: str) -> datetime:
    """Parse an ISO-8601 `as_of` query parameter into an aware UTC datetime.

    Naive timestamps are rejected. Values must include either a 'Z'
    suffix or an explicit UTC offset (e.g. +00:00, -04:00).
    """
    try:
        parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid as_of: {as_of!r}; expected ISO-8601 with timezone (Z or explicit offset)",
        ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise HTTPException(
            status_code=400,
            detail=f"naive as_of: {as_of!r}; include 'Z' or an explicit timezone offset",
        )
    return parsed.astimezone(timezone.utc)


@router.post("/{session}", response_model=dict)
def trigger_session(
    session: str,
    x_reed_token: str | None = Header(default=None),
    config: AppConfig = Depends(get_config),
    as_of: str | None = None,
    store: DigestStore = Depends(get_store),
) -> dict:
    """Run generate_digest for `session` and return the new digest id.

    Auth: when `REED_TRIGGER_TOKEN` is set in the environment, the
    `X-REED-Token` header must match. When unset, the endpoint is
    open only when REED_ENV=dev and we are not on HF (operator override).

    Optional `as_of` query param (ISO-8601 with timezone) anchors the
    time-window RSS filter and the digest's own as_of field. Used for
    backfilling briefs for past dates when RSS feeds still have the
    headlines in their payload. Format: ?as_of=2026-07-23T08:00:00Z
    """
    expected = os.environ.get("REED_TRIGGER_TOKEN")
    # Fail closed when token is unset in prod; allow dev only with REED_ENV=dev.
    if not expected:
        env = os.environ.get("REED_ENV", "prod")
        on_hf = bool(os.environ.get("SPACE_ID"))
        if env != "dev" or on_hf:
            raise HTTPException(status_code=503, detail="trigger token not configured")
    else:
        import hmac
        if not x_reed_token or not hmac.compare_digest(x_reed_token, expected):
            raise HTTPException(status_code=401, detail="missing or invalid token")

    # Parse as_of query param if provided (backfill mode). Do this BEFORE
    # the holiday check so the holiday gate can target the backfill date,
    # not today.
    parsed_as_of = None
    if as_of:
        parsed_as_of = _parse_as_of(as_of)

    # Holiday skip: GHA cron path goes through this trigger, so the gate must
    # live here (not just in scheduler.py) for the HF Space deployment.
    # For backfill (as_of provided), check the backfill date for holidays,
    # not today.
    from datetime import datetime as _dt, timezone as _tz
    from app.market_calendar import is_us_market_holiday
    holiday_anchor = parsed_as_of if parsed_as_of else _dt.now(_tz.utc)
    if config.scheduler.skip_holidays and is_us_market_holiday(holiday_anchor):
        return {
            "id": None,
            "headline": "[STUB] skipped (US market holiday)",
            "session": session,
            "as_of": holiday_anchor.isoformat(),
            "skipped": True,
        }

    try:
        provider = get_provider(config)
    except Exception as exc:
        logger.warning("provider init failed in trigger: %s", exc)
        raise HTTPException(status_code=503, detail=f"provider init failed: {exc}")

    try:
        digest = generate_digest(
            session=session,
            config=config,
            provider=provider,
            store=store,
            market_snapshot_meta=None,
            as_of=parsed_as_of,
        )
    except Exception as exc:
        # The session failed end-to-end (LLM provider error, JSON parse,
        # Pydantic validation, mirror push, etc.). Save a stub digest
        # so the trigger does not 500 and the dataset repo still gets
        # a record. The stub carries fallback_used=True and the
        # original exception in the headline so the operator can see
        # what failed.
        logger.exception("generate_digest failed in trigger; saving stub")
        # Surface the exception class and message to stderr for live debugging.
        import traceback
        print(f"TRIGGER_FAIL: {type(exc).__name__}: {exc}", flush=True)
        print(f"TRIGGER_FAIL_TRACEBACK:\n{traceback.format_exc()}", flush=True)
        from app.digests.models import Digest, MarketSnapshotMeta
        # Build a clean stub: clear stories/sources/themes so the public dataset
        # never contains fabricated content. Exception text lives only in the
        # operator-only warning field and the trigger response log.
        now = datetime.now(timezone.utc)
        warning = f"{type(exc).__name__}: {exc}"[:500]
        digest = Digest(
            session=session,  # type: ignore[arg-type]
            as_of=now,
            headline=f"[STUB] {session} brief unavailable",
            executive_summary=(
                "REED could not generate a structured brief for this session. "
                "The next scheduled trigger will retry."
            ),
            market_snapshot={},
            market_snapshot_meta=MarketSnapshotMeta(
                source="stub",
                fetched_at=now.isoformat(timespec="seconds"),
                values_raw={},
                delayed=True,
            ),
            stories=[],
            themes=[],
            watch_next_session=[],
            sources=[],
            generation={
                "provider": "stub",
                "model": "stub",
                "agent_turns": 0,
                "tool_calls": 0,
                "scraped_urls": 0,
                "fallback_used": True,
                "duration_ms": 0,
                "warning": warning,
            },
        )
        store.write(digest)

    return {
        "id": digest.id,
        "headline": digest.headline,
        "session": digest.session,
        "as_of": (digest.as_of or datetime.now(timezone.utc)).isoformat(),
    }
