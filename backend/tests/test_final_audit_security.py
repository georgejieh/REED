from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.config.models import RuntimeMode, Settings
from app.main import create_app
from app.secrets.in_memory_store import InMemorySecretStore


LOCAL_ORIGIN = "http://testserver"
LOCAL_HEADERS = {
    "Origin": LOCAL_ORIGIN,
    "Referer": f"{LOCAL_ORIGIN}/",
}


def local_app(tmp_path: Path):
    return create_app(
        Settings(
            database_path=tmp_path / "reed.db",
            allowed_hosts="testserver",
            local_allowed_origins=LOCAL_ORIGIN,
            scheduler_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )


def establish_session(client: TestClient) -> str:
    bootstrap = client.get("/api/auth/bootstrap")
    assert bootstrap.status_code == 204
    assert "reed_bootstrap=" in bootstrap.headers["set-cookie"]
    assert "HttpOnly" in bootstrap.headers["set-cookie"]
    assert "SameSite=strict" in bootstrap.headers["set-cookie"]
    exchange = client.post("/api/auth/session", headers=LOCAL_HEADERS)
    assert exchange.status_code == 200
    assert "reed_session=" in exchange.headers["set-cookie"]
    assert "HttpOnly" in exchange.headers["set-cookie"]
    assert "SameSite=strict" in exchange.headers["set-cookie"]
    return exchange.json()["csrf_token"]


def test_local_security_rejects_host_origin_referer_and_missing_csrf(
    tmp_path: Path,
) -> None:
    app = local_app(tmp_path)
    with TestClient(app) as client:
        hostile_host = client.get(
            "/api/health",
            headers={"host": "attacker.example"},
        )
        csrf = establish_session(client)
        hostile_origin = client.put(
            "/api/wizard/provider",
            headers={
                "Origin": "https://attacker.example",
                "Referer": f"{LOCAL_ORIGIN}/",
                "X-CSRF-Token": csrf,
            },
            json={"provider": "openrouter", "model": "model"},
        )
        hostile_referer = client.put(
            "/api/wizard/provider",
            headers={
                "Origin": LOCAL_ORIGIN,
                "Referer": "https://attacker.example/",
                "X-CSRF-Token": csrf,
            },
            json={"provider": "openrouter", "model": "model"},
        )
        missing_csrf = client.put(
            "/api/wizard/provider",
            headers=LOCAL_HEADERS,
            json={"provider": "openrouter", "model": "model"},
        )

    assert hostile_host.status_code == 400
    assert hostile_origin.status_code == 403
    assert hostile_referer.status_code == 403
    assert missing_csrf.status_code == 403


def test_local_initial_html_sets_bootstrap_without_exposing_it(
    tmp_path: Path,
) -> None:
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text(
        "<!doctype html><title>REED</title>",
        encoding="utf-8",
    )
    app = create_app(
        Settings(
            database_path=tmp_path / "reed.db",
            dashboard_path=dashboard,
            allowed_hosts="testserver",
            local_allowed_origins=LOCAL_ORIGIN,
            scheduler_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )
    with TestClient(app) as client:
        response = client.get("/")

    cookie = response.headers["set-cookie"]
    capability = cookie.split("reed_bootstrap=", 1)[1].split(";", 1)[0]
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in cookie
    assert capability not in response.text


def test_local_mode_refuses_non_loopback_binding() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(bind_host="0.0.0.0")


def test_hosted_mode_refuses_wildcard_cors_origin() -> None:
    with pytest.raises(ValidationError, match="exact origins"):
        Settings(
            runtime_mode=RuntimeMode.HOSTED,
            hosted_backend_origin="https://backend.example",
            hosted_operator_secret="operator-secret",
            hosted_allowed_origins="*",
        )


def test_hosted_mode_refuses_empty_cors_origin_allowlist() -> None:
    with pytest.raises(ValidationError, match="hosted CORS origin allowlist"):
        Settings(
            runtime_mode=RuntimeMode.HOSTED,
            hosted_backend_origin="https://backend.example",
            hosted_operator_secret="operator-secret",
            hosted_allowed_origins=" , ",
        )


def test_tls_bootstrap_cookie_is_strict_http_only_secure_and_not_cacheable(
    tmp_path: Path,
) -> None:
    app = local_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/auth/bootstrap")

    cookie = response.headers["set-cookie"]
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie


def test_bootstrap_exchange_alone_allows_missing_origin_with_exact_host(
    tmp_path: Path,
) -> None:
    app = local_app(tmp_path)
    with TestClient(app) as client:
        bootstrap = client.get("/api/auth/bootstrap")
        exchange = client.post("/api/auth/session")
        csrf = exchange.json()["csrf_token"]
        wizard = client.put(
            "/api/wizard/provider",
            headers={"X-CSRF-Token": csrf},
            json={"provider": "openrouter", "model": "model"},
        )
        manual_run = client.post(
            "/api/admin/runs",
            headers={"X-CSRF-Token": csrf},
            json={"market_window": "pre_market"},
        )

    assert bootstrap.status_code == 204
    assert exchange.status_code == 200
    assert wizard.status_code == 403
    assert manual_run.status_code == 403


def test_bootstrap_exchange_without_origin_rejects_unapproved_host(
    tmp_path: Path,
) -> None:
    app = local_app(tmp_path)
    with TestClient(app) as client:
        client.get("/api/auth/bootstrap")
        response = client.post(
            "/api/auth/session",
            headers={"Host": "attacker.example"},
        )

    assert response.status_code == 400


def test_bootstrap_is_required_short_lived_and_one_time(tmp_path: Path) -> None:
    app = local_app(tmp_path)
    with TestClient(app) as client:
        absent = client.post("/api/auth/session", headers=LOCAL_HEADERS)
        client.get("/api/auth/bootstrap")
        bootstrap_cookie = client.cookies.get("reed_bootstrap")
        app.state.security.bootstrap_expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        expired = client.post("/api/auth/session", headers=LOCAL_HEADERS)
        client.cookies.set("reed_bootstrap", bootstrap_cookie)
        app.state.security.bootstrap_expires_at = datetime.now(UTC) + timedelta(
            seconds=30
        )
        first = client.post("/api/auth/session", headers=LOCAL_HEADERS)
        client.cookies.set("reed_bootstrap", bootstrap_cookie)
        reused = client.post("/api/auth/session", headers=LOCAL_HEADERS)

    assert absent.status_code == 403
    assert expired.status_code == 403
    assert first.status_code == 200
    assert reused.status_code == 403
    combined = absent.text + expired.text + reused.text
    assert bootstrap_cookie not in combined


def test_expired_session_and_sensitive_responses_are_not_cacheable(
    tmp_path: Path,
) -> None:
    app = local_app(tmp_path)
    with TestClient(app) as client:
        csrf = establish_session(client)
        app.state.security.expire_all_sessions()
        expired = client.put(
            "/api/wizard/credential",
            headers={**LOCAL_HEADERS, "X-CSRF-Token": csrf},
            json={"credential": "do-not-reflect-this"},
        )
        bootstrap = client.get("/api/auth/bootstrap")
        wizard = client.get(
            "/api/wizard/state",
            headers=LOCAL_HEADERS,
        )

    assert expired.status_code == 403
    assert expired.headers["cache-control"] == "no-store"
    assert bootstrap.headers["cache-control"] == "no-store"
    assert wizard.headers["cache-control"] == "no-store"
    assert "do-not-reflect-this" not in expired.text
    for response in (expired, bootstrap):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "same-origin"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers[
            "content-security-policy"
        ]


def hosted_app(tmp_path: Path):
    return create_app(
        Settings(
            runtime_mode=RuntimeMode.HOSTED,
            database_path=tmp_path / "hosted.db",
            allowed_hosts="backend.example",
            hosted_backend_origin="https://backend.example",
            hosted_allowed_origins=(
                "https://georgejieh.github.io,http://localhost:5173"
            ),
            hosted_operator_secret="server-side-admin-secret",
            auth_rate_limit_attempts=2,
            scheduler_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )


def test_hosted_cors_and_backend_origin_authentication_boundary(
    tmp_path: Path,
) -> None:
    app = hosted_app(tmp_path)
    with TestClient(app, base_url="https://backend.example") as client:
        approved = client.options(
            "/api/digests",
            headers={
                "Origin": "https://georgejieh.github.io",
                "Access-Control-Request-Method": "GET",
            },
        )
        rejected = client.options(
            "/api/digests",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        pages_login = client.post(
            "/api/auth/login",
            headers={
                "Origin": "https://georgejieh.github.io",
                "Referer": "https://georgejieh.github.io/",
            },
            json={"secret": "server-side-admin-secret"},
        )
        backend_headers = {
            "Origin": "https://backend.example",
            "Referer": "https://backend.example/",
        }
        login = client.post(
            "/api/auth/login",
            headers=backend_headers,
            json={"secret": "server-side-admin-secret"},
        )
        csrf = login.json()["csrf_token"]
        protected = client.post(
            "/api/admin/runs",
            headers={**backend_headers, "X-CSRF-Token": csrf},
            json={"market_window": "pre_market"},
        )
        session_value = client.cookies.get("reed_session")
        client.cookies.set("reed_session", f"{session_value}x")
        tampered = client.post(
            "/api/admin/runs",
            headers={**backend_headers, "X-CSRF-Token": csrf},
            json={"market_window": "pre_market"},
        )

    assert approved.status_code == 200
    assert approved.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert approved.headers["access-control-allow-origin"] == (
        "https://georgejieh.github.io"
    )
    assert approved.headers["access-control-allow-credentials"] == "true"
    assert "*" not in approved.headers["access-control-allow-origin"]
    assert "access-control-allow-origin" not in rejected.headers
    assert pages_login.status_code == 403
    assert login.status_code == 200
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "server-side-admin-secret" not in login.text
    assert "." in session_value
    assert protected.status_code == 409
    assert tampered.status_code == 403


def test_hosted_login_is_rate_limited_and_does_not_reflect_secret(
    tmp_path: Path,
) -> None:
    app = hosted_app(tmp_path)
    headers = {
        "Origin": "https://backend.example",
        "Referer": "https://backend.example/",
    }
    with TestClient(app, base_url="https://backend.example") as client:
        responses = [
            client.post(
                "/api/auth/login",
                headers=headers,
                json={"secret": "wrong-secret-value"},
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [401, 401, 429]
    assert all("wrong-secret-value" not in response.text for response in responses)


def test_sensitive_validation_error_does_not_echo_submitted_secret(
    tmp_path: Path,
) -> None:
    app = hosted_app(tmp_path)
    secret = "sensitive-value-" * 1200
    headers = {
        "Origin": "https://backend.example",
        "Referer": "https://backend.example/",
    }
    with TestClient(app, base_url="https://backend.example") as client:
        response = client.post(
            "/api/auth/login",
            headers=headers,
            json={"secret": secret},
        )

    assert response.status_code == 422
    assert "sensitive-value" not in response.text
    assert response.headers["cache-control"] == "no-store"
