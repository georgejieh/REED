from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.configuration import (
    MarketWindow,
    ProviderConfiguration,
    ProviderName,
    RuntimeConfiguration,
)
from app.config.models import Settings
from app.intake.policy import OutboundResponse
from app.main import create_app
from app.secrets.in_memory_store import InMemorySecretStore


class ApiTransport:
    feed_url = "https://www.federalreserve.gov/feeds/press_all.xml"

    def request(self, method: str, url: str, **_: object) -> OutboundResponse:
        if url == self.feed_url:
            body = (
                "<?xml version='1.0'?><rss><channel><item>"
                "<guid>rss-1</guid><title>Update</title>"
                "<link>https://www.federalreserve.gov/newsevents/pressreleases/test.htm</link>"
                "<pubDate>Tue, 28 Jul 2099 11:30:00 +0000</pubDate>"
                "</item></channel></rss>"
            ).encode()
            return OutboundResponse(
                200,
                {"content-type": "application/rss+xml"},
                body,
                url,
            )
        content = json.dumps(
            {
                "title": "Manual digest",
                "summary": "A sourced update.",
                "items": [
                    {
                        "headline": "Update",
                        "summary": "A source update.",
                        "source_item_id": "rss-1",
                        "market_sentiment": "neutral",
                        "market_relevance": "A Federal Reserve update may inform rate expectations.",
                        "tickers": [],
                    }
                ],
            }
        )
        body = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode()
        return OutboundResponse(
            200,
            {"content-type": "application/json"},
            body,
            url,
        )


def build_client(path: Path, complete: bool) -> TestClient:
    secrets = InMemorySecretStore()
    secrets.set_credential(ProviderName.OPENROUTER, "private-value")
    app = create_app(
        Settings(
            database_path=path,
            allowed_hosts="testserver",
            local_allowed_origins="http://testserver",
        ),
        secret_store=secrets,
    )
    with TestClient(app):
        runtime = app.state.runtime
        runtime.outbound_transport = ApiTransport()
        runtime.rebuild_pipeline()
        runtime.pipeline.clock = lambda: datetime(
            2099, 7, 28, 12, 0, tzinfo=UTC
        )
        runtime.settings_store.save(
            RuntimeConfiguration(
                provider=ProviderConfiguration(
                    provider=ProviderName.OPENROUTER,
                    model="model-a",
                ),
                market_windows=(MarketWindow.PRE_MARKET,),
                rss_source_ids=("federal-reserve",),
                setup_complete=complete,
            )
        )
    return TestClient(app)


def authenticate(client: TestClient) -> dict[str, str]:
    origin = {
        "Origin": "http://testserver",
        "Referer": "http://testserver/",
    }
    assert client.get("/api/auth/bootstrap").status_code == 204
    response = client.post("/api/auth/session", headers=origin)
    assert response.status_code == 200
    return {**origin, "X-CSRF-Token": response.json()["csrf_token"]}


def test_manual_run_requires_same_origin_and_completed_configuration(
    tmp_path: Path,
) -> None:
    incomplete = build_client(tmp_path / "incomplete.db", complete=False)
    with incomplete:
        headers = authenticate(incomplete)
        no_origin = incomplete.post(
            "/api/admin/runs",
            json={"market_window": "pre_market"},
        )
        not_ready = incomplete.post(
            "/api/admin/runs",
            headers=headers,
            json={"market_window": "pre_market"},
        )

    assert no_origin.status_code == 403
    assert not_ready.status_code == 409


def test_manual_run_uses_pipeline_and_returns_published_state(
    tmp_path: Path,
) -> None:
    client = build_client(tmp_path / "complete.db", complete=True)

    with client:
        headers = authenticate(client)
        response = client.post(
            "/api/admin/runs",
            headers=headers,
            json={"market_window": "pre_market"},
        )

    assert response.status_code == 201
    assert response.json()["status"] == "published"
    assert response.json()["published_digest_id"]
