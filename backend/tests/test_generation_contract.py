from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.intake.models import RssItem
from app.runtime.generator import DigestGenerator
from app.runtime.generation_contract import InvalidGeneration, parse_generated_digest


def test_parse_generated_digest_preserves_grounded_market_analysis() -> None:
    content = json.dumps(
        {
            "title": "Oil and chip earnings set the tone",
            "summary": (
                "Oil supply risk and stronger storage earnings are the main "
                "pre-market themes. The evidence points to pressure on energy "
                "costs and a selective recovery in AI infrastructure shares."
            ),
            "items": [
                {
                    "headline": "Saudi oil exports take a longer route",
                    "summary": "Higher transport costs may tighten delivered oil supply.",
                    "source_item_id": "rss-oil",
                    "market_sentiment": "bullish",
                    "market_relevance": "Potentially supports crude prices and energy producers.",
                    "tickers": [],
                },
                {
                    "headline": "Seagate earnings beat expectations",
                    "summary": "The company reported a stronger quarter and shares rose after hours.",
                    "source_item_id": "rss-storage",
                    "market_sentiment": "bullish",
                    "market_relevance": "Signals continued demand for data-storage infrastructure.",
                    "tickers": ["STX"],
                },
            ],
        }
    )

    digest = parse_generated_digest(
        content,
        market_window="pre_market",
        permitted_source_ids={"rss-oil", "rss-storage"},
    )

    assert digest.summary.startswith("Oil supply risk")
    assert digest.items[0].market_sentiment == "bullish"
    assert digest.items[0].market_relevance.startswith("Potentially supports")
    assert digest.items[1].tickers == ["STX"]


def test_parse_generated_digest_rejects_untrusted_ticker_labels() -> None:
    content = json.dumps(
        {
            "title": "Market update",
            "summary": "A concise evidence-grounded market thesis.",
            "items": [
                {
                    "headline": "Company update",
                    "summary": "A validated source update.",
                    "source_item_id": "rss-1",
                    "market_sentiment": "mixed",
                    "market_relevance": "Could affect the named company's sector.",
                    "tickers": ["<script>"],
                }
            ],
        }
    )

    with pytest.raises(InvalidGeneration, match="malformed"):
        parse_generated_digest(
            content,
            market_window="pre_market",
            permitted_source_ids={"rss-1"},
        )


class SequentialProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_digest_generator_retries_once_after_contract_validation_failure() -> None:
    valid_content = json.dumps(
        {
            "title": "Market update",
            "summary": "A concise evidence-grounded market thesis.",
            "items": [
                {
                    "headline": "Company update",
                    "summary": "A validated source update.",
                    "source_item_id": "rss-1",
                    "market_sentiment": "neutral",
                    "market_relevance": "Could affect the named company's sector.",
                    "tickers": [],
                }
            ],
        }
    )
    provider = SequentialProvider(["not-json", valid_content])
    item = RssItem(
        id="rss-1",
        feed_id="feed-1",
        outlet="Example",
        title="Company update",
        canonical_url="https://example.com/story",
        published_at=datetime(2026, 7, 29, tzinfo=UTC),
        retrieved_at=datetime(2026, 7, 29, tzinfo=UTC),
        source_url="https://example.com/feed.xml",
        summary="A validated source update.",
    )

    digest = DigestGenerator(provider).generate(
        market_window="pre_market",
        rss_items=(item,),
    )

    assert digest.title == "Market update"
    assert len(provider.prompts) == 2
    assert "previous response was rejected" in provider.prompts[1]


def rss_item(item_id: str = "rss-1") -> RssItem:
    return RssItem(
        id=item_id,
        feed_id="feed-1",
        outlet="Example",
        title="Company update",
        canonical_url="https://example.com/story",
        published_at=datetime(2026, 7, 29, tzinfo=UTC),
        retrieved_at=datetime(2026, 7, 29, tzinfo=UTC),
        source_url="https://example.com/feed.xml",
        summary="A validated source update.",
    )


def valid_digest_content(source_item_id: str = "rss-1") -> str:
    return json.dumps(
        {
            "title": "Market update",
            "summary": "A concise evidence-grounded market thesis.",
            "items": [
                {
                    "headline": "Company update",
                    "summary": "A validated source update.",
                    "source_item_id": source_item_id,
                    "market_sentiment": "neutral",
                    "market_relevance": "Could affect the named company's sector.",
                    "tickers": [],
                }
            ],
        }
    )


def test_retry_repairs_from_identical_evidence_without_replaying_rejected_content() -> None:
    rejected = '<<<rejected-marker>>> {"title": "broken"'
    provider = SequentialProvider([rejected, valid_digest_content()])

    digest = DigestGenerator(provider).generate(
        market_window="pre_market",
        rss_items=(rss_item(),),
    )

    assert digest.title == "Market update"
    assert len(provider.prompts) == 2
    first_request, repair_request = provider.prompts
    # The repair request is built from the identical bounded evidence.
    assert repair_request.startswith(first_request)
    # The rejected model output is never replayed into the repair prompt.
    assert rejected not in repair_request
    assert "<<<rejected-marker>>>" not in repair_request
    # The repair instruction names only the safe category.
    assert "malformed_json" in repair_request
    assert "empty" not in repair_request.removeprefix(first_request)
    assert "untrusted_reference" not in repair_request


@pytest.mark.parametrize(
    ("rejected", "category"),
    [
        ("not-json", "malformed_json"),
        ('{"title": 42, "summary": "x", "items": []}', "malformed_json"),
        (valid_digest_content("rss-not-supplied"), "untrusted_reference"),
    ],
)
def test_first_failure_category_drives_repair_and_recovers(
    rejected: str,
    category: str,
) -> None:
    provider = SequentialProvider([rejected, valid_digest_content()])

    digest = DigestGenerator(provider).generate(
        market_window="pre_market",
        rss_items=(rss_item(),),
    )

    assert digest.title == "Market update"
    assert len(provider.prompts) == 2
    first_request, repair_request = provider.prompts
    assert repair_request.startswith(first_request)
    assert rejected.strip() not in repair_request
    repair_suffix = repair_request[len(first_request):]
    assert category in repair_suffix


def test_empty_first_failure_category_drives_repair_and_recovers() -> None:
    # Provider-level transports reject empty content before it reaches the
    # generator, so the empty category is exercised directly at the parser
    # boundary where an empty response is classified.
    with pytest.raises(InvalidGeneration) as excinfo:
        parse_generated_digest(
            "   \n  ",
            market_window="pre_market",
            permitted_source_ids={"rss-1"},
        )

    assert excinfo.value.category == "empty"
    assert excinfo.value.retry_exhausted is False


@pytest.mark.parametrize(
    ("first", "second", "category"),
    [
        ("not-json", "still-not-json", "malformed_json"),
        ("not-json", "", "empty"),
        (
            "not-json",
            valid_digest_content("rss-not-supplied"),
            "untrusted_reference",
        ),
    ],
)
def test_second_failure_rethrows_category_with_retry_exhausted(
    first: str,
    second: str,
    category: str,
) -> None:
    provider = SequentialProvider([first, second])

    with pytest.raises(InvalidGeneration) as excinfo:
        DigestGenerator(provider).generate(
            market_window="pre_market",
            rss_items=(rss_item(),),
        )

    error = excinfo.value
    # Exactly one repair attempt, then the second failure propagates.
    assert len(provider.prompts) == 2
    assert error.category == category
    assert error.retry_exhausted is True
    # Message compatibility is retained for existing callers.
    assert str(error) in {
        "generation content is empty",
        "generation content is malformed",
        "generation references intake evidence that was not supplied",
    }


def test_first_failure_is_marked_not_exhausted() -> None:
    with pytest.raises(InvalidGeneration) as excinfo:
        parse_generated_digest(
            "not-json",
            market_window="pre_market",
            permitted_source_ids={"rss-1"},
        )

    assert excinfo.value.category == "malformed_json"
    assert excinfo.value.retry_exhausted is False
