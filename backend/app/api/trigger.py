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

    digest = generate_digest(
        session=session,
        config=config,
        provider=provider,
        store=store,
        market_snapshot_meta=None,
    )
    return {
        "id": digest.id,
        "headline": digest.headline,
        "session": digest.session,
        "as_of": (digest.as_of or datetime.now(timezone.utc)).isoformat(),
    }
