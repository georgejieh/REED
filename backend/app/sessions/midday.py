"""Midday session definition."""

from __future__ import annotations

from app.sessions.registry import SessionDef, register

MIDDAY = SessionDef(
    name="midday",
    topic=(
        "US equity midday update: midday movers, sector rotation, "
        "mid-session economic releases, lunch lull activity."
    ),
    time_window="last 3 hours",
    system_prompt=(
        "You are a market-research agent for REED. Use the search_news "
        "and scrape_url tools to find relevant articles for the user's "
        "topic and time window. Prefer scrapable sources (Yahoo Finance, "
        "CNBC, MarketWatch, AP wire). Output strict JSON matching the "
        "schema in the user message."
    ),
    user_prompt_template=(
        "Topic: {topic}\n"
        "Time window: {time_window}\n"
        "Output JSON matching the digest schema."
    ),
    output_schema={
        "headline": "string",
        "executive_summary": "string",
        "stories": [
            {
                "tickers": ["string"],
                "headline": "string",
                "summary": "string",
                "sentiment": "bullish | bearish | neutral",
                "source_name": "string",
                "source_url": "string",
            }
        ],
        "themes": ["string"],
        "watch_next_session": ["string"],
        "sources": [{"id": "integer", "name": "string", "url": "string"}],
    },
)

register(MIDDAY)
