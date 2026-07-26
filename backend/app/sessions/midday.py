"""Midday session definition."""

from __future__ import annotations

from app.sessions.registry import SessionDef, register

MIDDAY = SessionDef(
    name="midday",
    topic=(
        "US equity midday update: midday movers, sector rotation, "
        "mid-session economic releases, lunch lull activity."
    ),
    time_window="America/New_York 09:45 through 12:30",
    system_prompt=(
        "You are a market-research agent for REED. The headlines below are pre-fetched "
        "and represent the universe of relevant coverage for this session. Synthesize "
        "a single JSON brief directly from these headlines. Do not invent URLs."
    ),
    user_prompt_template=(
        "Topic: {topic}\n"
        "Time window: {time_window}\n\n"
        "Pre-fetched headlines within the time window (use only these URLs; do not invent any):\n"
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

register(MIDDAY)
