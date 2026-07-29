from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config.configuration import (
    MarketWindow,
    ProviderConfiguration,
    ProviderName,
    RuntimeConfiguration,
    SearchConfiguration,
)
from app.config.settings_store import SettingsStore
from app.config.source_catalog import RssSource, SourceCatalog
from app.db.connection import Database
from app.db.migrations import migrate
from app.digests.repository import DigestRepository
from app.digests.run import RunStatus
from app.intake.policy import OutboundResponse
from app.runtime.pipeline import RuntimePipeline
from app.secrets.in_memory_store import InMemorySecretStore


class PipelineTransport:
    def __init__(self, feed_url: str) -> None:
        self.feed_url = feed_url
        self.provider_content = self.valid_content()
        self.urls: list[str] = []

    @staticmethod
    def valid_content() -> str:
        return json.dumps(
            {
                "title": "Market update",
                "summary": "A sourced update.",
                "items": [
                    {
                        "headline": "Opening update",
                        "summary": "Markets moved.",
                        "source_item_id": "rss-1",
                        "market_sentiment": "neutral",
                        "market_relevance": "Provides a validated market update.",
                        "tickers": [],
                    }
                ],
            }
        )

    def request(self, method: str, url: str, **_: object) -> OutboundResponse:
        self.urls.append(url)
        if url == self.feed_url:
            body = (
                "<?xml version='1.0'?><rss><channel><item>"
                "<guid>rss-1</guid><title>Opening update</title>"
                "<link>https://news.example.com/story</link>"
                "<pubDate>Tue, 28 Jul 2026 11:30:00 +0000</pubDate>"
                "<description>Markets moved.</description>"
                "</item></channel></rss>"
            ).encode()
            return OutboundResponse(
                200,
                {"content-type": "application/rss+xml"},
                body,
                url,
            )
        body = json.dumps(
            {"choices": [{"message": {"content": self.provider_content}}]}
        ).encode()
        return OutboundResponse(
            200,
            {"content-type": "application/json"},
            body,
            url,
        )


def build_pipeline(
    path: Path,
) -> tuple[RuntimePipeline, DigestRepository, PipelineTransport]:
    database = Database(path)
    migrate(database)
    repository = DigestRepository(database)
    store = SettingsStore(database)
    feed_url = "https://feeds.example.com/feed.xml"
    catalog = SourceCatalog(
        sources=(RssSource(id="feed", name="Feed", url=feed_url),)
    )
    store.save(
        RuntimeConfiguration(
            provider=ProviderConfiguration(
                provider=ProviderName.OPENROUTER,
                model="model-a",
            ),
            market_windows=(MarketWindow.PRE_MARKET,),
            rss_source_ids=("feed",),
            rss_minimum_items=1,
            max_future_skew_seconds=60,
            search=SearchConfiguration(),
            setup_complete=True,
        )
    )
    secrets = InMemorySecretStore()
    secrets.set_credential(ProviderName.OPENROUTER, "private-value")
    transport = PipelineTransport(feed_url)
    pipeline = RuntimePipeline(
        repository=repository,
        settings_store=store,
        source_catalog=catalog,
        transport=transport,
        secret_store=secrets,
        clock=lambda: datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )
    return pipeline, repository, transport


def create_run(repository: DigestRepository, minute: int) -> str:
    run = repository.create_run(
        market_window="pre_market",
        scheduled_time_utc=datetime(2026, 7, 28, 12, minute, tzinfo=UTC),
        claim_owner=f"manual-{minute}",
        claim_expiry=datetime(2099, 7, 28, 13, 0, tzinfo=UTC),
    )
    return run.id


def test_pipeline_publishes_only_fully_validated_generated_content(
    tmp_path: Path,
) -> None:
    pipeline, repository, _ = build_pipeline(tmp_path / "reed.db")
    run_id = create_run(repository, 0)

    digest = pipeline.execute(run_id)

    assert repository.get_run(run_id).status is RunStatus.PUBLISHED
    assert digest.title == "Market update"
    assert digest.items[0].source_url == "https://news.example.com/story"
    assert digest.items[0].market_sentiment == "neutral"
    assert digest.items[0].market_relevance == "Provides a validated market update."
    assert digest.items[0].tickers == []
    context = repository.get_run_context(run_id)
    assert context.window_start_utc == datetime(
        2026, 7, 27, 21, 0, tzinfo=UTC
    )
    assert context.window_end_utc == datetime(
        2026, 7, 28, 12, 0, tzinfo=UTC
    )
    assert context.max_future_skew_seconds == 60
    with repository.database.connect() as connection:
        evidence = connection.execute(
            """
            SELECT feed_id, source_url, retrieved_at, validation_outcome
            FROM intake_items WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    assert dict(evidence) == {
        "feed_id": "feed",
        "source_url": "https://feeds.example.com/feed.xml",
        "retrieved_at": "2026-07-28T12:00:00+00:00",
        "validation_outcome": "valid",
    }


@pytest.mark.parametrize("content", ["", "not-json", "{}", "[]"])
def test_empty_or_malformed_generation_fails_and_preserves_last_digest(
    tmp_path: Path,
    content: str,
) -> None:
    pipeline, repository, transport = build_pipeline(tmp_path / "reed.db")
    first_run = create_run(repository, 0)
    first_digest = pipeline.execute(first_run)
    second_run = create_run(repository, 1)
    transport.provider_content = content

    with pytest.raises(Exception):
        pipeline.execute(second_run)

    assert repository.get_run(second_run).status is RunStatus.FAILED
    assert [digest.id for digest in repository.list_published()] == [first_digest.id]


def test_search_never_runs_when_rss_minimum_is_unmet(tmp_path: Path) -> None:
    pipeline, repository, transport = build_pipeline(tmp_path / "reed.db")
    configuration = pipeline.settings_store.load().model_copy(
        update={
            "rss_minimum_items": 2,
            "search": SearchConfiguration(
                enabled=True,
                endpoint="https://search.example.com",
                query_templates=("market open",),
            ),
        }
    )
    pipeline.settings_store.save(configuration)
    run_id = create_run(repository, 0)

    with pytest.raises(Exception):
        pipeline.execute(run_id)

    assert repository.get_run(run_id).status is RunStatus.FAILED
    assert all("search.example.com" not in url for url in transport.urls)
