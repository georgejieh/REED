"""Market data provider abstraction.

Returns the live market snapshot that pre-faces every digest. The
default backend is Stooq, which serves a free CSV endpoint with no
API key. The endpoint is delayed (15 minutes for indices, end-of-day
for Treasury).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Quote:
    """A single market data point with provenance."""

    symbol: str
    value: str
    change_pct: str | None = None
    as_of: str | None = None
    delayed: bool = True


class MarketDataProvider(ABC):
    """Base class for every market data backend."""

    name: str

    DEFAULT_SYMBOLS: tuple[str, ...] = (
        "^SPX",
        "^NDX",
        "^VIX",
        "10USY.B",
        "DX-Y.NYB",
        "CL.NYM",
        "GC.CMX",
    )

    @abstractmethod
    def fetch_quotes(
        self, symbols: Sequence[str] | None = None
    ) -> dict[str, Quote]:
        """Return a symbol-to-Quote map for the requested symbols.

        Symbols absent from the response are simply omitted from the
        returned dict. On any failure, return an empty dict and log.
        """
        ...
