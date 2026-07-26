"""Tool dataclass used by the provider abstraction.

REED's agent loop runs with zero tools exposed: the RSS pre-flight is
the research step, and the model produces the whole brief in a single
turn. This dataclass remains because every provider signature accepts a
tool list (the loop passes an empty one) and because the abstraction
should not have to change if a tool is ever reintroduced.

An earlier version shipped a `scrape_url` tool backed by Firecrawl with
a trafilatura fall back. It was removed: scraping proved unreliable and
was frequently blocked from a free host, which is why the RSS pre-flight
replaced it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A function-callable tool exposed to the model."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    fn: Callable[..., Any]
    parallel_safe: bool = True
