"""Early-Market session definition."""

from __future__ import annotations

from app.sessions.registry import SessionDef, register

EARLY_MARKET = SessionDef(
    name="early_market",
    topic=(
        "First hour of US equity trading: opening prints, sector "
        "rotation, morning earnings reactions, breaking news during "
        "the cash session."
    ),
    time_window="last 90 minutes",
    system_prompt=(
        "You are a market-research agent for REED. Use the search_news and "
        "scrape_url tools to find relevant articles for the user's topic "
        "and time window. Prefer scrapable sources (Yahoo Finance, CNBC, "
        "MarketWatch, AP wire). Output strict JSON matching the schema "
        "in the user message.\n\n"
        "Budget guidance: you have a small per-session tool budget. Spend "
        "search calls on the most specific queries; prefer scraping URLs "
        "you already have over running new searches. If a tool returns a "
        "budget-exhausted error, synthesize the brief from what you "
        "already have rather than calling the tool again."
    ),
    user_prompt_template=(
        "Topic: {topic}\n"
        "Time window: {time_window}\n\n"
        "Output a single JSON object matching this schema.\n"
        "No prose outside the JSON. No markdown fences.\n"
        "Do not invent URLs; use only URLs returned by the search/scrape tools.\n\n"
        "Schema:\n"
        "```json\n"
        "{schema}\n"
        "```"
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

register(EARLY_MARKET)
