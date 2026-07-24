"""Token-protected manual trigger endpoint.

POST /api/trigger/{session} runs generate_digest synchronously and
returns the new digest id. Used by Hugging Face Spaces cron and any
operator who wants to run a session out of schedule.
"""

from __future__ import annotations

import json
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


@router.post("/{session}", response_model=dict)
def trigger_session(
    session: str,
    x_reed_token: str | None = Header(default=None),
    config: AppConfig = Depends(get_config),
    store: DigestStore = Depends(get_store),
) -> dict:
    """Run generate_digest for `session` and return the new digest id.

    Auth: when `REED_TRIGGER_TOKEN` is set in the environment, the
    `X-REED-Token` header must match. When unset, the endpoint is
    open (intended for local development only).
    """
    expected = os.environ.get("REED_TRIGGER_TOKEN")
    if expected:
        if not x_reed_token or x_reed_token != expected:
            raise HTTPException(status_code=401, detail="missing or invalid token")

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
        )
    except Exception as exc:
        # The session failed end-to-end (LLM provider error, JSON parse,
        # Pydantic validation, mirror push, etc.). Save a stub digest
        # so the trigger does not 500 and the dataset repo still gets
        # a record. The stub carries fallback_used=True and the
        # original exception in the headline so the operator can see
        # what failed.
        logger.exception("generate_digest failed in trigger; saving stub")
        from app.digests.generator import make_stub_provider_result
        from app.digests.models import (
            Digest,
            MarketSnapshotMeta,
            Story,
            Source,
        )
        stub = make_stub_provider_result()
        try:
            payload = json.loads(stub.text)
        except Exception:
            payload = {
                "headline": f"REED session failed: {exc!s}"[:200],
                "executive_summary": (
                    f"REED could not generate a structured brief for {session}. "
                    f"Reason: {exc!s}. The next scheduled trigger will retry."
                ),
                "stories": [],
                "themes": [],
                "watch_next_session": [],
                "sources": [],
            }
        now = datetime.now(timezone.utc)
        digest = Digest(
            session=session,  # type: ignore[arg-type]
            as_of=now,
            headline=payload.get("headline", "Brief generation failed"),
            executive_summary=payload.get("executive_summary", ""),
            market_snapshot={},
            market_snapshot_meta=MarketSnapshotMeta(
                source="stub",
                fetched_at=now.isoformat(timespec="seconds"),
                values_raw={},
                delayed=True,
            ),
            stories=[Story(**s) for s in payload.get("stories", [])],
            themes=payload.get("themes", []),
            watch_next_session=payload.get("watch_next_session", []),
            sources=[Source(**s) for s in payload.get("sources", [])],
            generation={
                "provider": "stub",
                "model": "stub",
                "agent_turns": 0,
                "tool_calls": 0,
                "scraped_urls": 0,
                "fallback_used": True,
                "duration_ms": 0,
                "warning": str(exc),
            },
        )
        store.write(digest)

    return {
        "id": digest.id,
        "headline": digest.headline,
        "session": digest.session,
        "as_of": (digest.as_of or datetime.now(timezone.utc)).isoformat(),
    }
