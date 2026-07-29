from __future__ import annotations

from pathlib import Path

import pytest

from app.config.configuration import (
    MarketWindow,
    ProviderConfiguration,
    ProviderName,
    RuntimeConfiguration,
)
from app.config.models import Settings
from app.config.settings_store import SettingsStore
from app.config.source_catalog import RssSource, SourceCatalog
from app.db.connection import Database
from app.db.migrations import migrate
from app.intake.policy import UnsafeOutboundUrl
from app.runtime.service import RuntimeService
from app.secrets.hosted_store import EnvironmentSecretStore
from app.secrets.in_memory_store import InMemorySecretStore
from app.secrets.keyring_store import KeyringSecretStore


def build_store(path: Path) -> tuple[Database, SettingsStore]:
    database = Database(path)
    migrate(database)
    return database, SettingsStore(database)


def test_typed_settings_round_trip_contains_no_credential(tmp_path: Path) -> None:
    database, store = build_store(tmp_path / "reed.db")
    configuration = RuntimeConfiguration(
        provider=ProviderConfiguration(
            provider=ProviderName.OPENROUTER,
            model="openai/gpt-4.1-mini",
        ),
        market_windows=(MarketWindow.PRE_MARKET, MarketWindow.CLOSE),
        rss_source_ids=("federal-reserve",),
    )

    store.save(configuration)

    assert store.load() == configuration
    with database.connect() as connection:
        persisted = connection.execute(
            "SELECT value_json FROM settings WHERE key = 'runtime_configuration'"
        ).fetchone()[0]
    assert "credential" not in persisted
    assert "api_key" not in persisted


def test_in_memory_secret_store_never_enters_settings(tmp_path: Path) -> None:
    database, store = build_store(tmp_path / "reed.db")
    secrets = InMemorySecretStore()
    secrets.set_credential(ProviderName.OPENROUTER, "private-value")

    assert secrets.has_credential(ProviderName.OPENROUTER)
    assert secrets.get_credential(ProviderName.OPENROUTER) == "private-value"
    assert store.load() == RuntimeConfiguration()
    with database.connect() as connection:
        rows = connection.execute("SELECT value_json FROM settings").fetchall()
    assert all("private-value" not in row[0] for row in rows)


def test_production_configuration_cannot_select_in_memory_secret_store() -> None:
    assert "secret_store" not in Settings.model_fields

    local = object.__new__(RuntimeService)
    local.settings = Settings()
    assert isinstance(local._default_secret_store(), KeyringSecretStore)

    hosted = object.__new__(RuntimeService)
    hosted.settings = Settings(
        runtime_mode="hosted",
        hosted_backend_origin="https://backend.example",
        hosted_allowed_origins="https://reader.example",
        hosted_operator_secret="operator-secret",
    )
    assert isinstance(
        hosted._default_secret_store(),
        EnvironmentSecretStore,
    )


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


def test_keyring_adapter_uses_profile_and_provider_identifiers() -> None:
    backend = FakeKeyring()
    store = KeyringSecretStore("desktop-profile", backend=backend)

    store.set_credential(ProviderName.OPENROUTER, "private-value")

    assert backend.values == {
        ("reed/desktop-profile", "provider/openrouter"): "private-value"
    }
    assert store.get_credential(ProviderName.OPENROUTER) == "private-value"


def test_hosted_environment_store_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REED_OPENROUTER_SECRET", "hosted-private-value")
    store = EnvironmentSecretStore(
        {ProviderName.OPENROUTER: "REED_OPENROUTER_SECRET"}
    )

    assert store.has_credential(ProviderName.OPENROUTER)
    assert store.get_credential(ProviderName.OPENROUTER) == "hosted-private-value"
    with pytest.raises(PermissionError, match="deployment environment"):
        store.set_credential(ProviderName.OPENROUTER, "replacement")


def test_source_catalog_rejects_unsafe_builtin_url() -> None:
    with pytest.raises(UnsafeOutboundUrl):
        SourceCatalog(
            sources=(
                RssSource(
                    id="unsafe",
                    name="Unsafe",
                    url="http://169.254.169.254/latest",
                ),
            )
        )
