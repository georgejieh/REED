"""Search provider abstraction shared by all news-search backends.

Concrete backends: DdgsProvider (default, keyless), BraveProvider,
TavilyProvider. Each returns a list of SearchResult with a
title, URL, snippet, and optional source domain. All backends
implement exponential backoff on rate-limit responses.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single news search hit."""

    title: str
    url: str
    snippet: str
    source: str | None = None
    published_at: str | None = None


class RatelimitException(Exception):
    """Raised by a search provider when it has been rate-limited.

    The factory and callers treat this as a retryable failure with
    exponential backoff.
    """


class SearchProvider(ABC):
    """Base class for every news search backend."""

    name: str

    def __init__(self, *, rate_limit_per_minute: int = 12):
        self.rate_limit_per_minute = rate_limit_per_minute

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        timelimit: str = "d",
        max_results: int = 20,
    ) -> list[SearchResult]:
        """Return up to `max_results` hits for `query`.

        `timelimit` is backend-specific. For DDG and Brave, "d" means
        last 24 hours, "w" last week, "m" last month. For Tavily,
        it is a number of days as a string.
        """
        ...


def backoff_sleeps(max_retries: int = 3) -> list[int]:
    """Return the list of seconds to sleep between retries.

    5s, 15s, 45s for max_retries=3.
    """
    base = 5
    return [base * (3 ** i) for i in range(max_retries)]
