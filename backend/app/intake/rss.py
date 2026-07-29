from __future__ import annotations

import hashlib
import html
import re
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from app.config.source_catalog import RssSource
from app.intake.models import RssIntakeResult, RssItem, SourceOutcome
from app.intake.policy import OutboundResponse, OutboundUrlPolicy


MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS_PER_FEED = 50
REQUEST_TIMEOUT_SECONDS = 8
ET = ZoneInfo("America/New_York")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


class RssFailureCode(StrEnum):
    ALL_SOURCES_FAILED = "all_sources_failed"
    ZERO_ITEMS = "zero_items"
    STALE_OR_OUT_OF_WINDOW = "stale_or_out_of_window"
    FUTURE_TIMESTAMP = "future_timestamp"
    INVALID_TIMESTAMP = "invalid_timestamp"
    MINIMUM_UNMET = "minimum_unmet"


class RssIntakeFailure(RuntimeError):
    def __init__(
        self,
        code: RssFailureCode,
        *,
        valid_item_count: int = 0,
        source_outcomes: tuple[SourceOutcome, ...] = (),
    ):
        super().__init__(code.value)
        self.code = code
        self.valid_item_count = valid_item_count
        self.source_outcomes = source_outcomes


class RssIntake:
    def __init__(self, transport: object):
        self.transport = transport
        self.url_policy = OutboundUrlPolicy()

    def collect(
        self,
        *,
        sources: tuple[RssSource, ...],
        start_utc: datetime,
        end_utc: datetime,
        retrieved_at: datetime,
        minimum_items: int,
        max_future_skew_seconds: int,
    ) -> RssIntakeResult:
        _require_aware(start_utc)
        _require_aware(end_utc)
        _require_aware(retrieved_at)
        if start_utc >= end_utc:
            raise ValueError("RSS interval must be nonempty")
        if minimum_items < 1:
            raise ValueError("RSS minimum must be positive")

        candidates: list[RssItem] = []
        outcomes: list[SourceOutcome] = []
        raw_item_count = 0
        stale_count = 0
        future_count = 0
        invalid_time_count = 0
        failed_count = 0
        seen_item_ids: set[str] = set()
        for source in sources:
            try:
                response = self.transport.request(
                    "GET",
                    source.url,
                    headers={
                        "accept": (
                            "application/rss+xml, application/atom+xml, "
                            "application/xml, text/xml"
                        ),
                        "user-agent": "REED/0.1",
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    max_bytes=MAX_FEED_BYTES,
                    follow_redirects=True,
                )
                parsed = _parse_feed(response, source, retrieved_at)
            except Exception:
                failed_count += 1
                outcomes.append(
                    SourceOutcome(
                        source_id=source.id,
                        source_url=source.url,
                        retrieved_at=retrieved_at,
                        state="failed",
                        item_count=0,
                        diagnostic="source acquisition failed",
                    )
                )
                continue

            raw_item_count += len(parsed)
            accepted_for_source = 0
            for item, timestamp_state in parsed:
                if timestamp_state == "invalid":
                    invalid_time_count += 1
                    continue
                assert item is not None
                if (
                    item.published_at
                    > retrieved_at + timedelta(seconds=max_future_skew_seconds)
                ):
                    future_count += 1
                    continue
                if not (
                    start_utc
                    <= item.published_at.astimezone(UTC)
                    < end_utc
                ):
                    stale_count += 1
                    continue
                try:
                    canonical = canonicalize_url(item.canonical_url)
                    self.url_policy.parse(canonical)
                except Exception:
                    continue
                accepted_item = RssItem(
                    id=item.id,
                    feed_id=item.feed_id,
                    outlet=item.outlet,
                    title=item.title,
                    canonical_url=canonical,
                    published_at=item.published_at.astimezone(UTC),
                    retrieved_at=item.retrieved_at.astimezone(UTC),
                    source_url=item.source_url,
                    summary=item.summary,
                )
                if accepted_item.id in seen_item_ids:
                    accepted_item = replace(
                        accepted_item,
                        id=(
                            f"{accepted_item.id}-"
                            + hashlib.sha256(canonical.encode()).hexdigest()[:12]
                        ),
                    )
                seen_item_ids.add(accepted_item.id)
                candidates.append(accepted_item)
                accepted_for_source += 1
            outcomes.append(
                SourceOutcome(
                    source_id=source.id,
                    source_url=source.url,
                    retrieved_at=retrieved_at,
                    state="succeeded",
                    item_count=accepted_for_source,
                )
            )

        outcome_tuple = tuple(outcomes)
        if sources and failed_count == len(sources):
            raise RssIntakeFailure(
                RssFailureCode.ALL_SOURCES_FAILED,
                source_outcomes=outcome_tuple,
            )

        deduplicated: list[RssItem] = []
        seen_urls: set[str] = set()
        for item in candidates:
            if item.canonical_url in seen_urls:
                continue
            seen_urls.add(item.canonical_url)
            deduplicated.append(item)

        if len(deduplicated) < minimum_items:
            if deduplicated:
                code = RssFailureCode.MINIMUM_UNMET
            elif future_count and not (stale_count or invalid_time_count):
                code = RssFailureCode.FUTURE_TIMESTAMP
            elif stale_count and not (future_count or invalid_time_count):
                code = RssFailureCode.STALE_OR_OUT_OF_WINDOW
            elif invalid_time_count and not (future_count or stale_count):
                code = RssFailureCode.INVALID_TIMESTAMP
            elif raw_item_count == 0:
                code = RssFailureCode.ZERO_ITEMS
            else:
                code = RssFailureCode.MINIMUM_UNMET
            raise RssIntakeFailure(
                code,
                valid_item_count=len(deduplicated),
                source_outcomes=outcome_tuple,
            )

        return RssIntakeResult(
            items=tuple(deduplicated),
            source_outcomes=outcome_tuple,
            state="partial" if failed_count else "complete",
        )


def _parse_feed(
    response: OutboundResponse,
    source: RssSource,
    retrieved_at: datetime,
) -> list[tuple[RssItem | None, str]]:
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError("RSS source returned a non-success status")
    content_type = response.headers.get("content-type", "").lower()
    stripped = response.body.lstrip().lower()
    if "xml" not in content_type and not stripped.startswith(
        (b"<?xml", b"<rss", b"<feed", b"<rdf")
    ):
        raise ValueError("RSS source did not return XML")
    upper_body = response.body.upper()
    if b"<!DOCTYPE" in upper_body or b"<!ENTITY" in upper_body:
        raise ValueError("RSS source contains a prohibited declaration")
    try:
        root = ElementTree.fromstring(response.body)
    except ElementTree.ParseError as error:
        raise ValueError("RSS source XML is malformed") from error

    entry_nodes = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ][:MAX_ITEMS_PER_FEED]
    parsed: list[tuple[RssItem | None, str]] = []
    for entry in entry_nodes:
        title = _child_text(entry, "title")
        link = _entry_link(entry)
        timestamp_text = (
            _child_text(entry, "pubDate")
            or _child_text(entry, "published")
            or _child_text(entry, "updated")
            or _child_text(entry, "date")
        )
        if not title or not link:
            continue
        published_at = parse_timestamp(timestamp_text)
        if published_at is None:
            parsed.append((None, "invalid"))
            continue
        summary = _sanitize_summary(
            _child_text(entry, "description")
            or _child_text(entry, "summary")
            or _child_text(entry, "content")
            or ""
        )
        identifier = _child_text(entry, "guid") or _child_text(entry, "id")
        if not identifier:
            identifier = "rss-" + hashlib.sha256(
                f"{link}\n{published_at.isoformat()}".encode()
            ).hexdigest()[:20]
        parsed.append(
            (
                RssItem(
                    id=identifier,
                    feed_id=source.id,
                    outlet=source.name,
                    title=" ".join(title.split())[:500],
                    canonical_url=link,
                    published_at=published_at,
                    retrieved_at=retrieved_at,
                    source_url=source.url,
                    summary=summary,
                ),
                "valid",
            )
        )
    return parsed


def parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def canonicalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("item URL is invalid")
    hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    port = parsed.port
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_QUERY_KEYS
        ),
        doseq=True,
    )
    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path or "/",
            query,
            "",
        )
    )


def compute_window_bounds(
    market_window: str,
    anchor: datetime,
) -> tuple[datetime, datetime]:
    _require_aware(anchor)
    anchor_et = anchor.astimezone(ET)
    anchor_date = anchor_et.date()
    specs = {
        "pre_market": ((-1, 17, 0), (0, 8, 0)),
        "early_market": ((0, 8, 0), (0, 9, 45)),
        "midday": ((0, 9, 45), (0, 12, 30)),
        "close": ((0, 12, 30), (0, 16, 15)),
    }
    if market_window == "weekend_recap":
        if anchor_et.weekday() != 0:
            raise ValueError("weekend recap requires a Monday occurrence")
        start_date = anchor_date - timedelta(days=2)
        start = datetime.combine(start_date, time(0, 0), ET)
        end = datetime.combine(anchor_date, time(0, 0), ET)
    else:
        try:
            start_spec, end_spec = specs[market_window]
        except KeyError as error:
            raise ValueError("unknown market window") from error
        start_date = anchor_date + timedelta(days=start_spec[0])
        end_date = anchor_date + timedelta(days=end_spec[0])
        start = datetime.combine(
            start_date,
            time(start_spec[1], start_spec[2]),
            ET,
        )
        end = datetime.combine(
            end_date,
            time(end_spec[1], end_spec[2]),
            ET,
        )
    return start.astimezone(UTC), end.astimezone(UTC)


def _entry_link(entry: ElementTree.Element) -> str:
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        relation = child.attrib.get("rel", "alternate")
        if href and relation in {"", "alternate"}:
            return href
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def _child_text(entry: ElementTree.Element, name: str) -> str:
    for child in entry:
        if _local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _sanitize_summary(value: str) -> str:
    without_tags = re.sub(r"<[^>]*>", " ", html.unescape(value))
    return " ".join(without_tags.split())[:1000]


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
