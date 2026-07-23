"""Read endpoint for the live market snapshot."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_config
from app.config import AppConfig
from app.market_data.factory import get_market_data_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/snapshot", tags=["snapshot"])


@router.get("")
def snapshot(config: AppConfig = Depends(get_config)) -> dict:
    """Return the live market snapshot from the configured provider.

    Re-queries the provider on each call (the snapshot is cheap to
    fetch and intended for the dashboard's "refresh" action).
    """
    try:
        provider = get_market_data_provider(config)
        quotes = provider.fetch_quotes()
    except Exception as exc:
        logger.warning("snapshot fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"snapshot failed: {exc}")

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    values: dict[str, dict[str, str | None | bool]] = {}
    for symbol, quote in quotes.items():
        values[symbol] = {
            "value": quote.value,
            "change_pct": quote.change_pct,
            "as_of": quote.as_of,
            "delayed": quote.delayed,
        }
    return {
        "meta": {
            "source": provider.name,
            "fetched_at": fetched_at,
            "delayed": True,
        },
        "values": values,
    }
