from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock, Thread, current_thread
from time import monotonic
from types import SimpleNamespace
from typing import Callable

from app.config.configuration import (
    MarketWindow,
    RuntimeConfiguration,
    SchedulerConfiguration,
)
from app.runtime.scheduler import LeaseStatus, SchedulerCoordinator, scheduled_occurrence


class ManualClock:
    def __init__(self, start: datetime) -> None:
        self._time = start
        self._lock = Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._time

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._time += timedelta(seconds=seconds)


def make_configuration(**overrides: object) -> RuntimeConfiguration:
    scheduler = SchedulerConfiguration(
        lease_ttl_seconds=overrides.get("lease_ttl_seconds", 30),
        lease_renewal_seconds=overrides.get("lease_renewal_seconds", 10),
    )
    return RuntimeConfiguration(
        market_windows=(MarketWindow.CLOSE,),
        setup_complete=True,
        scheduler=scheduler,
    )


class FastWaitCoordinator(SchedulerCoordinator):
    """Coordinator with a tiny promotion-wait interval for deterministic tests."""

    WAIT_SECONDS = 0.05

    def _start_leader_wait(self, configuration: RuntimeConfiguration) -> None:
        if self._leader_wait_thread is not None:
            return
        wait_seconds = self.WAIT_SECONDS

        def wait_and_promote() -> None:
            while not self._leader_wait_stop.wait(wait_seconds):
                if (
                    self.scheduler is not None and self.scheduler.running
                ) or self.lease_status is LeaseStatus.LOST:
                    return
                if self._try_promote(configuration):
                    return

        self._leader_wait_thread = Thread(target=wait_and_promote, daemon=True)
        self._leader_wait_thread.start()


class ExpiringLeaseRepository:
    """Repository where lease is held by another owner until expiry."""

    def __init__(
        self,
        *,
        owner: str,
        fence: int,
        expiry: datetime,
    ) -> None:
        self.owner = owner
        self.fence = fence
        self.expiry = expiry
        self._lock = Lock()

    def acquire_scheduler_lease(
        self,
        *,
        owner: str,
        now: datetime,
        lease_expiry: datetime,
        **_: object,
    ) -> int | None:
        with self._lock:
            if self.owner == owner:
                self.expiry = lease_expiry
                return self.fence
            if now >= self.expiry:
                self.owner = owner
                self.fence += 1
                self.expiry = lease_expiry
                return self.fence
            return None

    def release_scheduler_lease(self, **_: object) -> bool:
        return True

    def latest_scheduled_time(self, market_window: str) -> datetime:
        return scheduled_occurrence(market_window, datetime.now(UTC), "America/New_York")


def test_follower_autonomously_promotes_when_lease_expires() -> None:
    """
    Starting the coordinator once as a follower must be enough; the background
    wait/promotion mechanism must become leader after the prior lease expires.

    This fails on origin/main because start() does not start a promotion wait
    thread and therefore never promotes.
    """
    base = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    clock = ManualClock(base)
    configuration = make_configuration()
    repository = ExpiringLeaseRepository(
        owner="old-process",
        fence=1,
        expiry=base + timedelta(seconds=30),
    )
    coordinator = FastWaitCoordinator(
        repository=repository,
        pipeline=SimpleNamespace(),
        configuration_loader=lambda: configuration,
        owner="replica",
        clock=clock,
    )

    coordinator.start()  # exactly one explicit start

    assert coordinator.lease_status is LeaseStatus.FOLLOWER
    assert coordinator.scheduler is None

    clock.advance(31)
    # Give the fast promotion thread time to wake up and promote.
    Event().wait(0.2)

    assert coordinator.lease_status is LeaseStatus.LEADER
    assert repository.owner == "replica"
    assert repository.fence == 2
    scheduler = coordinator.scheduler
    assert scheduler is not None
    assert scheduler.running
    job_ids = {
        job.id
        for job in scheduler.get_jobs()
        if job.id.startswith("market-window:")
    }
    assert job_ids == {"market-window:close"}

    coordinator.stop()


def test_stop_while_waiting_cancels_promotion_thread() -> None:
    base = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    clock = ManualClock(base)
    configuration = make_configuration()
    repository = ExpiringLeaseRepository(
        owner="old-process",
        fence=1,
        expiry=base + timedelta(seconds=30),
    )
    coordinator = FastWaitCoordinator(
        repository=repository,
        pipeline=SimpleNamespace(),
        configuration_loader=lambda: configuration,
        owner="replica",
        clock=clock,
    )

    coordinator.start()
    assert coordinator.lease_status is LeaseStatus.FOLLOWER
    wait_thread = coordinator._leader_wait_thread
    assert wait_thread is not None
    assert wait_thread.is_alive()

    coordinator.stop()

    assert coordinator.lease_status is LeaseStatus.INACTIVE
    assert coordinator.scheduler is None
    assert coordinator._leader_wait_thread is None
    assert not wait_thread.is_alive()


def test_reload_while_follower_does_not_deadlock() -> None:
    base = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    clock = ManualClock(base)
    configuration = make_configuration()
    repository = ExpiringLeaseRepository(
        owner="old-process",
        fence=1,
        expiry=base + timedelta(seconds=30),
    )
    coordinator = FastWaitCoordinator(
        repository=repository,
        pipeline=SimpleNamespace(),
        configuration_loader=lambda: configuration,
        owner="replica",
        clock=clock,
    )

    coordinator.start()
    assert coordinator.lease_status is LeaseStatus.FOLLOWER

    # Reload must be safe while the promotion thread is waiting.
    coordinator.reload()
    assert coordinator.lease_status is LeaseStatus.FOLLOWER
    assert coordinator._leader_wait_thread is not None

    coordinator.stop()


def test_promotion_thread_does_not_self_join() -> None:
    """
    _try_promote runs inside the promotion thread. If it cancels the promotion
    wait and then joins that same thread, it will hang for the join timeout.

    This fails when the coordinator tries to cancel and join the thread it is
    currently executing on.
    """
    base = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    clock = ManualClock(base)
    configuration = make_configuration()

    class TransferRepository:
        def __init__(self) -> None:
            self.owner = "old-process"
            self.fence = 1
            self.expiry = base - timedelta(seconds=1)

        def acquire_scheduler_lease(
            self,
            *,
            owner: str,
            now: datetime,
            lease_expiry: datetime,
            **_: object,
        ) -> int | None:
            if self.owner != owner and now >= self.expiry:
                self.owner = owner
                self.fence += 1
                self.expiry = lease_expiry
                return self.fence
            return None

        def release_scheduler_lease(self, **_: object) -> bool:
            return True

        def latest_scheduled_time(self, market_window: str) -> datetime:
            return scheduled_occurrence(
                market_window,
                base,
                configuration.scheduler.timezone,
            )

    coordinator = SchedulerCoordinator(
        repository=TransferRepository(),
        pipeline=SimpleNamespace(),
        configuration_loader=lambda: configuration,
        owner="replica",
        clock=clock,
    )

    # Pretend the current thread is the promotion thread, as happens during
    # autonomous promotion.
    coordinator._leader_wait_thread = current_thread()

    before = monotonic()
    promoted = coordinator._try_promote(configuration)
    elapsed = monotonic() - before

    assert promoted is True
    assert coordinator.lease_status is LeaseStatus.LEADER
    assert coordinator._leader_wait_thread is None
    # A self-join currently blocks for the full one-second timeout.
    assert elapsed < 0.5, f"promotion self-joined for {elapsed:.2f}s"

    coordinator.stop()


def test_concurrent_start_and_promotion_create_single_scheduler() -> None:
    """
    Two coordinator paths (start() and _try_promote) must not both install a
    scheduler when they race to promote after a same-owner lease acquisition.

    The repository here renews the lease for the same owner, which is what the
    real implementation does once the prior owner has released or expired. With
    no serialization inside the coordinator, both paths can reach _become_leader
    and create duplicate scheduler/job sets.
    """
    base = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    clock = ManualClock(base)
    configuration = make_configuration()

    class RacingRepository:
        def __init__(self) -> None:
            # Same owner as the coordinator; lease is live. Calls from the
            # coordinator are treated as renewals and both succeed.
            self.owner = "replica"
            self.fence = 1
            self.expiry = base + timedelta(seconds=30)
            self.acquire_barrier = Barrier(2, timeout=2)
            self.become_count = 0
            self._lock = Lock()

        def acquire_scheduler_lease(
            self,
            *,
            owner: str,
            now: datetime,
            lease_expiry: datetime,
            **_: object,
        ) -> int | None:
            # Force both contenders to arrive here before either proceeds.
            self.acquire_barrier.wait()
            with self._lock:
                if self.owner == owner:
                    self.expiry = lease_expiry
                    return self.fence
                if now >= self.expiry:
                    self.owner = owner
                    self.fence += 1
                    self.expiry = lease_expiry
                    return self.fence
                return None

        def release_scheduler_lease(self, **_: object) -> bool:
            return True

        def latest_scheduled_time(self, market_window: str) -> datetime:
            return scheduled_occurrence(
                market_window,
                base,
                configuration.scheduler.timezone,
            )

    repository = RacingRepository()
    errors: list[tuple[str, BaseException]] = []

    coordinator = SchedulerCoordinator(
        repository=repository,
        pipeline=SimpleNamespace(),
        configuration_loader=lambda: configuration,
        owner="replica",
        clock=clock,
    )

    original_become: Callable[..., None] = SchedulerCoordinator._become_leader

    def counting_become(
        self: SchedulerCoordinator,
        config: RuntimeConfiguration,
        fence: int,
    ) -> None:
        with repository._lock:
            repository.become_count += 1
        original_become(self, config, fence)

    # Patch the instance so we can count without affecting other tests.
    coordinator._become_leader = lambda config, fence: counting_become(
        coordinator, config, fence
    )

    def run_start() -> None:
        try:
            coordinator.start()
        except BaseException as exc:  # pragma: no cover - diagnostic
            errors.append(("start", exc))

    def run_promote() -> None:
        try:
            coordinator._try_promote(configuration)
        except BaseException as exc:  # pragma: no cover - diagnostic
            errors.append(("promote", exc))

    t1 = Thread(target=run_start)
    t2 = Thread(target=run_promote)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, f"concurrency test raised exceptions: {errors}"
    assert (
        repository.become_count == 1
    ), f"expected exactly one leader promotion, got {repository.become_count}"

    coordinator.stop()
