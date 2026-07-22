"""Stooq market data provider (free CSV, no API key, delayed)."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Sequence
from datetime import datetime, timezone

import httpx

from app.market_data.base import MarketDataProvider, Quote

logger = logging.getLogger(__name__)


class StooqProvider(MarketDataProvider):
    name = "stooq"

    BASE_URL = "https://stooq.com/q/l/"

    def __init__(self, *, timeout: float = 10.0):
        self._timeout = timeout

    def fetch_quotes(
        self, symbols: Sequence[str] | None = None
    ) -> dict[str, Quote]:
        target = list(symbols) if symbols else list(self.DEFAULT_SYMBOLS)
        if not target:
            return {}

        params = {
            "s": ",".join(target),
            "f": "sd2t2ohlcv",
            "h": "",
            "e": "csv",
        }
        try:
            response = httpx.get(
                self.BASE_URL,
                params=params,
                timeout=self._timeout,
                headers={"User-Agent": "REED/0.1 (+https://github.com/georgejieh/REED)"},
            )
        except httpx.HTTPError as exc:
            logger.warning("stooq request failed: %s", exc)
            return {}

        if response.status_code != 200:
            logger.warning("stooq status %s", response.status_code)
            return {}

        try:
            reader = csv.DictReader(io.StringIO(response.text))
        except (csv.Error, ValueError) as exc:
            logger.warning("stooq csv parse failed: %s", exc)
            return {}

        as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
        quotes: dict[str, Quote] = {}
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            if not symbol:
                continue
            try:
                close = (row.get("Close") or "").strip()
                if not close or close == "-":
                    continue
                change_pct = (row.get("Change %") or "").strip() or None
                quotes[symbol] = Quote(
                    symbol=symbol,
                    value=close,
                    change_pct=change_pct,
                    as_of=as_of,
                    delayed=True,
                )
            except Exception as exc:
                logger.debug("skipping stooq row: %s", exc)
        return quotes
