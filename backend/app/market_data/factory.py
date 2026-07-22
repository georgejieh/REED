"""Factory that returns the right MarketDataProvider for the configured name."""

from __future__ import annotations

import logging

from app.config import AppConfig
from app.market_data.base import MarketDataProvider
from app.market_data.stooq import StooqProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES: dict[str, type[MarketDataProvider]] = {
    "stooq": StooqProvider,
}


def get_market_data_provider(config: AppConfig) -> MarketDataProvider:
    name = config.market_data.provider.value
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown market data provider '{name}'. "
            f"Valid options: {sorted(_PROVIDER_CLASSES)}"
        )
    logger.info("initialising market data provider=%s", name)
    return cls()
