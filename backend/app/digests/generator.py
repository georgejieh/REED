"""Digest generator.

Orchestrates the full pipeline for one session: lookup the SessionDef,
fetch a market snapshot, run the agent loop with the session's prompt
templates, parse the agent's JSON output into a Digest, and persist
the digest via the configured DigestStore.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.agents.runner import run_agent
from app.agents.tools import SessionCounters, get_agent_tools
from app.config import AppConfig
from app.digests.models import Digest, Generation, MarketSnapshotMeta, Source, Story
from app.digests.store import DigestStore
from app.market_data.factory import get_market_data_provider
from app.market_data.base import Quote
from app.providers.base import LLMProvider, ProviderResult
from app.sessions.registry import all_sessions

logger = logging.getLogger(__name__)


_STUB_DIGEST_PAYLOAD: dict = {
    "headline": "Stub digest for pipeline smoke test",
    "executive_summary": "This digest is produced by the placeholder generator.",
    "stories": [
        {
            "tickers": ["SPY"],
            "headline": "Markets flat in pre-market",
            "summary": "S&P 500 futures are unchanged ahead of the open.",
            "sentiment": "neutral",
            "source_name": "Reuters",
            "source_url": "https://example.com/reuters",
        }
    ],
    "themes": ["pre-market"],
    "watch_next_session": ["CPI release at 8:30 ET"],
    "sources": [{"id": 1, "name": "Reuters", "url": "https://example.com/reuters"}],
}


def make_stub_provider_result() -> ProviderResult:
    """Return a deterministic stub result used when no real provider is wired."""
    return ProviderResult(
        text=json.dumps(_STUB_DIGEST_PAYLOAD),
        tool_calls=[],
        usage={},
        raw=None,
    )


def _snapshot_to_dict(quotes: dict[str, Quote]) -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    for symbol, quote in quotes.items():
        out[symbol] = {
            "value": quote.value,
            "change_pct": quote.change_pct,
            "as_of": quote.as_of,
        }
    return out


def _merge_payload(
    payload: dict,
    snapshot: dict[str, dict[str, str | None]],
) -> dict:
    """Return payload with the live snapshot merged in under market_snapshot."""
    merged = dict(payload)
    merged["market_snapshot"] = {k: v["value"] for k, v in snapshot.items()}
    return merged


def _parse_payload(
    payload: dict | None,
    snapshot: dict[str, dict[str, str | None]],
) -> dict:
    """Validate, coerce, and merge the live market snapshot into the payload.

    Returns a minimal stub digest when `payload` is None or not a
    dict. The agent loop already emits a fallback digest when its
    LLM call fails to produce parseable JSON, so the `None` path
    here only protects against future refactors.
    """
    if not isinstance(payload, dict):
        return {
            "headline": "Brief generation failed",
            "executive_summary": (
                "REED could not generate a structured brief: "
                "agent payload was not a JSON object."
            ),
            "market_snapshot": {k: v["value"] for k, v in snapshot.items()},
            "stories": [],
            "themes": [],
            "watch_next_session": [],
            "sources": [],
        }
    return _merge_payload(payload, snapshot)


def generate_digest(
    *,
    session: str,
    config: AppConfig,
    provider: LLMProvider | None,
    store: DigestStore,
    market_snapshot: dict[str, str] | None = None,
    market_snapshot_meta: MarketSnapshotMeta | None = None,
) -> Digest:
    """Generate and persist a digest for the named session.

    When `provider` is None, uses the stub pipeline for smoke tests.
    Otherwise fetches a market snapshot, runs the agent, parses the
    result, and writes the digest.
    """
    names_to_defs = {s.name: s for s in all_sessions()}
    if session not in names_to_defs:
        raise ValueError(f"unknown session {session!r}")
    session_def = names_to_defs[session]

    if provider is None:
        result = make_stub_provider_result()
        logger.info("using stub provider result (no provider wired)")
        payload = json.loads(result.text)
        turns = 1
        tool_call_count = 0
        scraped_url_count = 0
        fallback_used = True
        snapshot_quotes: dict[str, Quote] = {}
        warning: str | None = None
        duration_ms = 0
    else:
        market_provider = get_market_data_provider(config)
        snapshot_quotes = market_provider.fetch_quotes()
        snapshot_dict = _snapshot_to_dict(snapshot_quotes)

        counters = SessionCounters(
            max_searches=config.search.per_session_max_queries,
            max_scrapes=config.search.per_session_max_scrapes,
        )
        tools = get_agent_tools(config, counters)
        system_prompt = session_def.system_prompt
        schema_block = json.dumps(session_def.output_schema, indent=2)
        user_prompt = session_def.user_prompt_template.format(
            topic=session_def.topic,
            time_window=session_def.time_window,
            schema=schema_block,
        )

        agent_result = run_agent(
            provider=provider,
            tools=tools,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            market_snapshot=snapshot_dict,
            max_turns=3,
            json_mode=True,
        )
        if agent_result.parsed_json is None:
            # The LLM returned text that is not parseable JSON. Synthesize
            # a minimal valid digest from the raw text so the trigger
            # does not 500 and the cron does not fail closed. The brief
            # is marked fallback_used=True and the warning is exposed in
            # generation.warning so the operator can see it in the API.
            logger.warning(
                "agent returned no parseable JSON; emitting fallback digest: %s",
                agent_result.warning,
            )
            fallback_used = True
            warning = agent_result.warning or "agent returned no parseable JSON"
            text = (agent_result.final_text or "").strip()
            payload = {
                "headline": text[:200] if text else "Brief generation failed",
                "executive_summary": (
                    f"REED could not generate a structured brief for {session}. "
                    f"Reason: {warning}. Run a manual trigger or check the LLM provider."
                ),
                "market_snapshot": {},
                "stories": [],
                "themes": [],
                "watch_next_session": [],
                "sources": [],
            }
        else:
            payload = _parse_payload(agent_result.parsed_json, snapshot_dict)
        turns = agent_result.turns
        tool_call_count = len(agent_result.tool_calls)
        scraped_url_count = sum(
            1
            for tc in agent_result.tool_calls
            if tc.get("name") == "scrape_url"
        )
        fallback_used = agent_result.fallback_used
        warning = agent_result.warning
        duration_ms = agent_result.duration_ms

    as_of = datetime.now(timezone.utc)
    meta = market_snapshot_meta or MarketSnapshotMeta(
        source="stooq" if snapshot_quotes else "stub",
        fetched_at=as_of.isoformat(timespec="seconds"),
        values_raw={},
        delayed=True,
    )

    stories = [Story(**s) for s in payload.get("stories", [])]
    sources = [Source(**s) for s in payload.get("sources", [])]

    digest = Digest(
        session=session,  # type: ignore[arg-type]
        as_of=as_of,
        headline=payload.get("headline", ""),
        executive_summary=payload.get("executive_summary", ""),
        market_snapshot=payload.get("market_snapshot", market_snapshot or {}),
        market_snapshot_meta=meta,
        stories=stories,
        themes=payload.get("themes", []),
        watch_next_session=payload.get("watch_next_session", []),
        sources=sources,
        generation=Generation(
            provider=config.provider.value,
            model=config.model,
            agent_turns=turns,
            tool_calls=tool_call_count,
            scraped_urls=scraped_url_count,
            fallback_used=fallback_used,
            duration_ms=duration_ms,
        ),
    )
    store.write(digest)
    logger.info(
        "generated digest %s for session %s (warning=%s)",
        digest.id,
        session,
        warning,
    )
    return digest
