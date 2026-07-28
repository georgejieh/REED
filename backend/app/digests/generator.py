"""Digest generator.

Orchestrates the full pipeline for one session: lookup the SessionDef,
fetch a market snapshot, fetch a pre-flight set of RSS headlines,
run the agent loop with the session's prompt templates, parse the
agent's JSON output into a Digest, and persist the digest via the
configured DigestStore.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.agents.runner import run_agent
from app.agents.tools import get_agent_tools
from app.config import AppConfig
from app.digests.models import Digest, Generation, MarketSnapshotMeta, Source, Story
from app.digests.redaction import redact_warning
from app.digests.store import DigestStore
from app.market_data.factory import get_market_data_provider
from app.market_data.base import Quote
from app.news.rss import fetch_headlines, render_for_prompt
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


def _normalize_url(url: str) -> str:
    """Normalize a URL for comparison: strip query, fragment, trailing slash."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.rstrip("/") if parts.path else "", "", "")).lower()


def _coerce_payload(payload: dict, allowed_links: set[str] | None = None) -> dict:
    """Coerce null/missing fields to defaults so the Story/Source models accept them.

    The LLM occasionally emits `null` for fields it cannot fill (summary,
    sentiment). Pydantic rejects None for required strings, so we replace
    null with sensible defaults here. Lists default to empty.

    If `allowed_links` is provided, any story whose `source_url` is not
    in that set is dropped. This prevents the LLM from hallucinating URLs
    that look like the configured feeds but were not actually pre-fetched.
    """
    coerced = dict(payload)

    # Story fields: coerce null/empty to defaults.
    story_defaults = {
        "summary": "",
        "headline": "",
        "tickers": [],
        "sentiment": "neutral",
        "source_name": "",
        "source_url": "",
    }
    for s in coerced.get("stories", []) or []:
        if not isinstance(s, dict):
            continue
        for k, default in story_defaults.items():
            if s.get(k) is None:
                s[k] = default
        if not isinstance(s.get("tickers"), list):
            s["tickers"] = []
        # Normalize sentiment to one of the Literal values; anything else becomes "neutral".
        if s.get("sentiment") not in ("bullish", "bearish", "neutral"):
            s["sentiment"] = "neutral"
        # Drop stories with no source_url; they are useless and may be hallucinated.
        if not s.get("source_url"):
            h = s.get("headline")
            logger.warning("dropping story with no source_url: %r", str(h)[:80] if h is not None else "")

    coerced["stories"] = [
        s for s in coerced.get("stories", []) or []
        if isinstance(s, dict) and s.get("source_url")
    ]

    # If we have an allowed set, drop any story whose URL doesn't match a
    # pre-fetched headline. This catches LLM-hallucinated URLs.
    if allowed_links:
        before = len(coerced["stories"])
        coerced["stories"] = [
            s for s in coerced["stories"]
            if _normalize_url(s.get("source_url", "")) in allowed_links
        ]
        dropped = before - len(coerced["stories"])
        if dropped:
            logger.warning("dropped %d story/stories whose source_url is not in the pre-fetched link set", dropped)

    # Source fields
    for src in coerced.get("sources", []) or []:
        if not isinstance(src, dict):
            continue
        for k in ("id", "name", "url"):
            if src.get(k) is None:
                src[k] = "" if k != "id" else 0

    # Lists
    for k in ("themes", "watch_next_session"):
        v = coerced.get(k)
        if v is None or not isinstance(v, list):
            coerced[k] = []

    # Strings
    for k in ("headline", "executive_summary"):
        if coerced.get(k) is None:
            coerced[k] = ""

    return coerced


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
    allowed_links: set[str] | None = None,
) -> dict:
    """Validate, coerce, and merge the live market snapshot into the payload.

    Returns a minimal stub digest when `payload` is None or not a
    dict. The agent loop already emits a fallback digest when its
    LLM call fails to produce parseable JSON, so the `None` path
    here only protects against future refactors.

    If `allowed_links` is provided, stories with source_urls not in
    that set are dropped during coercion (prevents LLM-hallucinated URLs).
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
    return _merge_payload(_coerce_payload(payload, allowed_links), snapshot)


def _normalize_as_of(as_of: datetime | None) -> datetime:
    """Return an aware UTC anchor.

    Rejects naive timestamps. Aware values are converted to UTC.
    """
    if as_of is None:
        return datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ValueError("naive as_of not allowed; pass an aware datetime")
    return as_of.astimezone(timezone.utc)


def generate_digest(
    *,
    session: str,
    config: AppConfig,
    provider: LLMProvider | None,
    store: DigestStore,
    market_snapshot: dict[str, str] | None = None,
    market_snapshot_meta: MarketSnapshotMeta | None = None,
    as_of: datetime | None = None,
) -> Digest:
    """Generate and persist a digest for the named session.

    `as_of` is the anchor for both the time-window RSS filter and the
    digest's own as_of field. It must be timezone-aware; naive values are
    rejected. Defaults to `datetime.now(timezone.utc)`.

    When `provider` is None, uses the stub pipeline for smoke tests.
    Otherwise fetches a market snapshot, runs the agent, parses the
    result, and writes the digest.
    """
    names_to_defs = {s.name: s for s in all_sessions()}
    if session not in names_to_defs:
        raise ValueError(f"unknown session {session!r}")
    session_def = names_to_defs[session]
    anchor = _normalize_as_of(as_of)

    if provider is None:
        result = make_stub_provider_result()
        logger.info("using stub provider result (no provider wired)")
        payload = json.loads(result.text)
        turns = 1
        tool_call_count = 0
        scraped_url_count = 0
        fallback_used = True
        warning: str | None = None
        duration_ms = 0
        snapshot_quotes: dict[str, Quote] = {}
        snapshot_dict: dict[str, dict[str, str | None]] = {}
    else:
        market_provider = get_market_data_provider(config)
        snapshot_quotes = market_provider.fetch_quotes()
        snapshot_dict = _snapshot_to_dict(snapshot_quotes)

        # Pre-flight RSS: fetch curated headlines for this session, filtered
        # by the exact America/New_York calendar window anchored at `as_of`.
        headlines = fetch_headlines(
            session_def.name,
            per_session_cap=25,
            now=anchor,
        )
        headlines_block = render_for_prompt(headlines)

        tools = get_agent_tools(config)
        system_prompt = session_def.system_prompt
        schema_block = json.dumps(session_def.output_schema, indent=2)
        user_prompt = session_def.user_prompt_template.format(
            topic=session_def.topic,
            time_window=session_def.time_window,
            schema=schema_block,
            headlines=headlines_block,
        )

        agent_result = run_agent(
            provider=provider,
            tools=tools,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            market_snapshot=snapshot_dict,
            max_turns=1,
            json_mode=True,
        )
        if agent_result.parsed_json is None:
            # The LLM returned text that is not parseable JSON. Synthesize
            # a minimal valid digest from the raw text so the trigger
            # does not 500 and the cron does not fail closed. The brief
            # is marked fallback_used=True and the warning is exposed in
            # generation.warning so the operator can see it in the API.
            raw_warning = agent_result.warning or "agent returned no parseable JSON"
            warning = redact_warning(raw_warning)
            logger.warning(
                "agent returned no parseable JSON; emitting fallback digest: %s",
                warning,
            )
            fallback_used = True
            text = (agent_result.final_text or "").strip()
            payload = {
                "headline": ("[STUB] " + (text[:200] if text else "Brief generation failed")),
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
            # Pass the pre-fetched headline URLs so we can drop any
            # story the LLM may have hallucinated a URL for.
            allowed_links = {_normalize_url(h.link) for h in headlines}
            payload = _parse_payload(agent_result.parsed_json, snapshot_dict, allowed_links=allowed_links)
            fallback_used = False
            warning = None
        turns = agent_result.turns
        tool_call_count = len(agent_result.tool_calls)
        # No tools are exposed to the model, so this is always 0. The field is
        # retained because every persisted digest and the public dataset
        # history carry it, and the dashboard reads the same shape.
        scraped_url_count = 0
        fallback_used = fallback_used or agent_result.fallback_used
        warning = redact_warning(warning or agent_result.warning)
        duration_ms = agent_result.duration_ms

    # Build values_raw from fetched quotes; never leave it empty when quotes exist.
    values_raw = {
        sym: {
            "value": q.value,
            "change_pct": q.change_pct,
            "as_of": q.as_of,
            "delayed": q.delayed,
        }
        for sym, q in snapshot_quotes.items()
    }
    meta = market_snapshot_meta or MarketSnapshotMeta(
        source="stooq" if snapshot_quotes else "stub",
        fetched_at=anchor.isoformat(timespec="seconds"),
        values_raw=values_raw,
        delayed=True,
    )
    if snapshot_quotes and not values_raw:
        logger.warning("market snapshot empty despite quotes fetched")

    stories = [Story(**s) for s in payload.get("stories", [])]
    sources = [Source(**s) for s in payload.get("sources", [])]

    digest = Digest(
        session=session,  # type: ignore[arg-type]
        as_of=anchor,
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
            warning=warning,
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
