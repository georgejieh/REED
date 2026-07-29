from __future__ import annotations

import ssl
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.configuration import MarketWindow
from app.config.models import Settings
from app.config.source_catalog import RssSource, SourceCatalog
from app.intake.policy import OutboundResponse, create_pinned_tls_context
from app.main import create_app
from app.runtime.pipeline import PipelineFailure, RuntimePipeline
from app.secrets.in_memory_store import InMemorySecretStore


ORIGIN = "http://testserver"
ORIGIN_HEADERS = {"Origin": ORIGIN, "Referer": f"{ORIGIN}/"}


def secured_client(tmp_path: Path):
    app = create_app(
        Settings(
            database_path=tmp_path / "reed.db",
            allowed_hosts="testserver",
            local_allowed_origins=ORIGIN,
            scheduler_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )
    client = TestClient(app)
    return app, client


def establish_session(client: TestClient) -> str:
    assert client.get("/api/auth/bootstrap").status_code == 204
    response = client.post("/api/auth/session", headers=ORIGIN_HEADERS)
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_safe_public_endpoint_contracts(tmp_path: Path) -> None:
    _, client = secured_client(tmp_path)
    with client:
        latest = client.get("/api/digests/latest")
        sessions = client.get("/api/sessions")
        canonical = client.get("/api/runtime/status")
        compatibility = client.get("/api/runtime-status")

    assert latest.status_code == 404
    assert sessions.status_code == 200
    assert {item["id"] for item in sessions.json()} == {
        "pre_market",
        "early_market",
        "midday",
        "close",
        "weekend_recap",
    }
    assert canonical.status_code == 200
    assert canonical.json() == compatibility.json()
    assert "diagnostic" not in canonical.text


class FeedTransport:
    def __init__(self, invalid_url: str | None = None):
        self.invalid_url = invalid_url

    def request(self, method: str, url: str, **_: object) -> OutboundResponse:
        assert method == "GET"
        if url == self.invalid_url:
            body = b"<rss><channel><item>"
        else:
            body = (
                b"<?xml version='1.0'?><rss><channel><item>"
                b"<guid>one</guid><title>One item</title>"
                b"<link>https://news.example.com/one</link>"
                b"<pubDate>Tue, 28 Jul 2026 12:00:00 +0000</pubDate>"
                b"</item></channel></rss>"
            )
        return OutboundResponse(
            200,
            {"content-type": "application/rss+xml"},
            body,
            url,
        )


def test_explicit_rss_catalog_validation_persists_safe_results(
    tmp_path: Path,
) -> None:
    app, client = secured_client(tmp_path)
    good = "https://feeds.example.com/good.xml"
    bad = "https://feeds.example.com/bad.xml"
    app.state.runtime.source_catalog = SourceCatalog(
        sources=(
            RssSource("good", "Good feed", good),
            RssSource("bad", "Bad feed", bad),
        )
    )
    app.state.runtime.outbound_transport = FeedTransport(invalid_url=bad)
    with client:
        csrf = establish_session(client)
        response = client.post(
            "/api/admin/rss-catalog/validate",
            headers={**ORIGIN_HEADERS, "X-CSRF-Token": csrf},
        )
        persisted = app.state.runtime.repository.latest_catalog_validation()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["valid"] is False
    assert response.json()["results"] == [
        {"source_id": "good", "valid": True, "item_count": 1},
        {"source_id": "bad", "valid": False, "item_count": 0},
    ]
    assert persisted is not None
    assert persisted["valid"] is False
    assert bad not in str(persisted)


def test_catalog_validation_is_opt_in_at_startup(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "startup.db",
            allowed_hosts="testserver",
            local_allowed_origins=ORIGIN,
            validate_rss_catalog_on_startup=True,
            scheduler_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )
    feed_url = "https://feeds.example.com/good.xml"
    app.state.runtime.source_catalog = SourceCatalog(
        sources=(RssSource("good", "Good feed", feed_url),)
    )
    app.state.runtime.outbound_transport = FeedTransport()
    with TestClient(app):
        persisted = app.state.runtime.repository.latest_catalog_validation()

    assert persisted is not None
    assert persisted["valid"] is True


def test_pinned_https_context_requires_hostname_and_certificates() -> None:
    context = create_pinned_tls_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_provider_error_diagnostic_redacts_secrets_and_url_queries() -> None:
    diagnostic = RuntimePipeline._diagnostic(
        ValueError(
            "token=private-value "
            "https://provider.example/failure?api_key=private-value"
        )
    )

    assert "private-value" not in diagnostic
    assert "?" not in diagnostic
    assert "[redacted]" in diagnostic


def test_manual_run_renews_claim_during_pipeline_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app, client = secured_client(tmp_path)
    runtime = app.state.runtime
    renewed: list[dict[str, object]] = []

    class RecordingHeartbeat:
        def __init__(self, **kwargs: object):
            renewed.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("app.api.admin.ClaimHeartbeat", RecordingHeartbeat)
    with client:
        runtime.settings_store.save(
            runtime.settings_store.load().model_copy(
                update={
                    "setup_complete": True,
                    "market_windows": (MarketWindow.PRE_MARKET,),
                    "rss_source_ids": ("federal-reserve",),
                }
            )
        )
        monkeypatch.setattr(
            runtime.pipeline,
            "execute",
            lambda run_id: (_ for _ in ()).throw(
                PipelineFailure(run_id, "stop after heartbeat")
            ),
        )
        csrf = establish_session(client)
        response = client.post(
            "/api/admin/runs",
            headers={**ORIGIN_HEADERS, "X-CSRF-Token": csrf},
            json={"market_window": "pre_market"},
        )

    assert response.status_code == 502
    assert len(renewed) == 1
    assert renewed[0]["claim_ttl"] == timedelta(minutes=15)
    assert renewed[0]["owner"].startswith("manual:")
    assert isinstance(renewed[0]["clock"](), datetime)
