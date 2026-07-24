"""Tools exposed to the model during an agent run.

Each tool is a Tool dataclass with a JSON schema, callable from the
model's function-calling interface. The same list is passed to every
provider (the provider abstraction converts it to the provider's
native tool format).

Per-session counters: the agent loop creates a SessionCounters
and passes it to get_agent_tools. The search and scrape tools
mutate the counter on each call; once a cap is hit, the tool
returns a structured error so the LLM knows the budget is gone
for this session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import AppConfig
from app.news.factory import get_search_provider
from app.providers.tools import SCRAPE_TOOL, scrape_url, Tool

logger = logging.getLogger(__name__)


@dataclass
class SessionCounters:
    """Per-session tool-call budget. Mutable; the agent tools update in place."""

    max_searches: int = 3
    max_scrapes: int = 2
    searches_used: int = 0
    scrapes_used: int = 0

    @property
    def searches_remaining(self) -> int:
        return max(0, self.max_searches - self.searches_used)

    @property
    def scrapes_remaining(self) -> int:
        return max(0, self.max_scrapes - self.scrapes_used)


def _search_news(
    query: str,
    *,
    timelimit: str = "d",
    max_results: int = 10,
    config: AppConfig,
    counters: SessionCounters,
) -> dict:
    """Search the web for recent news matching `query`."""
    if counters.searches_remaining <= 0:
        return {
            "results": [],
            "error": (
                f"search budget exhausted for this session "
                f"({counters.max_searches} used). Use the URLs you "
                f"already have or call scrape_url on them."
            ),
        }
    counters.searches_used += 1
    try:
        provider = get_search_provider(config)
    except Exception as exc:
        logger.warning("search provider init failed: %s", exc)
        return {"results": [], "error": f"search init failed: {exc}"}
    try:
        hits = provider.search(
            query, timelimit=timelimit, max_results=max_results
        )
    except Exception as exc:
        logger.warning("search failed for %r: %s", query, exc)
        return {"results": [], "error": str(exc)}
    return {
        "results": [
            {
                "title": hit.title,
                "url": hit.url,
                "snippet": hit.snippet,
                "source": hit.source,
                "published_at": hit.published_at,
            }
            for hit in hits
        ]
    }


SEARCH_NEWS_TOOL = Tool(
    name="search_news",
    description=(
        "Search the web for recent news matching a free-form query. "
        "Returns up to max_results hits with title, URL, snippet, and "
        "source. Use site: filters to bias toward specific outlets. "
        "Subject to a per-session search budget; calls beyond the "
        "budget return an error."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query. Use site: filters to bias by outlet.",
            },
            "timelimit": {
                "type": "string",
                "enum": ["d", "w", "m"],
                "description": "How recent: d = 24 hours, w = 7 days, m = 30 days.",
            },
            "max_results": {
                "type": "integer",
                "description": "Cap on the number of results returned.",
            },
        },
        "required": ["query"],
    },
    fn=_search_news,
)


def _scrape_url_bounded(url: str, *, counters: SessionCounters) -> dict:
    """Wrapper around scrape_url that enforces the per-session scrape cap.

    Returns a dict (not a ScrapeResult) so the tool schema is
    consistent with the search tool and so the LLM gets a
    structured error on cap exhaustion.
    """
    if counters.scrapes_remaining <= 0:
        return {
            "url": url,
            "ok": False,
            "error": (
                f"scrape budget exhausted for this session "
                f"({counters.max_scrapes} used). Synthesize the brief "
                f"from what you already have."
            ),
        }
    counters.scrapes_used += 1
    result = scrape_url(url)
    return {
        "url": result.url,
        "ok": result.ok,
        "text": result.text,
        "error": result.error,
    }


def get_agent_tools(
    config: AppConfig,
    counters: SessionCounters | None = None,
) -> list:
    """Return the list of tools the model gets during an agent run.

    If `counters` is provided, the search and scrape tools mutate
    it on each call and refuse calls beyond the configured caps.
    Pass a fresh SessionCounters per session.
    """
    if counters is None:
        counters = SessionCounters(
            max_searches=config.search.per_session_max_queries,
            max_scrapes=config.search.per_session_max_scrapes,
        )

    bound_search = Tool(
        name="search_news",
        description=SEARCH_NEWS_TOOL.description,
        parameters_schema=SEARCH_NEWS_TOOL.parameters_schema,
        fn=lambda *args, _config=config, _counters=counters, **kwargs: _search_news(
            *args, config=_config, counters=_counters, **kwargs
        ),
        parallel_safe=True,
    )
    bound_scrape = Tool(
        name="scrape_url",
        description=SCRAPE_TOOL.description,
        parameters_schema=SCRAPE_TOOL.parameters_schema,
        fn=lambda *args, _counters=counters, **kwargs: _scrape_url_bounded(
            *args, counters=_counters, **kwargs
        ),
        parallel_safe=False,
    )
    return [bound_search, bound_scrape]


def bind_scrape_tool() -> Tool:
    """Return the scrape tool bound to the existing scrape_url fn."""
    return SCRAPE_TOOL
