"""Tools exposed to the model during an agent run.

Each tool is a Tool dataclass with a JSON schema, callable from the
model's function-calling interface. The same list is passed to every
provider (the provider abstraction converts it to the provider's
native tool format).
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import AppConfig
from app.news.factory import get_search_provider
from app.providers.tools import SCRAPE_TOOL, Tool

logger = logging.getLogger(__name__)


def _search_news(
    query: str,
    *,
    timelimit: str = "d",
    max_results: int = 10,
    config: AppConfig,
) -> dict[str, Any]:
    """Search the web for recent news matching `query`."""
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
        "source. Use site: filters to bias toward specific outlets."
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


def get_agent_tools(
    config: AppConfig,
) -> list[Tool]:
    """Return the list of tools the model gets during an agent run."""
    tools: list[Tool] = [SEARCH_NEWS_TOOL, SCRAPE_TOOL]

    bound_search = Tool(
        name="search_news",
        description=SEARCH_NEWS_TOOL.description,
        parameters_schema=SEARCH_NEWS_TOOL.parameters_schema,
        fn=lambda *args, _config=config, **kwargs: _search_news(
            *args, config=_config, **kwargs
        ),
        parallel_safe=True,
    )
    tools[0] = bound_search
    return tools


def bind_scrape_tool() -> Tool:
    """Return the scrape tool bound to the existing scrape_url fn."""
    return SCRAPE_TOOL
