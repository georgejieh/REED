from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config.source_catalog import RssSource
from app.intake.policy import OutboundResponse
from app.intake.rss import (
    RssFailureCode,
    RssIntake,
    RssIntakeFailure,
    compute_window_bounds,
    parse_timestamp,
)


class FeedTransport:
    def __init__(self, responses: dict[str, OutboundResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def request(self, method: str, url: str, **_: object) -> OutboundResponse:
        self.requests.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def response(body: str, content_type: str = "application/rss+xml") -> OutboundResponse:
    return OutboundResponse(
        status_code=200,
        headers={"content-type": content_type},
        body=body.encode(),
        final_url="https://feeds.example.com/feed.xml",
    )


def rss(*items: tuple[str, str, str, str]) -> str:
    entries = "".join(
        (
            "<item>"
            f"<title>{title}</title>"
            f"<link>{link}</link>"
            f"<pubDate>{published}</pubDate>"
            f"<description>{summary}</description>"
            "</item>"
        )
        for title, link, published, summary in items
    )
    return f"<?xml version='1.0'?><rss><channel>{entries}</channel></rss>"


def source(identifier: str, url: str) -> RssSource:
    return RssSource(id=identifier, name=identifier.title(), url=url)


def test_partial_source_failure_continues_with_provenance() -> None:
    good_url = "https://feeds.example.com/good.xml"
    bad_url = "https://feeds.example.com/bad.xml"
    transport = FeedTransport(
        {
            good_url: response(
                rss(
                    (
                        "Opening update",
                        "https://news.example.com/story?utm_source=feed",
                        "Tue, 28 Jul 2026 11:30:00 +0000",
                        "<b>Markets</b> moved.",
                    )
                )
            ),
            bad_url: RuntimeError("feed unavailable"),
        }
    )

    result = RssIntake(transport).collect(
        sources=(source("good", good_url), source("bad", bad_url)),
        start_utc=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
        end_utc=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        minimum_items=1,
        max_future_skew_seconds=60,
    )

    assert result.state == "partial"
    assert len(result.items) == 1
    assert result.items[0].canonical_url == "https://news.example.com/story"
    assert result.items[0].feed_id == "good"
    assert result.items[0].retrieved_at == datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert result.items[0].summary == "Markets moved."
    assert [outcome.state for outcome in result.source_outcomes] == [
        "succeeded",
        "failed",
    ]


def test_deduplicates_canonical_urls_before_applying_configured_minimum() -> None:
    feed_url = "https://feeds.example.com/feed.xml"
    transport = FeedTransport(
        {
            feed_url: response(
                rss(
                    (
                        "Story",
                        "https://news.example.com/story?utm_source=one",
                        "Tue, 28 Jul 2026 11:20:00 +0000",
                        "One",
                    ),
                    (
                        "Story copy",
                        "https://NEWS.example.com/story?utm_medium=two#top",
                        "Tue, 28 Jul 2026 11:21:00 +0000",
                        "Two",
                    ),
                )
            )
        }
    )

    with pytest.raises(RssIntakeFailure) as failure:
        RssIntake(transport).collect(
            sources=(source("feed", feed_url),),
            start_utc=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
            end_utc=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            minimum_items=2,
            max_future_skew_seconds=60,
        )

    assert failure.value.code is RssFailureCode.MINIMUM_UNMET
    assert failure.value.valid_item_count == 1


def test_all_sources_failed_has_distinct_failure_state() -> None:
    feed_url = "https://feeds.example.com/feed.xml"
    transport = FeedTransport({feed_url: RuntimeError("network failure")})

    with pytest.raises(RssIntakeFailure) as failure:
        RssIntake(transport).collect(
            sources=(source("feed", feed_url),),
            start_utc=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
            end_utc=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            minimum_items=1,
            max_future_skew_seconds=60,
        )

    assert failure.value.code is RssFailureCode.ALL_SOURCES_FAILED


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (rss(), RssFailureCode.ZERO_ITEMS),
        (
            rss(
                (
                    "Old story",
                    "https://news.example.com/old",
                    "Tue, 28 Jul 2026 07:59:59 +0000",
                    "Old",
                )
            ),
            RssFailureCode.STALE_OR_OUT_OF_WINDOW,
        ),
        (
            rss(
                (
                    "Future story",
                    "https://news.example.com/future",
                    "Tue, 28 Jul 2026 12:01:01 +0000",
                    "Future",
                )
            ),
            RssFailureCode.FUTURE_TIMESTAMP,
        ),
    ],
)
def test_zero_stale_and_future_timestamp_failures_are_precise(
    body: str,
    code: RssFailureCode,
) -> None:
    feed_url = "https://feeds.example.com/feed.xml"

    with pytest.raises(RssIntakeFailure) as failure:
        RssIntake(FeedTransport({feed_url: response(body)})).collect(
            sources=(source("feed", feed_url),),
            start_utc=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
            end_utc=datetime(2026, 7, 28, 13, 0, tzinfo=UTC),
            retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            minimum_items=1,
            max_future_skew_seconds=60,
        )

    assert failure.value.code is code


def test_atom_parsing_requires_an_explicit_timestamp_offset() -> None:
    feed_url = "https://feeds.example.com/atom.xml"
    atom = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Offset missing</title>
        <link href="https://news.example.com/story" />
        <updated>2026-07-28T11:30:00</updated>
      </entry>
    </feed>
    """

    with pytest.raises(RssIntakeFailure) as failure:
        RssIntake(FeedTransport({feed_url: response(atom)})).collect(
            sources=(source("feed", feed_url),),
            start_utc=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
            end_utc=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            minimum_items=1,
            max_future_skew_seconds=60,
        )

    assert failure.value.code is RssFailureCode.INVALID_TIMESTAMP


def test_duplicate_feed_identifiers_do_not_collapse_distinct_urls() -> None:
    feed_url = "https://feeds.example.com/feed.xml"
    body = (
        "<?xml version='1.0'?><rss><channel>"
        "<item><guid>shared</guid><title>One</title>"
        "<link>https://news.example.com/one</link>"
        "<pubDate>Tue, 28 Jul 2026 11:20:00 +0000</pubDate></item>"
        "<item><guid>shared</guid><title>Two</title>"
        "<link>https://news.example.com/two</link>"
        "<pubDate>Tue, 28 Jul 2026 11:21:00 +0000</pubDate></item>"
        "</channel></rss>"
    )

    result = RssIntake(FeedTransport({feed_url: response(body)})).collect(
        sources=(source("feed", feed_url),),
        start_utc=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
        end_utc=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        minimum_items=2,
        max_future_skew_seconds=60,
    )

    assert len({item.id for item in result.items}) == 2


def test_rss_interval_is_start_inclusive_and_end_exclusive() -> None:
    feed_url = "https://feeds.example.com/feed.xml"
    body = rss(
        (
            "At start",
            "https://news.example.com/start",
            "Tue, 28 Jul 2026 08:00:00 +0000",
            "Included",
        ),
        (
            "At end",
            "https://news.example.com/end",
            "Tue, 28 Jul 2026 12:00:00 +0000",
            "Excluded",
        ),
    )

    result = RssIntake(FeedTransport({feed_url: response(body)})).collect(
        sources=(source("feed", feed_url),),
        start_utc=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
        end_utc=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        minimum_items=1,
        max_future_skew_seconds=60,
    )

    assert [item.title for item in result.items] == ["At start"]


def test_market_window_interval_is_dst_correct_in_utc() -> None:
    spring = compute_window_bounds(
        "pre_market",
        datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
    )
    fall = compute_window_bounds(
        "pre_market",
        datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
    )

    assert spring == (
        datetime(2026, 3, 8, 21, 0, tzinfo=UTC),
        datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
    )
    assert fall == (
        datetime(2026, 11, 1, 22, 0, tzinfo=UTC),
        datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
    )


def test_weekend_recap_covers_complete_saturday_and_sunday() -> None:
    bounds = compute_window_bounds(
        "weekend_recap",
        datetime(2026, 7, 27, 11, 0, tzinfo=UTC),
    )

    assert bounds == (
        datetime(2026, 7, 25, 4, 0, tzinfo=UTC),
        datetime(2026, 7, 27, 4, 0, tzinfo=UTC),
    )


def test_weekend_recap_bounds_are_dst_safe() -> None:
    spring = compute_window_bounds(
        "weekend_recap",
        datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
    )
    fall = compute_window_bounds(
        "weekend_recap",
        datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
    )

    assert spring == (
        datetime(2026, 3, 7, 5, 0, tzinfo=UTC),
        datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
    )
    assert spring[1] - spring[0] == timedelta(hours=47)
    assert fall == (
        datetime(2026, 10, 31, 4, 0, tzinfo=UTC),
        datetime(2026, 11, 2, 5, 0, tzinfo=UTC),
    )
    assert fall[1] - fall[0] == timedelta(hours=49)


def test_weekend_recap_interval_is_saturday_inclusive_monday_exclusive() -> None:
    feed_url = "https://feeds.example.com/weekend.xml"
    body = rss(
        (
            "Before Saturday",
            "https://news.example.com/before",
            "Sat, 25 Jul 2026 03:59:59 +0000",
            "Excluded",
        ),
        (
            "Saturday start",
            "https://news.example.com/start",
            "Sat, 25 Jul 2026 04:00:00 +0000",
            "Included",
        ),
        (
            "Monday boundary",
            "https://news.example.com/end",
            "Mon, 27 Jul 2026 04:00:00 +0000",
            "Excluded",
        ),
    )
    start_utc, end_utc = compute_window_bounds(
        "weekend_recap",
        datetime(2026, 7, 27, 11, 0, tzinfo=UTC),
    )

    result = RssIntake(
        FeedTransport({feed_url: response(body)})
    ).collect(
        sources=(source("weekend", feed_url),),
        start_utc=start_utc,
        end_utc=end_utc,
        retrieved_at=datetime(2026, 7, 27, 11, 0, tzinfo=UTC),
        minimum_items=1,
        max_future_skew_seconds=60,
    )

    assert [item.title for item in result.items] == ["Saturday start"]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2026-11-01T01:30:00",
        "2026-03-08T02:30:00",
    ],
)
def test_missing_ambiguous_and_nonexistent_unoffset_times_are_rejected(
    value: str,
) -> None:
    assert parse_timestamp(value) is None


def test_explicit_offsets_disambiguate_dst_transition_timestamps() -> None:
    first = parse_timestamp("2026-11-01T01:30:00-04:00")
    second = parse_timestamp("2026-11-01T01:30:00-05:00")

    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert second == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
