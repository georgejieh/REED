"""Unit tests for exact America/New_York RSS time windows.

All tests use stdlib unittest. They cover exact inclusive bounds, DST
handling, naive-anchor rejection, future/stale date handling, and the
Monday-only weekend recap rule.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch as _mock_patch
from zoneinfo import ZoneInfo

from app.news.rss import (
    Headline,
    compute_session_bounds,
    fetch_headlines,
    filter_by_session,
)


ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def _h(published_at: str, title: str = "headline") -> Headline:
    """Build a Headline with the given ISO-8601 publication timestamp."""
    return Headline(
        outlet="test",
        title=title,
        link=f"https://example.com/{title}",
        published_at=published_at,
        summary="",
    )


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Build an aware America/New_York datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=ET)


class TestExactWindowBounds(unittest.TestCase):
    """Exact inclusive boundaries for each weekday session."""

    def test_pre_market_includes_previous_day_1700_and_anchor_0800(self):
        anchor = _et(2026, 7, 21, 8, 0)  # Tuesday 08:00 ET.
        bounds = compute_session_bounds("pre_market", anchor)
        self.assertEqual(bounds.start, _et(2026, 7, 20, 17, 0))
        self.assertEqual(bounds.end, _et(2026, 7, 21, 8, 0))

        # In UTC: start = 2026-07-20 21:00; end = 2026-07-21 12:00.
        headlines = [
            _h("2026-07-20T20:59:59+00:00", "before_start_excluded"),
            _h("2026-07-20T21:00:00+00:00", "start_included"),
            _h("2026-07-21T11:59:59+00:00", "before_end_included"),
            _h("2026-07-21T12:00:00+00:00", "end_included"),
            _h("2026-07-21T12:00:01+00:00", "after_end_excluded"),
        ]
        kept = filter_by_session(headlines, "pre_market", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(titles, {"start_included", "before_end_included", "end_included"})

    def test_early_market_0800_through_0945(self):
        anchor = _et(2026, 7, 21, 9, 45)
        bounds = compute_session_bounds("early_market", anchor)
        self.assertEqual(bounds.start, _et(2026, 7, 21, 8, 0))
        self.assertEqual(bounds.end, _et(2026, 7, 21, 9, 45))

        # UTC: start = 12:00; end = 13:45.
        headlines = [
            _h("2026-07-21T11:59:59+00:00", "before_start_excluded"),
            _h("2026-07-21T12:00:00+00:00", "start_included"),
            _h("2026-07-21T13:44:59+00:00", "before_end_included"),
            _h("2026-07-21T13:45:00+00:00", "end_included"),
            _h("2026-07-21T13:45:01+00:00", "after_end_excluded"),
        ]
        kept = filter_by_session(headlines, "early_market", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(titles, {"start_included", "before_end_included", "end_included"})

    def test_midday_0945_through_1230(self):
        anchor = _et(2026, 7, 21, 12, 30)
        bounds = compute_session_bounds("midday", anchor)
        self.assertEqual(bounds.start, _et(2026, 7, 21, 9, 45))
        self.assertEqual(bounds.end, _et(2026, 7, 21, 12, 30))

        # UTC: start = 13:45; end = 16:30.
        headlines = [
            _h("2026-07-21T13:44:59+00:00", "before_start_excluded"),
            _h("2026-07-21T13:45:00+00:00", "start_included"),
            _h("2026-07-21T16:29:59+00:00", "before_end_included"),
            _h("2026-07-21T16:30:00+00:00", "end_included"),
            _h("2026-07-21T16:30:01+00:00", "after_end_excluded"),
        ]
        kept = filter_by_session(headlines, "midday", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(titles, {"start_included", "before_end_included", "end_included"})

    def test_close_1230_through_1615(self):
        anchor = _et(2026, 7, 21, 16, 15)
        bounds = compute_session_bounds("close", anchor)
        self.assertEqual(bounds.start, _et(2026, 7, 21, 12, 30))
        self.assertEqual(bounds.end, _et(2026, 7, 21, 16, 15))

        # UTC: start = 16:30; end = 20:15.
        headlines = [
            _h("2026-07-21T16:29:59+00:00", "before_start_excluded"),
            _h("2026-07-21T16:30:00+00:00", "start_included"),
            _h("2026-07-21T20:14:59+00:00", "before_end_included"),
            _h("2026-07-21T20:15:00+00:00", "end_included"),
            _h("2026-07-21T20:15:01+00:00", "after_end_excluded"),
        ]
        kept = filter_by_session(headlines, "close", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(titles, {"start_included", "before_end_included", "end_included"})


class TestWeekendRecapBounds(unittest.TestCase):
    """Weekend recap is Monday-only, spans Friday 17:00 to Saturday 23:55."""

    def test_weekend_recap_monday_anchor_valid(self):
        anchor = _et(2026, 7, 20, 7, 0)  # Monday.
        bounds = compute_session_bounds("weekend_recap", anchor)
        self.assertEqual(bounds.start, _et(2026, 7, 17, 17, 0))
        self.assertEqual(bounds.end, _et(2026, 7, 18, 23, 55))

    def test_weekend_recap_friday_1700_saturday_2355_inclusive(self):
        anchor = _et(2026, 7, 20, 7, 0)  # Monday.
        # UTC: start = Friday 21:00; end = Sunday 03:55.
        headlines = [
            _h("2026-07-17T20:59:59+00:00", "friday_1659_excluded"),
            _h("2026-07-17T21:00:00+00:00", "friday_1700_included"),
            _h("2026-07-19T03:54:00+00:00", "saturday_2354_included"),
            _h("2026-07-19T03:55:00+00:00", "saturday_2355_included"),
            _h("2026-07-19T03:55:01+00:00", "saturday_2355_excluded"),
            _h("2026-07-19T04:00:00+00:00", "sunday_0000_excluded"),
        ]
        kept = filter_by_session(headlines, "weekend_recap", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(
            titles,
            {"friday_1700_included", "saturday_2354_included", "saturday_2355_included"},
        )

    def test_weekend_recap_rejects_non_monday_anchor(self):
        tuesday = _et(2026, 7, 21, 7, 0)
        with self.assertRaises(ValueError):
            compute_session_bounds("weekend_recap", tuesday)

        sunday = _et(2026, 7, 19, 7, 0)
        with self.assertRaises(ValueError):
            compute_session_bounds("weekend_recap", sunday)

        friday = _et(2026, 7, 17, 7, 0)
        with self.assertRaises(ValueError):
            compute_session_bounds("weekend_recap", friday)

    def test_weekend_recap_spring_forward_monday_0000_anchor(self):
        """Spring DST: Mon 2025-03-10 00:00 EDT anchor.

        Friday (2025-03-07) is still EST; Saturday (2025-03-08) is still
        EST. The window must use each target date's natural offset, not
        the anchor's EDT offset. Buggy timedelta subtraction yielded
        Fri 10:00 EST and Sat 16:55 EST; the correct bounds are
        Fri 17:00 EST and Sat 23:55 EST.
        """
        anchor = _et(2025, 3, 10, 0, 0)
        bounds = compute_session_bounds("weekend_recap", anchor)
        self.assertEqual(bounds.start, _et(2025, 3, 7, 17, 0))
        self.assertEqual(bounds.end, _et(2025, 3, 8, 23, 55))

    def test_weekend_recap_fall_back_monday_0000_anchor(self):
        """Fall DST: Mon 2025-11-03 00:00 EST anchor.

        Friday (2025-10-31) is still EDT; Saturday (2025-11-01) is still
        EDT. The window must use each target date's natural offset, not
        the anchor's EST offset. Buggy timedelta subtraction yielded
        Fri 10:00 EDT and Sat 16:55 EDT; the correct bounds are
        Fri 17:00 EDT and Sat 23:55 EDT.
        """
        anchor = _et(2025, 11, 3, 0, 0)
        bounds = compute_session_bounds("weekend_recap", anchor)
        self.assertEqual(bounds.start, _et(2025, 10, 31, 17, 0))
        self.assertEqual(bounds.end, _et(2025, 11, 1, 23, 55))

    def test_weekend_recap_spring_forward_0000_anchor_filtering(self):
        """End-to-end filtering at the 00:00 EDT anchor must keep
        Friday 17:00 EST and Saturday 23:55 EST headlines and drop
        everything outside the window.
        """
        anchor = _et(2025, 3, 10, 0, 0)
        # Fri 17:00 EST = 22:00 UTC; Sat 23:55 EST = 2025-03-09 04:55 UTC.
        headlines = [
            _h("2025-03-07T21:59:59+00:00", "friday_1659_excluded"),
            _h("2025-03-07T22:00:00+00:00", "friday_1700_included"),
            _h("2025-03-09T04:54:59+00:00", "saturday_2354_included"),
            _h("2025-03-09T04:55:00+00:00", "saturday_2355_included"),
            _h("2025-03-09T04:55:01+00:00", "saturday_2355_excluded"),
        ]
        kept = filter_by_session(headlines, "weekend_recap", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(
            titles,
            {"friday_1700_included", "saturday_2354_included", "saturday_2355_included"},
        )

    def test_weekend_recap_fall_back_0000_anchor_filtering(self):
        """End-to-end filtering at the 00:00 EST anchor must keep
        Friday 17:00 EDT and Saturday 23:55 EDT headlines and drop
        everything outside the window.
        """
        anchor = _et(2025, 11, 3, 0, 0)
        # Fri 17:00 EDT = 21:00 UTC; Sat 23:55 EDT = 2025-11-02 03:55 UTC.
        headlines = [
            _h("2025-10-31T20:59:59+00:00", "friday_1659_excluded"),
            _h("2025-10-31T21:00:00+00:00", "friday_1700_included"),
            _h("2025-11-02T03:54:59+00:00", "saturday_2354_included"),
            _h("2025-11-02T03:55:00+00:00", "saturday_2355_included"),
            _h("2025-11-02T03:55:01+00:00", "saturday_2355_excluded"),
        ]
        kept = filter_by_session(headlines, "weekend_recap", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(
            titles,
            {"friday_1700_included", "saturday_2354_included", "saturday_2355_included"},
        )


class TestFetchHeadlinesProductionPath(unittest.TestCase):
    """fetch_headlines must apply the exact session window to fetched
    headlines. Patch _fetch_all_async to supply deterministic fixtures
    and verify the filtered result respects the requested anchor."""

    def test_weekend_recap_0000_anchor_filters_via_fetch_headlines(self):
        anchor = _et(2025, 11, 3, 0, 0)  # DST fall-back Monday 00:00 EST.
        fixture = [
            _h("2025-10-31T20:59:59+00:00", "friday_1659_excluded"),
            _h("2025-10-31T21:00:00+00:00", "friday_1700_included"),
            _h("2025-11-02T03:54:59+00:00", "saturday_2354_included"),
            _h("2025-11-02T03:55:00+00:00", "saturday_2355_included"),
            _h("2025-11-02T03:55:01+00:00", "saturday_2355_excluded"),
        ]
        from app.news import rss as rss_mod
        with _mock_patch.object(rss_mod, "_fetch_all_async", return_value=fixture):
            kept = fetch_headlines("weekend_recap", now=anchor)
        titles = {h.title for h in kept}
        self.assertEqual(
            titles,
            {"friday_1700_included", "saturday_2354_included", "saturday_2355_included"},
        )


class TestAnchorNormalization(unittest.TestCase):
    """Aware anchors are normalized to ET; naive anchors are rejected."""

    def test_aware_anchor_with_offset_is_normalized_to_et(self):
        # 2026-07-21 08:00 UTC is 04:00 ET -> anchor day still Monday.
        anchor_utc = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
        bounds = compute_session_bounds("pre_market", anchor_utc)
        self.assertEqual(bounds.start, _et(2026, 7, 20, 17, 0))
        self.assertEqual(bounds.end, _et(2026, 7, 21, 8, 0))

    def test_naive_anchor_is_rejected(self):
        naive = datetime(2026, 7, 21, 8, 0)
        with self.assertRaises(ValueError):
            compute_session_bounds("pre_market", naive)

    def test_naive_as_of_rejected_in_filter(self):
        headlines = [_h("2026-07-21T12:00:00+00:00")]
        naive = datetime(2026, 7, 21, 8, 0)
        with self.assertRaises(ValueError):
            filter_by_session(headlines, "pre_market", naive)


class TestTimestampHandling(unittest.TestCase):
    """Undated, unparseable, stale, and future entries are dropped."""

    def test_drop_undated_entries(self):
        anchor = _et(2026, 7, 21, 8, 0)
        headlines = [
            Headline(
                outlet="test", title="dated", link="https://example.com/dated",
                published_at="2026-07-21T12:00:00+00:00", summary="",
            ),
            Headline(
                outlet="test", title="undated", link="https://example.com/undated",
                published_at="", summary="",
            ),
        ]
        kept = filter_by_session(headlines, "pre_market", anchor)
        self.assertEqual([h.title for h in kept], ["dated"])

    def test_drop_unparseable_entries(self):
        anchor = _et(2026, 7, 21, 8, 0)
        headlines = [
            _h("2026-07-21T12:00:00+00:00", "parseable"),
            _h("not-a-datetime", "unparseable"),
        ]
        kept = filter_by_session(headlines, "pre_market", anchor)
        self.assertEqual([h.title for h in kept], ["parseable"])

    def test_drop_stale_2024_and_2025(self):
        anchor = _et(2026, 7, 21, 8, 0)
        headlines = [
            _h("2024-09-01T10:00:00+00:00", "from_2024"),
            _h("2025-12-25T10:00:00+00:00", "from_2025"),
            _h("2026-07-21T12:00:00+00:00", "current"),
        ]
        kept = filter_by_session(headlines, "pre_market", anchor)
        self.assertEqual([h.title for h in kept], ["current"])

    def test_drop_future_entries_with_no_15_minute_extension(self):
        anchor = _et(2026, 7, 21, 8, 0)
        at_end = _h("2026-07-21T12:00:00+00:00", "at_end")
        one_second_over = _h("2026-07-21T12:00:01+00:00", "one_second_over")
        kept = filter_by_session([at_end, one_second_over], "pre_market", anchor)
        self.assertEqual([h.title for h in kept], ["at_end"])

    def test_dst_summer_et(self):
        anchor = _et(2026, 7, 21, 8, 0)
        headline = _h("2026-07-21T12:00:00+00:00", "summer_0800_et")
        kept = filter_by_session([headline], "pre_market", anchor)
        self.assertEqual([h.title for h in kept], ["summer_0800_et"])

    def test_dst_winter_et(self):
        anchor = _et(2026, 1, 12, 8, 0)
        # EST (-05:00): 08:00 ET = 13:00 UTC; window start previous day 22:00 UTC.
        headline = _h("2026-01-12T13:00:00+00:00", "winter_0800_et")
        kept = filter_by_session([headline], "pre_market", anchor)
        self.assertEqual([h.title for h in kept], ["winter_0800_et"])

    def test_anchor_end_exactness(self):
        anchor = _et(2026, 7, 21, 9, 45)
        headlines = [
            _h("2026-07-21T12:00:00+00:00", "start_inclusive"),
            _h("2026-07-21T13:45:00+00:00", "end_inclusive"),
            _h("2026-07-21T13:45:00.001+00:00", "just_past_end"),
        ]
        kept = filter_by_session(headlines, "early_market", anchor)
        titles = {h.title for h in kept}
        self.assertEqual(titles, {"start_inclusive", "end_inclusive"})

    def test_unknown_session_raises(self):
        anchor = _et(2026, 7, 21, 8, 0)
        with self.assertRaises(ValueError):
            compute_session_bounds("not_a_session", anchor)


if __name__ == "__main__":
    unittest.main()
