from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config.models import Settings
from app.db.connection import Database
from app.db.migrations import migrate
from app.digests.models import DigestDraft, DigestDraftItem, IntakeItem
from app.digests.repository import DigestRepository
from app.digests.run import RunStatus
from app.main import create_app


REQUIRED_TABLES = {
    "settings",
    "rss_catalog_versions",
    "rss_sources",
    "runs",
    "intake_items",
    "drafts",
    "published_digests",
    "published_digest_items",
    "scheduler_leases",
    "scheduled_executions",
    "publication_locks",
    "schema_migrations",
}


def build_repository(path: Path) -> DigestRepository:
    database = Database(path)
    migrate(database)
    return DigestRepository(database)


def create_draft(repository: DigestRepository, suffix: str = "one") -> str:
    now = datetime.now(UTC)
    run = repository.create_run(
        market_window="pre_market",
        scheduled_time_utc=now,
        claim_owner=f"worker-{suffix}",
        claim_expiry=now + timedelta(minutes=5),
    )
    repository.add_intake_item(
        run.id,
        IntakeItem(
            id=f"source-{suffix}",
            title=f"Source {suffix}",
            url=f"https://example.com/{suffix}",
            source_name="Example",
            published_at=now,
        ),
    )
    repository.save_draft(
        run.id,
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
    repository.set_run_status(run.id, RunStatus.FETCHING)
    repository.set_run_status(run.id, RunStatus.GENERATING)
    repository.set_run_status(run.id, RunStatus.VALIDATING)
    return run.id


def test_empty_database_migrates_with_required_pragmas_and_tables(tmp_path: Path) -> None:
    database = Database(tmp_path / "reed.db")
    migrate(database)

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert REQUIRED_TABLES <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_atomic_promotion_publishes_one_immutable_digest(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "reed.db")
    run_id = create_draft(repository)

    digest = repository.promote_draft(run_id)

    assert repository.get_run(run_id).status is RunStatus.PUBLISHED
    assert repository.list_published() == [digest]
    with repository.database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE published_digests SET title = ? WHERE id = ?",
                ("changed", digest.id),
            )


def test_published_digest_rejects_new_child_items(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "reed.db")
    run_id = create_draft(repository)
    digest = repository.promote_draft(run_id)

    with repository.database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                INSERT INTO published_digest_items(
                    digest_id, position, headline, summary,
                    source_name, source_url
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    digest.id,
                    1,
                    "Late headline",
                    "Late summary",
                    "Late source",
                    "https://example.com/late",
                ),
            )


@pytest.mark.parametrize(
    "failure_stage",
    ["after_digest_insert", "after_run_update", "before_commit"],
)
def test_publication_fault_rolls_back_digest_and_run_state(
    tmp_path: Path, failure_stage: str
) -> None:
    repository = build_repository(tmp_path / "reed.db")
    prior_run_id = create_draft(repository, "prior")
    prior = repository.promote_draft(prior_run_id)
    run_id = create_draft(repository, failure_stage)

    def inject(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError(failure_stage)

    with pytest.raises(RuntimeError, match=failure_stage):
        repository.promote_draft(run_id, fault=inject)

    assert repository.list_published() == [prior]
    assert repository.get_run(run_id).status is RunStatus.VALIDATING


def test_failed_run_never_changes_public_digest(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "reed.db")
    prior_run_id = create_draft(repository, "prior")
    prior = repository.promote_draft(prior_run_id)
    run_id = create_draft(repository, "failed")

    repository.fail_run(run_id, "provider request failed")

    assert repository.get_run(run_id).status is RunStatus.FAILED
    assert repository.list_published() == [prior]


def test_failed_run_diagnostic_is_bounded_and_redacted(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "reed.db")
    run_id = create_draft(repository, "redacted")
    diagnostic = (
        "\x1b[31mrequest failed\x1b[0m "
        "Bearer secret-token "
        "https://example.com/path?api_key=secret "
        + "x" * 600
    )

    failed = repository.fail_run(run_id, diagnostic)

    assert failed.diagnostic is not None
    assert len(failed.diagnostic) <= 500
    assert "\x1b" not in failed.diagnostic
    assert "secret-token" not in failed.diagnostic
    assert "api_key=secret" not in failed.diagnostic


def test_startup_reconciliation_fails_abandoned_run(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "reed.db")
    now = datetime.now(UTC)
    run = repository.create_run(
        market_window="pre_market",
        scheduled_time_utc=now.replace(microsecond=0),
        claim_owner="stopped-worker",
        claim_expiry=now - timedelta(seconds=1),
    )
    repository.set_run_status(run.id, RunStatus.FETCHING)
    repository.set_run_status(run.id, RunStatus.GENERATING)

    result = repository.reconcile(now)

    assert result.failed_runs == 1
    reconciled = repository.get_run(run.id)
    assert reconciled.status is RunStatus.FAILED
    assert reconciled.diagnostic == "run interrupted by restart"
    assert repository.list_published() == []


def test_startup_reconciliation_expires_queued_and_generating_runs_and_releases_claims(
    tmp_path: Path,
) -> None:
    repository = build_repository(tmp_path / "reed.db")
    now = datetime.now(UTC).replace(microsecond=0)
    queued = repository.create_run(
        market_window="pre_market",
        scheduled_time_utc=now,
        claim_owner="queued-owner",
        claim_expiry=now - timedelta(seconds=2),
    )
    generating = repository.create_run(
        market_window="midday",
        scheduled_time_utc=now + timedelta(seconds=1),
        claim_owner="generating-owner",
        claim_expiry=now - timedelta(seconds=1),
    )
    repository.set_run_status(generating.id, RunStatus.FETCHING)
    repository.set_run_status(generating.id, RunStatus.GENERATING)

    result = repository.reconcile(now)

    assert result.failed_runs == 2
    assert repository.get_run(queued.id).status is RunStatus.FAILED
    assert repository.get_run(generating.id).status is RunStatus.FAILED
    with repository.database.connect() as connection:
        executions = connection.execute(
            """
            SELECT status, claim_owner, claim_expiry
            FROM scheduled_executions
            WHERE id IN (?, ?)
            ORDER BY id
            """,
            (queued.scheduled_execution_id, generating.scheduled_execution_id),
        ).fetchall()
    assert [dict(execution) for execution in executions] == [
        {"status": "pending", "claim_owner": None, "claim_expiry": None},
        {"status": "pending", "claim_owner": None, "claim_expiry": None},
    ]


def test_app_boots_after_migration_and_reconciliation(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "reed.db",
            allowed_hosts="testserver",
        )
    )

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {
            "status": "ok",
            "service": "reed",
        }
        assert client.get("/api/digests").json() == []
        assert client.get("/api/runtime-status").json() == {
            "scheduler_active": False,
            "scheduler_leader": False,
            "scheduler_lease": "inactive",
            "latest_run": None,
        }
