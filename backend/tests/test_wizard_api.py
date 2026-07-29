from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config.models import Settings
from app.config.configuration import ProviderName
from app.config.models import RuntimeMode
from app.main import create_app
from app.secrets.hosted_store import EnvironmentSecretStore
from app.secrets.in_memory_store import InMemorySecretStore


SAME_ORIGIN = {
    "Origin": "http://testserver",
    "Referer": "http://testserver/",
}


def build_client(tmp_path: Path) -> TestClient:
    app = create_app(
        Settings(
            database_path=tmp_path / "reed.db",
            allowed_hosts="testserver",
            local_allowed_origins="http://testserver",
        ),
        secret_store=InMemorySecretStore(),
    )
    return TestClient(app)


def authenticate(client: TestClient) -> dict[str, str]:
    assert client.get("/api/auth/bootstrap").status_code == 204
    response = client.post("/api/auth/session", headers=SAME_ORIGIN)
    assert response.status_code == 200
    return {**SAME_ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def test_initial_state_has_no_defaults_or_secret_fields(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        authenticate(client)
        response = client.get("/api/wizard/state")

    assert response.status_code == 200
    assert response.json() == {
        "provider": None,
        "model": None,
        "endpoint": None,
        "credential_present": False,
        "market_windows": [],
        "rss_source_ids": [],
        "catalog_version": "2026-07-28",
        "complete": False,
    }
    assert "key" not in response.text.lower()
    assert "secret" not in response.text.lower()


def test_mutations_require_same_origin(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        authenticate(client)
        response = client.put(
            "/api/wizard/provider",
            json={"provider": "openrouter", "model": "openai/gpt-4.1-mini"},
        )

    assert response.status_code == 403


def test_configuration_requires_explicit_values_and_enabled_rss(
    tmp_path: Path,
) -> None:
    with build_client(tmp_path) as client:
        headers = authenticate(client)
        provider = client.put(
            "/api/wizard/provider",
            headers=headers,
            json={"provider": "openrouter", "model": "openai/gpt-4.1-mini"},
        )
        credential = client.put(
            "/api/wizard/credential",
            headers=headers,
            json={"credential": "private-provider-value"},
        )
        windows = client.put(
            "/api/wizard/market-windows",
            headers=headers,
            json={"market_windows": ["pre_market", "close"]},
        )
        incomplete = client.post("/api/wizard/complete", headers=headers)
        sources = client.put(
            "/api/wizard/rss-sources",
            headers=headers,
            json={"source_ids": ["federal-reserve"]},
        )
        completed = client.post("/api/wizard/complete", headers=headers)
        state = client.get("/api/wizard/state")

    assert provider.status_code == 200
    assert credential.status_code == 204
    assert windows.status_code == 200
    assert incomplete.status_code == 409
    assert sources.status_code == 200
    assert completed.status_code == 200
    assert state.json()["complete"] is True
    assert state.json()["credential_present"] is True
    assert "private-provider-value" not in state.text


def test_source_selection_rejects_unknown_catalog_entry(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        headers = authenticate(client)
        response = client.put(
            "/api/wizard/rss-sources",
            headers=headers,
            json={"source_ids": ["unknown-source"]},
        )

    assert response.status_code == 422


def test_provider_and_model_must_be_explicit(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        headers = authenticate(client)
        missing_model = client.put(
            "/api/wizard/provider",
            headers=headers,
            json={"provider": "openrouter"},
        )
        missing_provider = client.put(
            "/api/wizard/provider",
            headers=headers,
            json={"model": "openai/gpt-4.1-mini"},
        )

    assert missing_model.status_code == 422
    assert missing_provider.status_code == 422


def test_local_ollama_provider_accepts_loopback_http_endpoint(
    tmp_path: Path,
) -> None:
    with build_client(tmp_path) as client:
        headers = authenticate(client)
        response = client.put(
            "/api/wizard/provider",
            headers=headers,
            json={
                "provider": "ollama",
                "model": "local-model",
                "endpoint": "http://127.0.0.1:11434/v1",
            },
        )

    assert response.status_code == 200
    assert response.json()["endpoint"] == "http://127.0.0.1:11434/v1"


def test_remote_provider_rejects_loopback_endpoint(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        headers = authenticate(client)
        response = client.put(
            "/api/wizard/provider",
            headers=headers,
            json={
                "provider": "openai_compatible",
                "model": "remote-model",
                "endpoint": "http://127.0.0.1:11434/v1",
            },
        )

    assert response.status_code == 422


def test_hosted_state_exposes_presence_but_not_environment_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REED_HOSTED_PROVIDER", "hosted-private-value")
    app = create_app(
        Settings(
            runtime_mode=RuntimeMode.HOSTED,
            database_path=tmp_path / "reed.db",
            allowed_hosts="backend.example",
            hosted_backend_origin="https://backend.example",
            hosted_allowed_origins="https://reader.example",
            hosted_operator_secret="operator-secret",
        ),
        secret_store=EnvironmentSecretStore(
            {ProviderName.OPENROUTER: "REED_HOSTED_PROVIDER"}
        ),
    )
    hosted_headers = {
        "Origin": "https://backend.example",
        "Referer": "https://backend.example/",
    }
    with TestClient(app, base_url="https://backend.example") as client:
        login = client.post(
            "/api/auth/login",
            headers=hosted_headers,
            json={"secret": "operator-secret"},
        )
        headers = {
            **hosted_headers,
            "X-CSRF-Token": login.json()["csrf_token"],
        }
        provider = client.put(
            "/api/wizard/provider",
            headers=headers,
            json={"provider": "openrouter", "model": "openai/gpt-4.1-mini"},
        )
        state = client.get("/api/wizard/state")
        submission = client.put(
            "/api/wizard/credential",
            headers=headers,
            json={"credential": "browser-value"},
        )

    assert provider.status_code == 200
    assert state.json()["credential_present"] is True
    assert "hosted-private-value" not in state.text
    assert submission.status_code == 403
