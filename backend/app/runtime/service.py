from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.config.configuration import ProviderName
from app.config.models import RuntimeMode, Settings
from app.config.settings_store import SettingsStore
from app.config.source_catalog import SourceCatalog
from app.db.connection import Database
from app.db.migrations import migrate
from app.db.reconciliation import reconcile_startup
from app.digests.repository import DigestRepository
from app.intake.policy import OutboundUrlPolicy, SafeOutboundTransport
from app.runtime.pipeline import RuntimePipeline
from app.runtime.scheduler import SchedulerCoordinator
from app.security import configured_values
from app.secrets.base import SecretStore
from app.secrets.hosted_store import EnvironmentSecretStore
from app.secrets.keyring_store import KeyringSecretStore


class RuntimeService:
    def __init__(
        self,
        settings: Settings,
        secret_store: SecretStore | None = None,
    ):
        self.settings = settings
        self.database = Database(settings.database_path)
        self.repository = DigestRepository(self.database)
        self.settings_store = SettingsStore(self.database)
        self.url_policy = OutboundUrlPolicy()
        self.source_catalog = SourceCatalog(policy=self.url_policy)
        self.outbound_transport = SafeOutboundTransport(policy=self.url_policy)
        self.secret_store = secret_store or self._default_secret_store()
        self.rebuild_pipeline()
        self.scheduler = SchedulerCoordinator(
            repository=self.repository,
            pipeline=self.pipeline,
            configuration_loader=self.settings_store.load,
            owner=f"runtime:{uuid4()}",
        )
        self.settings_store.on_save = self._configuration_saved

    def rebuild_pipeline(self) -> None:
        self.pipeline = RuntimePipeline(
            repository=self.repository,
            settings_store=self.settings_store,
            source_catalog=self.source_catalog,
            transport=self.outbound_transport,
            secret_store=self.secret_store,
            runtime_mode=self.settings.runtime_mode,
            provider_allowed_hosts=configured_values(
                self.settings.provider_allowed_hosts
            ),
        )

    def _default_secret_store(self) -> SecretStore:
        if self.settings.runtime_mode is RuntimeMode.HOSTED:
            return EnvironmentSecretStore(
                {
                    ProviderName.OPENROUTER: "REED_OPENROUTER_CREDENTIAL",
                    ProviderName.OLLAMA: "REED_OLLAMA_CREDENTIAL",
                    ProviderName.OPENAI_COMPATIBLE: (
                        "REED_OPENAI_COMPATIBLE_CREDENTIAL"
                    ),
                }
            )
        return KeyringSecretStore(self.settings.local_profile_id)

    def start(self) -> None:
        migrate(self.database)
        reconcile_startup(self.repository)
        if self.settings.validate_rss_catalog_on_startup:
            self.validate_source_catalog()
        if self.settings.scheduler_enabled:
            self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()

    def reload_scheduler(self) -> None:
        if self.settings.scheduler_enabled:
            self.scheduler.reload()

    def validate_source_catalog(self) -> dict[str, object]:
        results = self.source_catalog.validate_feeds(self.outbound_transport)
        return self.repository.record_catalog_validation(
            catalog_version=self.source_catalog.version,
            validated_at=datetime.now(UTC),
            results=[
                {
                    "source_id": result.source_id,
                    "valid": result.valid,
                    "item_count": result.item_count,
                }
                for result in results
            ],
        )

    def _configuration_saved(self, _: object) -> None:
        self.reload_scheduler()
