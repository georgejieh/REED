"""RSS news pre-flight for REED.

Fetches public RSS feeds for the named session, parses with feedparser,
dedupes, and returns a flat list of headlines. The agent loop uses
this list as the primary context for brief generation; no external
search API is involved.

Cost: zero. The HTTP requests go directly to public RSS endpoints.
No API keys, no rate limits beyond what each publisher imposes on
RSS (typically generous).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class Headline:
    outlet: str
    title: str
    link: str
    published_at: str  # ISO 8601; may be empty if the feed omits it
    summary: str


# Per-session feed lists. Each session uses a curated subset.
RSS_FEEDS = {
    "pre_market": [
        ("MarketWatch Top Stories", "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("MarketWatch Real-time", "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/"),
        ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ],
    "early_market": [
        ("MarketWatch Real-time", "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/"),
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("Seeking Alpha", "https://seekingalpha.com/feed.xml"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ],
    "midday": [
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("MarketWatch Top Stories", "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("Seeking Alpha", "https://seekingalpha.com/feed.xml"),
        ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ],
    "close": [
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("MarketWatch Top Stories", "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("MarketWatch Real-time", "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/"),
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ],
    "weekend_recap": [
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("MarketWatch Top Stories", "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("FT Home", "https://www.ft.com/rss/home"),
        ("Seeking Alpha", "https://seekingalpha.com/feed.xml"),
    ],
}


MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB cap per feed
REQUEST_TIMEOUT = 8.0  # seconds per feed
MAX_CONCURRENT = 4  # bounded concurrency across feeds

# Headlines dated more than this many minutes in the future are
# treated as bogus (clock skew, timezone bugs) and dropped by filter_by_window.
FUTURE_TOLERANCE_MINUTES = 15


async def _fetch_one(client: httpx.AsyncClient, outlet: str, url: str) -> list[Headline]:
    try:
        response = await client.get(
            url,
            headers={
                "User-Agent": "REED/0.1 (+https://huggingface.co/spaces/coldashsage/reed)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
            },
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        logger.warning("rss fetch failed for %s (%s): %s", outlet, url, exc)
        return []
    if response.status_code >= 400:
        logger.warning("rss status %d for %s (%s)", response.status_code, outlet, url)
        return []
    # Verify content-type is XML-flavored.
    ct = (response.headers.get("content-type") or "").lower()
    if "xml" not in ct and not response.text.lstrip().startswith(("<?xml", "<rss", "<feed", "<rdf")):
        logger.warning("rss non-xml body for %s (%s): content-type=%s", outlet, url, ct)
        return []
    # Cap response body to prevent OOM on a free Space; reject oversized feeds early.
    cl = response.headers.get("content-length")
    if cl and int(cl) > MAX_RESPONSE_BYTES:
        logger.warning("rss %s exceeded size cap (header): %s bytes", outlet, cl)
        return []
    content = response.content[: MAX_RESPONSE_BYTES + 1]
    if len(content) > MAX_RESPONSE_BYTES:
        logger.warning("rss %s exceeded size cap (actual): %s bytes", outlet, len(content))
        return []
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        logger.warning("rss bozo for %s (%s): %s", outlet, url, parsed.bozo_exception)
        return []
    out: list[Headline] = []
    for entry in parsed.entries[:15]:  # cap per outlet
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        # Sanitize summary: strip HTML, collapse whitespace, cap length.
        raw_summary = (entry.get("summary") or entry.get("description") or "").strip()
        if raw_summary:
            soup = BeautifulSoup(raw_summary, "html.parser")
            summary = soup.get_text(" ", strip=True)
            summary = " ".join(summary.split())  # collapse whitespace
        else:
            summary = ""
        if len(summary) > 400:
            summary = summary[:397] + "..."
        out.append(
            Headline(
                outlet=outlet,
                title=title,
                link=link,
                published_at=_parse_published(entry),
                summary=summary,
            )
        )
    return out


def _parse_published(entry: Any) -> str:
    raw = entry.get("published_parsed") or entry.get("updated_parsed")
    if raw:
        try:
            return datetime(*raw[:6], tzinfo=timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    raw = entry.get("published") or entry.get("updated")
    return str(raw) if raw else ""


async def _fetch_all_async(feeds: list[tuple[str, str]]) -> list[Headline]:
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        async def one(outlet: str, url: str) -> list[Headline]:
            async with sem:
                return await _fetch_one(client, outlet, url)
        results = await asyncio.gather(
            *(one(outlet, url) for outlet, url in feeds),
            return_exceptions=True,
        )
    out: list[Headline] = []
    seen: set[str] = set()
    for r in results:
        if isinstance(r, BaseException):
            logger.warning("rss fetch raised: %s", r)
            continue
        for h in r:
            if h.link in seen:
                continue
            seen.add(h.link)
            out.append(h)
    return out


def fetch_headlines(
    session: str,
    *,
    time_window: str | None = None,
    per_session_cap: int = 25,
    now: datetime | None = None,
) -> list[Headline]:
    """Synchronous entry point. Fetches all configured feeds for `session`,
    dedupes by link, optionally filters by `time_window`, and caps at
    per_session_cap.

    `now` is the anchor for the time-window filter. Default is the current
    UTC time. Pass an explicit `now` for backfill to target a past date.
    """
    feeds = RSS_FEEDS.get(session, [])
    if not feeds:
        logger.warning("no rss feeds configured for session %s", session)
        return []
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # In an async context, we should not block. Use a fresh thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                headlines = pool.submit(asyncio.run, _fetch_all_async(feeds)).result()
        else:
            headlines = asyncio.run(_fetch_all_async(feeds))
    except RuntimeError:
        # No event loop at all.
        headlines = asyncio.run(_fetch_all_async(feeds))
    if time_window:
        before = len(headlines)
        headlines = filter_by_window(headlines, time_window, now=now)
        logger.info(
            "rss filter: kept %d of %d headlines for time_window=%r now=%s",
            len(headlines), before, time_window, now.isoformat() if now else "live",
        )
    return headlines[:per_session_cap]


_TIME_WINDOW_UNITS = {
    "minute": 60,
    "minutes": 60,
    "hour": 3600,
    "hours": 3600,
    "day": 86400,
    "days": 86400,
    "week": 604800,
    "weeks": 604800,
}


def parse_time_window(text: str) -> timedelta | None:
    """Parse a session time_window string like "last 12 hours" or "last 90 minutes".

    Returns a timedelta or None if the string is not in the expected format.
    """
    import re
    if not text:
        return None
    m = re.match(
        r"^\s*last\s+(\d+)\s+(minute|minutes|hour|hours|day|days|week|weeks)\s*$",
        text.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    return timedelta(seconds=_TIME_WINDOW_UNITS[unit] * n)


def _published_to_aware_dt(published_at: str) -> datetime | None:
    """Convert a Headline.published_at string to an aware UTC datetime.

    Returns None if the string is empty or unparseable. Naive timestamps
    are assumed to be UTC (RSS feeds often omit the timezone).
    """
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def filter_by_window(
    headlines: list[Headline],
    time_window: str,
    *,
    now: datetime | None = None,
) -> list[Headline]:
    """Return only headlines whose published_at is within `time_window` AND not
    significantly in the future relative to `now`.

    A headline is kept when `cutoff <= published_at <= now + FUTURE_TOLERANCE`.
    The upper bound exists because real RSS feeds occasionally have
    future-dated items (clock skew, timezone bugs, promo items). For
    backfill mode (now anchored to a past date), the upper bound drops
    headlines that were published AFTER the backfill date.

    `now` is injectable for tests; defaults to `datetime.now(timezone.utc)`.

    Headlines without a usable timestamp are dropped. The model has no web
    access and its training data is older than the session window, so it
    cannot judge whether an undated item belongs in this brief. An undated
    entry that survives into the prompt is indistinguishable from a current
    one, so the safe default is to drop it rather than let the model guess.
    """
    delta = parse_time_window(time_window)
    if delta is None:
        # Unparseable window: keep everything.
        return list(headlines)
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - delta
    upper = now + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)
    out: list[Headline] = []
    undated = 0
    for h in headlines:
        dt = _published_to_aware_dt(h.published_at)
        if dt is None:
            undated += 1
            continue
        if cutoff <= dt <= upper:
            out.append(h)
    if undated:
        logger.info("rss filter: dropped %d headline(s) with no usable timestamp", undated)
    return out


def render_for_prompt(headlines: list[Headline]) -> str:
    """Render the headlines list as plain text for the LLM user prompt.

    Format is compact and token-efficient. Each headline gets:
    outlet, published_at, title, link, summary (truncated).
    """
    if not headlines:
        return "(no headlines available from rss feeds)"
    lines: list[str] = []
    for i, h in enumerate(headlines, 1):
        parts: list[str] = [f"[{i}]", h.outlet]
        if h.published_at:
            parts.append(h.published_at)
        parts.append(f"title: {h.title}")
        if h.summary:
            parts.append(f"summary: {h.summary}")
        parts.append(f"link: {h.link}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)
