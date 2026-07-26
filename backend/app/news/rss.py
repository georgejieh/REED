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
from zoneinfo import ZoneInfo

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


@dataclass
class WindowBounds:
    """Inclusive start/end in America/New_York local time."""

    start: datetime
    end: datetime


ET = ZoneInfo("America/New_York")


# Per-session ET time windows. Bounds are inclusive and keyed to the
# anchor day's America/New_York calendar date.
#
# Tuple shape: ((start_day_delta, start_hour, start_minute),
#               (end_day_delta, end_hour, end_minute)).
# start_day_delta=0 means the same ET calendar day as the anchor;
# -1 means the previous calendar day.
_SESSION_WINDOWS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "pre_market": ((-1, 17, 0), (0, 8, 0)),
    "early_market": ((0, 8, 0), (0, 9, 45)),
    "midday": ((0, 9, 45), (0, 12, 30)),
    "close": ((0, 12, 30), (0, 16, 15)),
}

WEEKEND_RECAP_END_WEEKDAY = 0  # Monday
WEEKEND_RECAP_START_DAY_DELTA = 3  # Friday (Mon - 3 days).
WEEKEND_RECAP_END_DAY_DELTA = 2  # Saturday (Mon - 2 days).
WEEKEND_RECAP_START_HOUR, WEEKEND_RECAP_START_MINUTE = 17, 0
WEEKEND_RECAP_END_HOUR, WEEKEND_RECAP_END_MINUTE = 23, 55


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
    seen: set[str] = set()
    for entry in parsed.entries[:15]:  # cap per outlet
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link or link in seen:
            continue
        seen.add(link)
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


def _ensure_aware_utc(value: datetime) -> datetime:
    """Reject naive datetimes and return an aware UTC datetime."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("naive datetime not allowed; pass an aware value")
    return value.astimezone(timezone.utc)


def compute_session_bounds(session: str, anchor: datetime) -> WindowBounds:
    """Return inclusive America/New_York bounds for `session`.

    `anchor` must be timezone-aware. It is normalized to ET and used to
    pick the anchor calendar date; the exact window depends only on
    that date, not on the anchor's minute.

    weekend_recap requires a Monday anchor and spans the previous
    Friday 17:00 through Saturday 23:55 ET.
    """
    anchor_utc = _ensure_aware_utc(anchor)
    anchor_et = anchor_utc.astimezone(ET)
    if session == "weekend_recap":
        if anchor_et.weekday() != WEEKEND_RECAP_END_WEEKDAY:
            raise ValueError(
                "weekend_recap requires a Monday anchor in America/New_York"
            )
        anchor_date = anchor_et.date()
        start_date = anchor_date - timedelta(days=WEEKEND_RECAP_START_DAY_DELTA)
        end_date = anchor_date - timedelta(days=WEEKEND_RECAP_END_DAY_DELTA)
        # Build wall-clock datetimes on the target dates so each bound
        # inherits the natural offset for that date (handles DST).
        start = datetime(
            start_date.year, start_date.month, start_date.day,
            WEEKEND_RECAP_START_HOUR, WEEKEND_RECAP_START_MINUTE,
            tzinfo=ET,
        )
        end = datetime(
            end_date.year, end_date.month, end_date.day,
            WEEKEND_RECAP_END_HOUR, WEEKEND_RECAP_END_MINUTE,
            tzinfo=ET,
        )
        return WindowBounds(start=start, end=end)
    spec = _SESSION_WINDOWS.get(session)
    if spec is None:
        raise ValueError(f"unknown session {session!r}")
    start_offset, end_offset = spec
    start = (anchor_et + timedelta(days=start_offset[0])).replace(
        hour=start_offset[1], minute=start_offset[2], second=0, microsecond=0
    )
    end = (anchor_et + timedelta(days=end_offset[0])).replace(
        hour=end_offset[1], minute=end_offset[2], second=0, microsecond=0
    )
    return WindowBounds(start=start, end=end)


def filter_by_session(
    headlines: list[Headline],
    session: str,
    anchor: datetime,
) -> list[Headline]:
    """Return headlines whose published_at falls within the exact ET
    calendar window for `session` anchored at `anchor`.

    `anchor` must be timezone-aware. Naive anchors raise ValueError.
    Entries without a parseable timestamp are dropped. Entries whose
    publication timestamp is before the window start or after the
    inclusive window end are dropped. There is no future-tolerance
    extension; the window end is strict.
    """
    bounds = compute_session_bounds(session, anchor)
    start_utc = bounds.start.astimezone(timezone.utc)
    end_utc = bounds.end.astimezone(timezone.utc)
    out: list[Headline] = []
    undated = 0
    for h in headlines:
        dt = _published_to_aware_dt(h.published_at)
        if dt is None:
            undated += 1
            continue
        if start_utc <= dt <= end_utc:
            out.append(h)
    if undated:
        logger.info("rss filter: dropped %d headline(s) with no usable timestamp", undated)
    return out


def fetch_headlines(
    session: str,
    *,
    per_session_cap: int = 25,
    now: datetime | None = None,
) -> list[Headline]:
    """Synchronous entry point. Fetches all configured feeds for `session`,
    dedupes by link, filters by the exact session-aware calendar window
    anchored at `now`, and caps at `per_session_cap`.

    `now` is the anchor for the time-window filter. It must be
    timezone-aware when given; otherwise it is rejected. Defaults to the
    current UTC time.
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
    if now is not None:
        now = _ensure_aware_utc(now)
    else:
        now = datetime.now(timezone.utc)
    # Always apply the exact session window when an anchor is available.
    headlines = filter_by_session(headlines, session, now)
    return headlines[:per_session_cap]


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
