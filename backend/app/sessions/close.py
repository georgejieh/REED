"""Market-Close session definition."""

from __future__ import annotations

from app.sessions.registry import SessionDef, register

CLOSE = SessionDef(
    name="close",
    topic=(
        "End-of-day US equity summary: closing prints, biggest winners "
        "and losers, after-hours earnings reactions, summary of the day's "
        "key events."
    ),
    time_window="last 4 hours",
    system_prompt=(
        "You are a market-research agent for REED. The headlines below are pre-fetched "
        "and represent the universe of relevant coverage for this session. Synthesize "
        "a single JSON brief directly from these headlines. Do not invent URLs."
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

register(CLOSE)
