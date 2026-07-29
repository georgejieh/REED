from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config.configuration import (
    MarketWindow,
    RuntimeConfiguration,
    SchedulerConfiguration,
)
from app.config.models import Settings
from app.db.migrations import migrate
from app.runtime.scheduler import (
    SchedulerCoordinator,
    coalesced_occurrences,
    scheduled_occurrence,
)
from app.runtime.service import RuntimeService
from app.secrets.in_memory_store import InMemorySecretStore


def test_occurrence_identity_tracks_iana_timezone_across_dst_transitions() -> None:
    before_spring = scheduled_occurrence(
        "pre_market",
        datetime(2026, 3, 6, 15, 0, tzinfo=UTC),
        "America/New_York",
    )
    after_spring = scheduled_occurrence(
        "pre_market",
        datetime(2026, 3, 9, 15, 0, tzinfo=UTC),
        "America/New_York",
    )
    before_fall = scheduled_occurrence(
        "pre_market",
        datetime(2026, 10, 30, 15, 0, tzinfo=UTC),
        "America/New_York",
    )
    after_fall = scheduled_occurrence(
        "pre_market",
        datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
        "America/New_York",
    )

    assert before_spring == datetime(2026, 3, 6, 13, 0, tzinfo=UTC)
    assert after_spring == datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    assert before_fall == datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    assert after_fall == datetime(2026, 11, 2, 13, 0, tzinfo=UTC)


def test_missed_occurrences_are_coalesced_to_one_within_grace() -> None:
    now = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)

    occurrences = coalesced_occurrences(
        "early_market",
        after_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        now_utc=now,
        timezone_name="America/New_York",
        misfire_grace=timedelta(days=5),
    )
    expired = coalesced_occurrences(
        "early_market",
        after_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        now_utc=now,
        timezone_name="America/New_York",
        misfire_grace=timedelta(minutes=5),
    )

    assert occurrences == (datetime(2026, 7, 28, 13, 45, tzinfo=UTC),)
    assert expired == ()


def test_scheduler_invokes_pipeline_directly_without_http() -> None:
    scheduled = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    class Repository:
        def __init__(self) -> None:
            self.claimed = False
            self.intervals: list[tuple[datetime, datetime, int]] = []

        def claim_scheduled_execution(self, **_: object) -> object | None:
            if self.claimed:
                return None
            self.claimed = True
            return SimpleNamespace(id="run-1", fence_generation=1)

        def set_occurrence_interval(
            self,
            run_id: str,
            *,
            start_utc: datetime,
            end_utc: datetime,
            max_future_skew_seconds: int,
        ) -> None:
            assert run_id == "run-1"
            self.intervals.append(
                (start_utc, end_utc, max_future_skew_seconds)
            )

    class Pipeline:
        def __init__(self) -> None:
            self.run_ids: list[str] = []

        def execute(self, run_id: str) -> None:
            self.run_ids.append(run_id)

    pipeline = Pipeline()
    repository = Repository()
    coordinator = SchedulerCoordinator(
        repository=repository,
        pipeline=pipeline,
        configuration_loader=lambda: SimpleNamespace(
            scheduler=SimpleNamespace(claim_ttl_seconds=900),
            max_future_skew_seconds=60,
        ),
        owner="replica-a",
        clock=lambda: scheduled,
    )

    coordinator.run_occurrence("pre_market", scheduled)
    coordinator.run_occurrence("pre_market", scheduled)

    assert pipeline.run_ids == ["run-1"]
    assert repository.intervals == [
        (
            datetime(2026, 7, 27, 21, 0, tzinfo=UTC),
            datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
            60,
        )
    ]


def test_scheduler_registers_configured_windows_with_explicit_misfire_policy() -> None:
    now = datetime(2026, 7, 28, 12, 1, tzinfo=UTC)
    configuration = RuntimeConfiguration(
        market_windows=(MarketWindow.PRE_MARKET, MarketWindow.CLOSE),
        setup_complete=True,
        scheduler=SchedulerConfiguration(
            misfire_grace_seconds=600,
            coalesce=True,
        ),
    )

    class Repository:
        def acquire_scheduler_lease(self, **_: object) -> int:
            return 1

        def release_scheduler_lease(self, **_: object) -> bool:
            return True

        def latest_scheduled_time(self, market_window: str) -> datetime:
            return scheduled_occurrence(
                market_window,
                now,
                configuration.scheduler.timezone,
            )

    coordinator = SchedulerCoordinator(
        repository=Repository(),
        pipeline=SimpleNamespace(),
        configuration_loader=lambda: configuration,
        owner="replica-a",
        clock=lambda: now,
    )
    coordinator.start()
    try:
        jobs = {
            job.id: job
            for job in coordinator.scheduler.get_jobs()
            if job.id.startswith("market-window:")
        }
        assert set(jobs) == {
            "market-window:pre_market",
            "market-window:close",
        }
        assert all(job.coalesce is True for job in jobs.values())
        assert all(job.misfire_grace_time == 600 for job in jobs.values())
        assert all(job.max_instances == 1 for job in jobs.values())
    finally:
        coordinator.stop()


def test_completed_setup_automatically_reloads_scheduler(tmp_path: Path) -> None:
    service = RuntimeService(
        Settings(database_path=tmp_path / "reed.db"),
        secret_store=InMemorySecretStore(),
    )
    migrate(service.database)
    reloads: list[bool] = []
    service.scheduler.reload = lambda: reloads.append(True)

    service.settings_store.save(
        RuntimeConfiguration(
            market_windows=(MarketWindow.PRE_MARKET,),
            setup_complete=True,
        )
    )

    assert reloads == [True]


def test_scheduler_replica_configuration_allows_exactly_one() -> None:
    assert Settings(scheduler_enabled_replicas=1).scheduler_enabled_replicas == 1
    with pytest.raises(ValidationError):
        Settings(scheduler_enabled_replicas=2)
