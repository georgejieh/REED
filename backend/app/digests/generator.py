"""Digest generator. Stub for now; Chunk 2b adds the agent runner.

This module's job: take a session name and an optional pre-fetched
market snapshot, run the provider with the session's prompt template
and JSON-mode enabled, parse the response into a Digest, and write it
via the store.

For now the provider call is a deterministic stub that returns a
fixture-shaped JSON string, so the pipeline can be tested end-to-end
without keys.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.config import AppConfig
from app.digests.models import Digest, Generation, MarketSnapshotMeta, Story
from app.digests.store import DigestStore
from app.providers.base import LLMProvider, ProviderResult

logger = logging.getLogger(__name__)


def make_stub_provider_result() -> ProviderResult:
    """Return a deterministic stub result used when no real provider is wired.

    Chunk 2b replaces this with the real agent-runner-driven result.
    """
    return ProviderResult(
        text=json.dumps(_STUB_DIGEST_PAYLOAD),
        tool_calls=[],
        usage={},
        raw=None,
    )


_STUB_DIGEST_PAYLOAD: dict = {
    "headline": "Stub digest for pipeline smoke test",
    "executive_summary": "This digest is produced by the chunk 0c stub.",
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


def generate_digest(
    *,
    session: str,
    config: AppConfig,
    provider: LLMProvider | None,
    store: DigestStore,
    market_snapshot: dict[str, str],
    market_snapshot_meta: MarketSnapshotMeta,
) -> Digest:
    """Generate and persist a digest for the named session.

    The provider is invoked with json_mode=True. The response text is
    parsed as JSON and merged with the pre-fetched market snapshot and
    generation metadata.
    """
    if provider is None:
        result = make_stub_provider_result()
        logger.info("using stub provider result (no provider wired)")
    else:
        result = provider.generate(
            system_prompt="stub system prompt",
            user_prompt="stub user prompt",
            json_mode=True,
        )

    payload = json.loads(result.text)
    as_of = datetime.now(timezone.utc)
    digest = Digest(
        session=session,  # type: ignore[arg-type]
        as_of=as_of,
        headline=payload["headline"],
        executive_summary=payload["executive_summary"],
        market_snapshot=market_snapshot,
        market_snapshot_meta=market_snapshot_meta,
        stories=[Story(**s) for s in payload.get("stories", [])],
        themes=payload.get("themes", []),
        watch_next_session=payload.get("watch_next_session", []),
        sources=payload.get("sources", []),
        generation=Generation(
            provider=config.provider.value,
            model=config.model,
            agent_turns=1,
            tool_calls=0,
            scraped_urls=0,
            fallback_used=provider is None,
            duration_ms=0,
        ),
    )
    store.write(digest)
    logger.info("generated digest %s for session %s", digest.id, session)
    return digest
