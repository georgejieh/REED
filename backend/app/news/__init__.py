"""Public exports for the news package."""

from app.news.brave import BraveProvider
from app.news.ddgs import DdgsProvider
from app.news.factory import get_search_provider
from app.news.pipeline import dedupe_urls, run_pipeline, seed_search
from app.news.search import RatelimitException, SearchProvider, SearchResult, backoff_sleeps
from app.news.tavily import TavilyProvider

__all__ = [
    "BraveProvider",
    "DdgsProvider",
    "RatelimitException",
    "SearchProvider",
    "SearchResult",
    "TavilyProvider",
    "backoff_sleeps",
    "dedupe_urls",
    "get_search_provider",
    "run_pipeline",
    "seed_search",
]
