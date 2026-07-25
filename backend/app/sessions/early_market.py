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
        "You are a market-research agent for REED. The headlines are pre-fetched and "
        "appear in your context. Use the scrape_url tool to read the full text of a "
        "specific article when the headline alone is not enough; otherwise, synthesize "
        "the brief from the headlines.\n\n"
        "Budget guidance: you have at most {per_session_max_scrapes} scrape_url calls "
        "per session. Spend them on the most decision-relevant articles. If you have "
        "no scrape budget left, synthesize from the headlines alone."
    ),
    user_prompt_template=(
        "Topic: {topic}\n"
        "Time window: {time_window}\n\n"
        "Pre-fetched headlines (use only these URLs; do not invent any):\n"
        "{headlines}\n\n"
        "Output a single JSON object matching this schema.\n"
        "No prose outside the JSON. No markdown fences. No commentary inside the JSON.\n"
        "The first character of your response must be {{ and the last must be }}.\n\n"
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
