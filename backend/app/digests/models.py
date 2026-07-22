"""Digest data models.

The Digest is the contract between the agent runner and every consumer
(read API, dashboard, dataset mirror). The shape mirrors the example
in data/samples/example-digest.json.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


Sentiment = Literal["bullish", "bearish", "neutral"]
SessionName = Literal["pre_market", "early_market", "midday", "close", "weekend_recap"]


class TickerMention(BaseModel):
    """A ticker referenced in a story."""

    symbol: str
    exchange: str | None = None


class Source(BaseModel):
    """A numbered source citation."""

    id: int
    name: str
    url: str


class Story(BaseModel):
    """A single news story inside a digest."""

    tickers: list[str] = Field(default_factory=list)
    headline: str
    summary: str
    sentiment: Sentiment
    source_name: str
    source_url: str


class MarketSnapshotValue(BaseModel):
    """A single market data point with provenance."""

    value: str
    change_pct: str | None = None
    as_of: str | None = None
    delayed: bool = True


class MarketSnapshotMeta(BaseModel):
    """Provenance block for the market snapshot."""

    source: str
    fetched_at: str
    values_raw: dict[str, MarketSnapshotValue]
    delayed: bool = True


class Generation(BaseModel):
    """Runtime metadata about how the digest was produced."""

    provider: str
    model: str
    agent_turns: int
    tool_calls: int
    scraped_urls: int
    fallback_used: bool
    duration_ms: int


class Digest(BaseModel):
    """A complete market digest for one session."""

    id: str | None = None
    session: SessionName
    as_of: datetime
    headline: str
    executive_summary: str
    market_snapshot: dict[str, str]
    market_snapshot_meta: MarketSnapshotMeta
    stories: list[Story] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    watch_next_session: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    generation: Generation

    @field_validator("session", mode="before")
    @classmethod
    def _normalise_session(cls, v: str) -> str:
        if isinstance(v, str):
            return v.lower().replace("-", "_").replace(" ", "_")
        return v
