"""Tool dataclass and the built-in scrape_url tool used by REED agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    """A function-callable tool exposed to the model."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    fn: Callable[..., Any]
    parallel_safe: bool = True


@dataclass
class ScrapeResult:
    url: str
    text: str
    ok: bool
    error: str | None = None


def scrape_url(url: str, *, timeout: float = 4.0) -> ScrapeResult:
    """Placeholder scrape tool that always returns an error.

    Replaced by a real httpx + trafilatura implementation in a later
    release of REED.
    """
    return ScrapeResult(url=url, text="", ok=False, error="scraper not yet implemented")


SCRAPE_TOOL = Tool(
    name="scrape_url",
    description="Fetch the full text of a URL. Returns the article text or an error.",
    parameters_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Absolute URL to fetch"}},
        "required": ["url"],
    },
    fn=scrape_url,
)
