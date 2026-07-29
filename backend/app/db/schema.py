from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.digests.run import RunStatus


@dataclass(frozen=True)
class RunRecord:
    id: str
    scheduled_execution_id: str
    fence_generation: int
    status: RunStatus
    diagnostic: str | None
    published_digest_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RunContext:
    run: RunRecord
    market_window: str
    scheduled_time_utc: datetime
    run_request_id: str | None
    window_start_utc: datetime | None
    window_end_utc: datetime | None
    max_future_skew_seconds: int | None


@dataclass(frozen=True)
class ReconciliationResult:
    failed_runs: int
    repaired_runs: int


@dataclass(frozen=True)
class ScheduledExecutionRecord:
    id: str
    market_window: str
    scheduled_time_utc: datetime
    status: str
    claim_owner: str | None
    claim_expiry: datetime | None
    fence_generation: int
    attempt_count: int


@dataclass(frozen=True)
class SchedulerLeaseRecord:
    name: str
    owner: str
    lease_expiry: datetime
    fence_generation: int
