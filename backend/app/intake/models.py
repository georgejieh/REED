from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RssItem:
    id: str
    feed_id: str
    outlet: str
    title: str
    canonical_url: str
    published_at: datetime
    retrieved_at: datetime
    source_url: str
    summary: str
    validation_outcome: str = "valid"


@dataclass(frozen=True)
class SourceOutcome:
    source_id: str
    source_url: str
    retrieved_at: datetime
    state: str
    item_count: int
    diagnostic: str | None = None


@dataclass(frozen=True)
class RssIntakeResult:
    items: tuple[RssItem, ...]
    source_outcomes: tuple[SourceOutcome, ...]
    state: str


@dataclass(frozen=True)
class SearchItem:
    query_template: str
    rank: int
    canonical_url: str
    title: str
    content: str
    parser_outcome: str
    byte_count: int


@dataclass(frozen=True)
class SearchResult:
    items: tuple[SearchItem, ...]
    state: str
