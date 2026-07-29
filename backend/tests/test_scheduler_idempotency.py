from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db.connection import Database
from app.db.migrations import migrate
from app.digests.models import DigestDraft, DigestDraftItem, IntakeItem
from app.digests.repository import DigestRepository
from app.digests.run import RunStatus


def build_repository(path: Path) -> DigestRepository:
    database = Database(path)
    migrate(database)
    return DigestRepository(database)


def prepare_draft(repository: DigestRepository, run_id: str, suffix: str) -> None:
    published_at = datetime(2026, 11, 2, 12, 30, tzinfo=UTC)
    repository.add_intake_item(
        run_id,
        IntakeItem(
            id=f"source-{suffix}",
            title=f"Source {suffix}",
            url=f"https://example.com/{suffix}",
            source_name="Example",
            published_at=published_at,
        ),
    )
    repository.save_draft(
        run_id,
        DigestDraft(
            id=f"draft-{suffix}",
            market_window="pre_market",
            title=f"Digest {suffix}",
            summary=f"Summary {suffix}",
            items=[
                DigestDraftItem(
                    headline=f"Headline {suffix}",
                    summary=f"Item summary {suffix}",
                    source_item_id=f"source-{suffix}",
                )
            ],
        ),
    )
    repository.set_run_status(run_id, RunStatus.FETCHING)
    repository.set_run_status(run_id, RunStatus.GENERATING)
    repository.set_run_status(run_id, RunStatus.VALIDATING)


def test_duplicate_fire_is_suppressed_by_durable_occurrence_identity(
    tmp_path: Path,
) -> None:
    repository = build_repository(tmp_path / "reed.db")
    scheduled = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
    now = scheduled

    first = repository.claim_scheduled_execution(
        market_window="pre_market",
        scheduled_time_utc=scheduled,
        claim_owner="replica-a",
        now=now,
        claim_expiry=now + timedelta(minutes=5),
    )
    duplicate = repository.claim_scheduled_execution(
        market_window="pre_market",
        scheduled_time_utc=scheduled,
        claim_owner="replica-b",
        now=now + timedelta(seconds=1),
        claim_expiry=now + timedelta(minutes=5),
    )

    assert first is not None
    assert first.fence_generation == 1
    assert duplicate is None
    assert repository.scheduled_execution_count() == 1


def test_restart_recovers_expired_claim_with_new_attempt_and_fence(
    tmp_path: Path,
) -> None:
    repository = build_repository(tmp_path / "reed.db")
    scheduled = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
    first = repository.claim_scheduled_execution(
        market_window="pre_market",
        scheduled_time_utc=scheduled,
        claim_owner="stopped-replica",
        now=scheduled,
        claim_expiry=scheduled + timedelta(seconds=10),
    )
    recovered = repository.claim_scheduled_execution(
        market_window="pre_market",
        scheduled_time_utc=scheduled,
        claim_owner="replacement-replica",
        now=scheduled + timedelta(seconds=11),
        claim_expiry=scheduled + timedelta(minutes=5),
    )

    assert first is not None
    assert recovered is not None
    assert recovered.id != first.id
    assert recovered.fence_generation == 2
    occurrence = repository.get_scheduled_execution(
        "pre_market",
        scheduled,
    )
    assert occurrence.attempt_count == 2
    assert occurrence.claim_owner == "replacement-replica"


def test_stale_fence_cannot_publish_and_current_attempt_publishes_once(
    tmp_path: Path,
) -> None:
    repository = build_repository(tmp_path / "reed.db")
    scheduled = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
    first = repository.claim_scheduled_execution(
        market_window="pre_market",
        scheduled_time_utc=scheduled,
        claim_owner="replica-a",
        now=scheduled,
        claim_expiry=scheduled + timedelta(seconds=10),
    )
    assert first is not None
    prepare_draft(repository, first.id, "stale")

    current = repository.claim_scheduled_execution(
        market_window="pre_market",
        scheduled_time_utc=scheduled,
        claim_owner="replica-b",
        now=scheduled + timedelta(seconds=11),
        claim_expiry=datetime(2099, 1, 1, tzinfo=UTC),
    )
    assert current is not None
    prepare_draft(repository, current.id, "current")

    with pytest.raises(ValueError, match="claim is not current"):
        repository.promote_draft(first.id)

    digest = repository.promote_draft(current.id)
    duplicate = repository.claim_scheduled_execution(
        market_window="pre_market",
        scheduled_time_utc=scheduled,
        claim_owner="replica-a",
        now=scheduled + timedelta(minutes=1),
        claim_expiry=scheduled + timedelta(minutes=6),
    )

    assert duplicate is None
    assert [item.id for item in repository.list_published()] == [digest.id]


def test_scheduler_lease_rejects_replica_until_expiry_and_increments_fence(
    tmp_path: Path,
) -> None:
    repository = build_repository(tmp_path / "reed.db")
    now = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)

    first = repository.acquire_scheduler_lease(
        name="market-windows",
        owner="replica-a",
        now=now,
        lease_expiry=now + timedelta(seconds=30),
    )
    blocked = repository.acquire_scheduler_lease(
        name="market-windows",
        owner="replica-b",
        now=now + timedelta(seconds=1),
        lease_expiry=now + timedelta(seconds=31),
    )
    recovered = repository.acquire_scheduler_lease(
        name="market-windows",
        owner="replica-b",
        now=now + timedelta(seconds=31),
        lease_expiry=now + timedelta(seconds=61),
    )

    assert first == 1
    assert blocked is None
    assert recovered == 2
    assert repository.release_scheduler_lease(
        name="market-windows",
        owner="replica-b",
        fence_generation=2,
        now=now + timedelta(seconds=32),
    )
    next_leader = repository.acquire_scheduler_lease(
        name="market-windows",
        owner="replica-c",
        now=now + timedelta(seconds=32),
        lease_expiry=now + timedelta(seconds=62),
    )
    assert next_leader == 3
