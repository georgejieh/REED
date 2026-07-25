"""Tools exposed to the model during an agent run.

Each tool is a Tool dataclass with a JSON schema, callable from the
model's function-calling interface. The same list is passed to every
provider (the provider abstraction converts it to the provider's
native tool format).

Per-session counters: the agent loop creates a SessionCounters
and passes it to get_agent_tools. The scrape tool mutates the
counter on each call; once a cap is hit, the tool returns a
structured error so the LLM knows the budget is gone for this
session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import AppConfig
from app.providers.tools import SCRAPE_TOOL, scrape_url, Tool

logger = logging.getLogger(__name__)


@dataclass
class SessionCounters:
    """Per-session tool-call budget. Mutable; the agent tools update in place."""

    max_scrapes: int = 2
    scrapes_used: int = 0

    @property
    def scrapes_remaining(self) -> int:
        return max(0, self.max_scrapes - self.scrapes_used)


def _scrape_url_bounded(url: str, *, counters: SessionCounters) -> dict:
    """Wrapper around scrape_url that enforces the per-session scrape cap.

    Returns a dict (not a ScrapeResult) so the tool schema is
    consistent for the LLM and so the LLM gets a structured error
    on cap exhaustion.
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

    REED's RSS pre-flight provides all the news context the LLM
    needs to synthesize a brief. The agent loop runs with zero
    tools exposed; the model produces the full JSON in a single
    turn. The scrape_url tool is still available via
    `bind_scrape_tool` for operator-driven use from the CLI, but
    it is not exposed to the agent during a normal session.

    `counters` is kept as a parameter for backward compatibility;
    it is unused because no tools mutate it.
    """
    return []


def bind_scrape_tool() -> Tool:
    """Return the scrape tool bound to the existing scrape_url fn."""
    return SCRAPE_TOOL
