from __future__ import annotations

import json

import pytest

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
