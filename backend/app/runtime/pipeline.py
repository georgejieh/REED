from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.config.models import RuntimeMode
from app.config.settings_store import SettingsStore
from app.config.source_catalog import SourceCatalog
from app.digests.models import IntakeItem, PublishedDigest
from app.digests.repository import DigestRepository, redact_diagnostic
from app.digests.run import RunStatus
from app.intake.rss import RssIntake, RssIntakeFailure, compute_window_bounds
from app.intake.searxng import SearxngEnricher
from app.providers.factory import build_provider
from app.runtime.generator import DigestGenerator
from app.secrets.base import SecretStore


class PipelineFailure(RuntimeError):
    def __init__(self, run_id: str, diagnostic: str):
        super().__init__(diagnostic)
        self.run_id = run_id
        self.diagnostic = diagnostic


class RuntimePipeline:
    def __init__(
        self,
        *,
        repository: DigestRepository,
        settings_store: SettingsStore,
        source_catalog: SourceCatalog,
        transport: object,
        secret_store: SecretStore,
        runtime_mode: RuntimeMode = RuntimeMode.LOCAL,
        provider_allowed_hosts: tuple[str, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.settings_store = settings_store
        self.source_catalog = source_catalog
        self.transport = transport
        self.secret_store = secret_store
        self.runtime_mode = runtime_mode
        self.provider_allowed_hosts = provider_allowed_hosts
        self.clock = clock or (lambda: datetime.now(UTC))

    def execute(self, run_id: str) -> PublishedDigest:
        try:
            return self._execute(run_id)
        except Exception as error:
            diagnostic = self._diagnostic(error)
            try:
                current = self.repository.get_run(run_id)
                if current.status not in {RunStatus.FAILED, RunStatus.PUBLISHED}:
                    self.repository.fail_run(run_id, diagnostic)
            except Exception as failure_error:
                raise PipelineFailure(
                    run_id,
                    "run failure could not be recorded",
                ) from failure_error
            raise PipelineFailure(run_id, diagnostic) from error

    def _execute(self, run_id: str) -> PublishedDigest:
        configuration = self.settings_store.load()
        if not configuration.setup_complete:
            raise ValueError("runtime configuration is incomplete")
        if configuration.provider is None:
            raise ValueError("provider configuration is missing")

        context = self.repository.get_run_context(run_id)
        if context.run.status is not RunStatus.QUEUED:
            raise ValueError("run is not queued")
        enabled_windows = {window.value for window in configuration.market_windows}
        if context.market_window not in enabled_windows:
            raise ValueError("run market window is not enabled")
        selected = set(configuration.rss_source_ids)
        sources = tuple(
            source
            for source in self.source_catalog.sources
            if source.id in selected
        )
        if len(sources) != len(selected) or not sources:
            raise ValueError("configured RSS source selection is invalid")

        self.repository.set_run_status(run_id, RunStatus.FETCHING)
        start_utc, end_utc = compute_window_bounds(
            context.market_window,
            context.scheduled_time_utc,
        )
        self.repository.set_occurrence_interval(
            run_id,
            start_utc=start_utc,
            end_utc=end_utc,
            max_future_skew_seconds=configuration.max_future_skew_seconds,
        )
        retrieved_at = self.clock()
        try:
            intake = RssIntake(self.transport).collect(
                sources=sources,
                start_utc=start_utc,
                end_utc=end_utc,
                retrieved_at=retrieved_at,
                minimum_items=configuration.rss_minimum_items,
                max_future_skew_seconds=configuration.max_future_skew_seconds,
            )
        except RssIntakeFailure as error:
            self._record_rss_outcomes(run_id, error.source_outcomes)
            raise
        self._record_rss_outcomes(run_id, intake.source_outcomes)
        for item in intake.items:
            self.repository.add_intake_item(
                run_id,
                IntakeItem(
                    id=item.id,
                    title=item.title,
                    url=item.canonical_url,
                    source_name=item.outlet,
                    published_at=item.published_at,
                    feed_id=item.feed_id,
                    source_url=item.source_url,
                    retrieved_at=item.retrieved_at,
                    summary=item.summary,
                    validation_outcome=item.validation_outcome,
                ),
            )

        search_items = ()
        if configuration.search.enabled:
            try:
                search = SearxngEnricher(self.transport).enrich(
                    configuration.search,
                    market_window=context.market_window,
                )
                search_items = search.items
                self.repository.record_source_outcome(
                    run_id,
                    source_type="search",
                    source_id="searxng",
                    source_url=configuration.search.endpoint or "",
                    retrieved_at=self.clock(),
                    state=search.state,
                    item_count=len(search.items),
                )
            except Exception:
                self.repository.record_source_outcome(
                    run_id,
                    source_type="search",
                    source_id="searxng",
                    source_url=configuration.search.endpoint or "",
                    retrieved_at=self.clock(),
                    state="failed",
                    item_count=0,
                    diagnostic="supplemental search failed",
                )

        self.repository.set_run_status(run_id, RunStatus.GENERATING)
        provider = build_provider(
            configuration.provider,
            secret_store=self.secret_store,
            transport=self.transport,
            runtime_mode=self.runtime_mode,
            allowed_remote_hosts=self.provider_allowed_hosts,
        )
        draft = DigestGenerator(provider).generate(
            market_window=context.market_window,
            rss_items=intake.items,
            search_items=search_items,
        )
        self.repository.save_draft(run_id, draft)
        self.repository.set_run_status(run_id, RunStatus.VALIDATING)
        return self.repository.promote_draft(run_id)

    def _record_rss_outcomes(self, run_id: str, outcomes: tuple[object, ...]) -> None:
        for outcome in outcomes:
            self.repository.record_source_outcome(
                run_id,
                source_type="rss",
                source_id=outcome.source_id,
                source_url=outcome.source_url,
                retrieved_at=outcome.retrieved_at,
                state=outcome.state,
                item_count=outcome.item_count,
                diagnostic=outcome.diagnostic,
            )

    @staticmethod
    def _diagnostic(error: Exception) -> str:
        if isinstance(error, RssIntakeFailure):
            return f"rss intake failed: {error.code.value}"
        text = str(error).strip()
        return redact_diagnostic(text or error.__class__.__name__)
