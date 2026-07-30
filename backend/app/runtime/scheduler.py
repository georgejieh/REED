from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from threading import Event, Lock, Thread, current_thread
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config.configuration import RuntimeConfiguration
from app.intake.rss import compute_window_bounds


SCHEDULER_LEASE_NAME = "market-windows"


@dataclass(frozen=True)
class MarketWindowSchedule:
    hour: int
    minute: int
    weekdays: tuple[int, ...]


MARKET_WINDOW_SCHEDULES = {
    "pre_market": MarketWindowSchedule(8, 0, (0, 1, 2, 3, 4)),
    "early_market": MarketWindowSchedule(9, 45, (0, 1, 2, 3, 4)),
    "midday": MarketWindowSchedule(12, 30, (0, 1, 2, 3, 4)),
    "close": MarketWindowSchedule(16, 15, (0, 1, 2, 3, 4)),
    "weekend_recap": MarketWindowSchedule(7, 0, (0,)),
}


class LeaseStatus(StrEnum):
    INACTIVE = "inactive"
    LEADER = "leader"
    FOLLOWER = "follower"
    LOST = "lost"


@dataclass(frozen=True)
class SchedulerStatus:
    active: bool
    lease_status: LeaseStatus
    leader: bool


class ScheduledRepository(Protocol):
    def claim_scheduled_execution(self, **kwargs: object) -> object | None: ...

    def set_occurrence_interval(self, run_id: str, **kwargs: object) -> None: ...

    def renew_scheduled_claim(self, **kwargs: object) -> bool: ...

    def acquire_scheduler_lease(self, **kwargs: object) -> int | None: ...

    def release_scheduler_lease(self, **kwargs: object) -> bool: ...

    def latest_scheduled_time(self, market_window: str) -> datetime | None: ...


class Pipeline(Protocol):
    def execute(self, run_id: str) -> object: ...


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")


def _resolve_local(local_date: date, spec: MarketWindowSchedule, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(local_date, time(spec.hour, spec.minute))
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    first_roundtrip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    second_roundtrip = second.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if first_roundtrip != naive and second_roundtrip != naive:
        raise ValueError("scheduled local time does not exist")
    if first.utcoffset() != second.utcoffset():
        return min(first.astimezone(UTC), second.astimezone(UTC))
    return first.astimezone(UTC)


def scheduled_occurrence(
    market_window: str,
    at_utc: datetime,
    timezone_name: str,
) -> datetime:
    _require_aware(at_utc)
    try:
        spec = MARKET_WINDOW_SCHEDULES[market_window]
    except KeyError as error:
        raise ValueError("unknown market window") from error
    zone = ZoneInfo(timezone_name)
    local_date = at_utc.astimezone(zone).date()
    for days_back in range(8):
        candidate_date = local_date - timedelta(days=days_back)
        if candidate_date.weekday() not in spec.weekdays:
            continue
        candidate = _resolve_local(candidate_date, spec, zone)
        if candidate <= at_utc.astimezone(UTC):
            return candidate
    raise RuntimeError("scheduled occurrence could not be derived")


def coalesced_occurrences(
    market_window: str,
    *,
    after_utc: datetime | None,
    now_utc: datetime,
    timezone_name: str,
    misfire_grace: timedelta,
) -> tuple[datetime, ...]:
    _require_aware(now_utc)
    if after_utc is not None:
        _require_aware(after_utc)
    occurrence = scheduled_occurrence(market_window, now_utc, timezone_name)
    if after_utc is not None and occurrence <= after_utc.astimezone(UTC):
        return ()
    if now_utc.astimezone(UTC) - occurrence > misfire_grace:
        return ()
    return (occurrence,)


class ClaimHeartbeat:
    def __init__(
        self,
        *,
        repository: ScheduledRepository,
        run_id: str,
        owner: str,
        fence_generation: int,
        clock: Callable[[], datetime],
        claim_ttl: timedelta,
    ):
        self.repository = repository
        self.run_id = run_id
        self.owner = owner
        self.fence_generation = fence_generation
        self.clock = clock
        self.claim_ttl = claim_ttl
        self.interval = max(5.0, claim_ttl.total_seconds() / 3)
        self.stop_event = Event()
        self.thread: Thread | None = None

    def __enter__(self) -> ClaimHeartbeat:
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            now = self.clock()
            renewed = self.repository.renew_scheduled_claim(
                run_id=self.run_id,
                claim_owner=self.owner,
                fence_generation=self.fence_generation,
                now=now,
                claim_expiry=now + self.claim_ttl,
            )
            if not renewed:
                self.stop_event.set()
                return


class SchedulerCoordinator:
    def __init__(
        self,
        *,
        repository: ScheduledRepository,
        pipeline: Pipeline,
        configuration_loader: Callable[[], RuntimeConfiguration],
        owner: str,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.pipeline = pipeline
        self.configuration_loader = configuration_loader
        self.owner = owner
        self.clock = clock or (lambda: datetime.now(UTC))
        self.scheduler: BackgroundScheduler | None = None
        self.lease_fence: int | None = None
        self.lease_status = LeaseStatus.INACTIVE
        self._leader_wait_stop = Event()
        self._leader_wait_thread: Thread | None = None
        self._coordinator_lock = Lock()

    @property
    def status(self) -> SchedulerStatus:
        active = self.scheduler is not None and self.scheduler.running
        return SchedulerStatus(
            active=active,
            lease_status=self.lease_status,
            leader=active and self.lease_status is LeaseStatus.LEADER,
        )

    def start(self) -> None:
        configuration = self.configuration_loader()
        scheduler_config = configuration.scheduler
        if (
            not configuration.setup_complete
            or not scheduler_config.enabled
            or not configuration.market_windows
        ):
            self.stop()
            return
        if self.scheduler is not None and self.scheduler.running:
            if self.lease_status is LeaseStatus.LEADER:
                self._renew_lease()
            return
        now = self.clock()
        lease_ttl = timedelta(seconds=scheduler_config.lease_ttl_seconds)
        fence = self.repository.acquire_scheduler_lease(
            name=SCHEDULER_LEASE_NAME,
            owner=self.owner,
            now=now,
            lease_expiry=now + lease_ttl,
        )
        if fence is None:
            with self._coordinator_lock:
                self.lease_status = LeaseStatus.FOLLOWER
                self._start_leader_wait(configuration)
            return
        self._promote_to_leader(configuration, fence)

    def _promote_to_leader(
        self,
        configuration: RuntimeConfiguration,
        fence: int,
    ) -> None:
        """Serialize ownership transitions so only one scheduler/job set installs.

        Must be called with a freshly acquired fence. On failure/cancellation the
        fence is released back to the repository and state reverts to follower.
        """
        with self._coordinator_lock:
            if self.scheduler is not None and self.scheduler.running:
                # We won the lease race but lost the transition race; release the
                # redundant fence so it can be reused.
                self.repository.release_scheduler_lease(
                    name=SCHEDULER_LEASE_NAME,
                    owner=self.owner,
                    fence_generation=fence,
                    now=self.clock(),
                )
                return
            self._cancel_leader_wait()
            try:
                self._become_leader(configuration, fence)
            except BaseException:
                self.repository.release_scheduler_lease(
                    name=SCHEDULER_LEASE_NAME,
                    owner=self.owner,
                    fence_generation=fence,
                    now=self.clock(),
                )
                self.lease_status = LeaseStatus.FOLLOWER
                self._start_leader_wait(configuration)
                raise

    def stop(self) -> None:
        with self._coordinator_lock:
            self._cancel_leader_wait()
            if self.scheduler is not None:
                self.scheduler.shutdown(wait=False)
                self.scheduler = None
            if self.lease_fence is not None:
                self.repository.release_scheduler_lease(
                    name=SCHEDULER_LEASE_NAME,
                    owner=self.owner,
                    fence_generation=self.lease_fence,
                    now=self.clock(),
                )
            self.lease_fence = None
            self.lease_status = LeaseStatus.INACTIVE

    def reload(self) -> None:
        self.stop()
        self.start()

    def run_occurrence(
        self,
        market_window: str,
        scheduled_time_utc: datetime,
    ) -> object | None:
        if self.scheduler is not None and not self._renew_lease():
            return None
        configuration = self.configuration_loader()
        now = self.clock()
        claim_ttl = timedelta(seconds=configuration.scheduler.claim_ttl_seconds)
        run = self.repository.claim_scheduled_execution(
            market_window=market_window,
            scheduled_time_utc=scheduled_time_utc,
            claim_owner=self.owner,
            now=now,
            claim_expiry=now + claim_ttl,
        )
        if run is None:
            return None
        start_utc, end_utc = compute_window_bounds(
            market_window,
            scheduled_time_utc,
        )
        self.repository.set_occurrence_interval(
            run.id,
            start_utc=start_utc,
            end_utc=end_utc,
            max_future_skew_seconds=configuration.max_future_skew_seconds,
        )
        with ClaimHeartbeat(
            repository=self.repository,
            run_id=run.id,
            owner=self.owner,
            fence_generation=run.fence_generation,
            clock=self.clock,
            claim_ttl=claim_ttl,
        ):
            return self.pipeline.execute(run.id)

    def _become_leader(
        self,
        configuration: RuntimeConfiguration,
        fence: int,
    ) -> None:
        scheduler_config = configuration.scheduler
        self.lease_fence = fence
        self.lease_status = LeaseStatus.LEADER
        self.scheduler = BackgroundScheduler(
            timezone=scheduler_config.timezone,
            job_defaults={
                "coalesce": scheduler_config.coalesce,
                "misfire_grace_time": scheduler_config.misfire_grace_seconds,
                "max_instances": 1,
            },
        )
        self.scheduler.add_job(
            self._renew_lease,
            "interval",
            seconds=scheduler_config.lease_renewal_seconds,
            id="scheduler-lease",
            replace_existing=True,
        )
        for window in configuration.market_windows:
            spec = MARKET_WINDOW_SCHEDULES[window.value]
            self.scheduler.add_job(
                self._fire_window,
                CronTrigger(
                    day_of_week=",".join(str(day) for day in spec.weekdays),
                    hour=spec.hour,
                    minute=spec.minute,
                    timezone=scheduler_config.timezone,
                ),
                args=(window.value,),
                id=f"market-window:{window.value}",
                replace_existing=True,
            )
        self.scheduler.start()
        self._recover_missed(configuration, self.clock())

    def _start_leader_wait(self, configuration: RuntimeConfiguration) -> None:
        if self._leader_wait_thread is not None:
            return
        scheduler_config = configuration.scheduler
        wait_seconds = min(
            scheduler_config.lease_ttl_seconds,
            max(5, scheduler_config.lease_renewal_seconds),
        )

        def wait_and_promote() -> None:
            while not self._leader_wait_stop.wait(wait_seconds):
                if self.lease_status is LeaseStatus.LOST:
                    return
                if self._try_promote(configuration):
                    return

        self._leader_wait_thread = Thread(target=wait_and_promote, daemon=True)
        self._leader_wait_thread.start()

    def _cancel_leader_wait(self) -> None:
        self._leader_wait_stop.set()
        if self._leader_wait_thread is not None:
            # Never join the thread we are currently executing on; the caller is
            # responsible for clearing the handle in that case.
            if self._leader_wait_thread is not current_thread():
                self._leader_wait_thread.join(timeout=1)
            self._leader_wait_thread = None
        self._leader_wait_stop.clear()

    def _try_promote(self, configuration: RuntimeConfiguration) -> bool:
        if self.lease_status is LeaseStatus.LOST:
            return False
        scheduler_config = configuration.scheduler
        now = self.clock()
        fence = self.repository.acquire_scheduler_lease(
            name=SCHEDULER_LEASE_NAME,
            owner=self.owner,
            now=now,
            lease_expiry=now + timedelta(seconds=scheduler_config.lease_ttl_seconds),
        )
        if fence is None:
            return False
        self._promote_to_leader(configuration, fence)
        return True

    def _become_leader(
        self,
        configuration: RuntimeConfiguration,
        fence: int,
    ) -> None:
        scheduler_config = configuration.scheduler
        self.lease_fence = fence
        self.lease_status = LeaseStatus.LEADER
        self.scheduler = BackgroundScheduler(
            timezone=scheduler_config.timezone,
            job_defaults={
                "coalesce": scheduler_config.coalesce,
                "misfire_grace_time": scheduler_config.misfire_grace_seconds,
                "max_instances": 1,
            },
        )
        self.scheduler.add_job(
            self._renew_lease,
            "interval",
            seconds=scheduler_config.lease_renewal_seconds,
            id="scheduler-lease",
            replace_existing=True,
        )
        for window in configuration.market_windows:
            spec = MARKET_WINDOW_SCHEDULES[window.value]
            self.scheduler.add_job(
                self._fire_window,
                CronTrigger(
                    day_of_week=",".join(str(day) for day in spec.weekdays),
                    hour=spec.hour,
                    minute=spec.minute,
                    timezone=scheduler_config.timezone,
                ),
                args=(window.value,),
                id=f"market-window:{window.value}",
                replace_existing=True,
            )
        self.scheduler.start()
        self._recover_missed(configuration, self.clock())

    def _fire_window(self, market_window: str) -> None:
        if not self._renew_lease():
            return
        configuration = self.configuration_loader()
        occurrence = scheduled_occurrence(
            market_window,
            self.clock(),
            configuration.scheduler.timezone,
        )
        self.run_occurrence(market_window, occurrence)

    def _renew_lease(self) -> bool:
        configuration = self.configuration_loader()
        now = self.clock()
        fence = self.repository.acquire_scheduler_lease(
            name=SCHEDULER_LEASE_NAME,
            owner=self.owner,
            now=now,
            lease_expiry=now
            + timedelta(seconds=configuration.scheduler.lease_ttl_seconds),
        )
        if fence is None or (
            self.lease_fence is not None and fence != self.lease_fence
        ):
            self.lease_status = LeaseStatus.LOST
            return False
        self.lease_fence = fence
        self.lease_status = LeaseStatus.LEADER
        return True

    def _recover_missed(
        self,
        configuration: RuntimeConfiguration,
        now: datetime,
    ) -> None:
        grace = timedelta(
            seconds=configuration.scheduler.misfire_grace_seconds
        )
        for window in configuration.market_windows:
            for occurrence in coalesced_occurrences(
                window.value,
                after_utc=None,
                now_utc=now,
                timezone_name=configuration.scheduler.timezone,
                misfire_grace=grace,
            ):
                self.scheduler.add_job(
                    self.run_occurrence,
                    "date",
                    run_date=now,
                    args=(window.value, occurrence),
                    id=f"recovery:{window.value}:{occurrence.isoformat()}",
                    replace_existing=True,
                )
