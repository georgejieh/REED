from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from sqlite3 import Connection, Row
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from app.db.connection import Database
from app.db.schema import (
    ReconciliationResult,
    RunContext,
    RunRecord,
    ScheduledExecutionRecord,
    SchedulerLeaseRecord,
)
from app.digests.models import (
    DigestDraft,
    IntakeItem,
    PublishedDigest,
    PublishedDigestItem,
)
from app.digests.run import ALLOWED_TRANSITIONS, RunStatus


FaultHook = Callable[[str], None]
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
BEARER_PATTERN = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
SECRET_PATTERN = re.compile(
    r"\b(api[_-]?key|token|secret)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def redact_diagnostic(value: str) -> str:
    value = ANSI_PATTERN.sub("", value)
    value = BEARER_PATTERN.sub("Bearer [redacted]", value)
    value = SECRET_PATTERN.sub(r"\1=[redacted]", value)

    def strip_query(match: re.Match[str]) -> str:
        parsed = urlsplit(match.group(0))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    return URL_PATTERN.sub(strip_query, value)[:500]


class DigestRepository:
    def __init__(self, database: Database):
        self.database = database

    def create_run(
        self,
        *,
        market_window: str,
        scheduled_time_utc: datetime,
        claim_owner: str,
        claim_expiry: datetime,
    ) -> RunRecord:
        now = datetime.now(UTC)
        execution_id = str(uuid4())
        run_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_executions(
                    id, market_window, scheduled_time_utc, status, claim_owner,
                    claim_expiry, fence_generation, attempt_count
                ) VALUES (?, ?, ?, 'claimed', ?, ?, 1, 1)
                """,
                (
                    execution_id,
                    market_window,
                    _iso(scheduled_time_utc),
                    claim_owner,
                    _iso(claim_expiry),
                ),
            )
            connection.execute(
                """
                INSERT INTO runs(
                    id, scheduled_execution_id, fence_generation, status,
                    created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?)
                """,
                (run_id, execution_id, RunStatus.QUEUED, _iso(now), _iso(now)),
            )
        return self.get_run(run_id)

    def claim_scheduled_execution(
        self,
        *,
        market_window: str,
        scheduled_time_utc: datetime,
        claim_owner: str,
        now: datetime,
        claim_expiry: datetime,
    ) -> RunRecord | None:
        scheduled_text = _iso(scheduled_time_utc)
        now_text = _iso(now)
        claim_expiry_text = _iso(claim_expiry)
        if claim_expiry <= now:
            raise ValueError("claim expiry must be in the future")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO scheduled_executions(
                    id, market_window, scheduled_time_utc, status,
                    fence_generation, attempt_count
                ) VALUES (?, ?, ?, 'queued', 0, 0)
                """,
                (str(uuid4()), market_window, scheduled_text),
            )
            current = connection.execute(
                """
                SELECT * FROM scheduled_executions
                WHERE market_window = ? AND scheduled_time_utc = ?
                """,
                (market_window, scheduled_text),
            ).fetchone()
            if current is None:
                raise RuntimeError("scheduled execution could not be created")
            if current["status"] == "published":
                return None
            if (
                current["claim_owner"] is not None
                and current["claim_expiry"] is not None
                and current["claim_expiry"] > now_text
            ):
                return None

            changed = connection.execute(
                """
                UPDATE scheduled_executions
                SET status = 'claimed', claim_owner = ?, claim_expiry = ?,
                    fence_generation = fence_generation + 1,
                    attempt_count = attempt_count + 1
                WHERE id = ? AND status = ?
                  AND fence_generation = ?
                  AND (
                      (claim_owner IS NULL AND ? IS NULL)
                      OR claim_owner = ?
                  )
                  AND (
                      (claim_expiry IS NULL AND ? IS NULL)
                      OR claim_expiry = ?
                  )
                """,
                (
                    claim_owner,
                    claim_expiry_text,
                    current["id"],
                    current["status"],
                    current["fence_generation"],
                    current["claim_owner"],
                    current["claim_owner"],
                    current["claim_expiry"],
                    current["claim_expiry"],
                ),
            ).rowcount
            if changed != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM scheduled_executions WHERE id = ?",
                (current["id"],),
            ).fetchone()
            if claimed is None:
                raise RuntimeError("claimed execution is missing")
            run_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO runs(
                    id, scheduled_execution_id, fence_generation, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    claimed["id"],
                    claimed["fence_generation"],
                    RunStatus.QUEUED,
                    now_text,
                    now_text,
                ),
            )
        return self.get_run(run_id)

    def renew_scheduled_claim(
        self,
        *,
        run_id: str,
        claim_owner: str,
        fence_generation: int,
        now: datetime,
        claim_expiry: datetime,
    ) -> bool:
        if claim_expiry <= now:
            raise ValueError("claim expiry must be in the future")
        with self.database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE scheduled_executions
                SET claim_expiry = ?
                WHERE id = (
                    SELECT scheduled_execution_id FROM runs WHERE id = ?
                )
                  AND status = 'claimed'
                  AND claim_owner = ?
                  AND fence_generation = ?
                  AND claim_expiry > ?
                """,
                (
                    _iso(claim_expiry),
                    run_id,
                    claim_owner,
                    fence_generation,
                    _iso(now),
                ),
            ).rowcount
        return changed == 1

    def get_scheduled_execution(
        self,
        market_window: str,
        scheduled_time_utc: datetime,
    ) -> ScheduledExecutionRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM scheduled_executions
                WHERE market_window = ? AND scheduled_time_utc = ?
                """,
                (market_window, _iso(scheduled_time_utc)),
            ).fetchone()
        if row is None:
            raise KeyError((market_window, scheduled_time_utc))
        return self._scheduled_execution_record(row)

    def scheduled_execution_count(self) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM scheduled_executions"
            ).fetchone()[0]

    def latest_scheduled_time(self, market_window: str) -> datetime | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(scheduled_time_utc) AS scheduled_time_utc
                FROM scheduled_executions WHERE market_window = ?
                """,
                (market_window,),
            ).fetchone()
        value = row["scheduled_time_utc"] if row else None
        return _datetime(value) if value else None

    def acquire_scheduler_lease(
        self,
        *,
        name: str,
        owner: str,
        now: datetime,
        lease_expiry: datetime,
    ) -> int | None:
        if lease_expiry <= now:
            raise ValueError("scheduler lease expiry must be in the future")
        now_text = _iso(now)
        expiry_text = _iso(lease_expiry)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO scheduler_leases(
                    name, owner, lease_expiry, fence_generation
                ) VALUES (?, ?, ?, 1)
                """,
                (name, owner, expiry_text),
            )
            current = connection.execute(
                "SELECT * FROM scheduler_leases WHERE name = ?",
                (name,),
            ).fetchone()
            if current is None:
                raise RuntimeError("scheduler lease could not be created")
            if current["owner"] == owner:
                changed = connection.execute(
                    """
                    UPDATE scheduler_leases SET lease_expiry = ?
                    WHERE name = ? AND owner = ? AND fence_generation = ?
                    """,
                    (
                        expiry_text,
                        name,
                        owner,
                        current["fence_generation"],
                    ),
                ).rowcount
                return current["fence_generation"] if changed == 1 else None
            if current["lease_expiry"] > now_text:
                return None
            changed = connection.execute(
                """
                UPDATE scheduler_leases
                SET owner = ?, lease_expiry = ?,
                    fence_generation = fence_generation + 1
                WHERE name = ? AND owner = ? AND lease_expiry = ?
                  AND fence_generation = ?
                """,
                (
                    owner,
                    expiry_text,
                    name,
                    current["owner"],
                    current["lease_expiry"],
                    current["fence_generation"],
                ),
            ).rowcount
            if changed != 1:
                return None
            return current["fence_generation"] + 1

    def scheduler_lease(self, name: str) -> SchedulerLeaseRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduler_leases WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return SchedulerLeaseRecord(
            name=row["name"],
            owner=row["owner"],
            lease_expiry=_datetime(row["lease_expiry"]),
            fence_generation=row["fence_generation"],
        )

    def release_scheduler_lease(
        self,
        *,
        name: str,
        owner: str,
        fence_generation: int,
        now: datetime,
    ) -> bool:
        with self.database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE scheduler_leases SET lease_expiry = ?
                WHERE name = ? AND owner = ? AND fence_generation = ?
                """,
                (_iso(now), name, owner, fence_generation),
            ).rowcount
        return changed == 1

    def create_manual_run(
        self,
        *,
        market_window: str,
        requested_at: datetime,
        claim_expiry: datetime,
    ) -> RunRecord:
        request_id = f"manual:{uuid4()}"
        now = datetime.now(UTC)
        execution_id = str(uuid4())
        run_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_executions(
                    id, market_window, scheduled_time_utc, status, claim_owner,
                    claim_expiry, fence_generation, attempt_count, run_request_id
                ) VALUES (?, ?, ?, 'claimed', ?, ?, 1, 1, ?)
                """,
                (
                    execution_id,
                    market_window,
                    _iso(requested_at),
                    request_id,
                    _iso(claim_expiry),
                    request_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO runs(
                    id, scheduled_execution_id, fence_generation, status,
                    created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?)
                """,
                (run_id, execution_id, RunStatus.QUEUED, _iso(now), _iso(now)),
            )
        return self.get_run(run_id)

    def set_occurrence_interval(
        self,
        run_id: str,
        *,
        start_utc: datetime,
        end_utc: datetime,
        max_future_skew_seconds: int,
    ) -> None:
        with self.database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE scheduled_executions
                SET window_start_utc = ?, window_end_utc = ?,
                    max_future_skew_seconds = ?
                WHERE id = (
                    SELECT scheduled_execution_id FROM runs WHERE id = ?
                )
                """,
                (
                    _iso(start_utc),
                    _iso(end_utc),
                    max_future_skew_seconds,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise KeyError(run_id)

    def add_intake_item(self, run_id: str, item: IntakeItem) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO intake_items(
                    id, run_id, title, url, source_name, published_at,
                    feed_id, source_url, retrieved_at, summary,
                    validation_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    run_id,
                    item.title,
                    item.url,
                    item.source_name,
                    _iso(item.published_at),
                    item.feed_id,
                    item.source_url,
                    _iso(item.retrieved_at) if item.retrieved_at else None,
                    item.summary,
                    item.validation_outcome,
                ),
            )

    def record_source_outcome(
        self,
        run_id: str,
        *,
        source_type: str,
        source_id: str,
        source_url: str,
        retrieved_at: datetime,
        state: str,
        item_count: int,
        diagnostic: str | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_outcomes(
                    run_id, source_type, source_id, source_url, retrieved_at,
                    state, item_count, diagnostic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source_type, source_id) DO UPDATE SET
                    state = excluded.state,
                    item_count = excluded.item_count,
                    diagnostic = excluded.diagnostic
                """,
                (
                    run_id,
                    source_type,
                    source_id,
                    source_url,
                    _iso(retrieved_at),
                    state,
                    item_count,
                    redact_diagnostic(diagnostic) if diagnostic else None,
                ),
            )

    def save_draft(self, run_id: str, draft: DigestDraft) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO drafts(id, run_id, content_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    draft.id,
                    run_id,
                    draft.model_dump_json(),
                    _iso(datetime.now(UTC)),
                ),
            )

    def set_run_status(self, run_id: str, status: RunStatus) -> RunRecord:
        current = self.get_run(run_id)
        if status not in ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(f"invalid run transition {current.status} to {status}")
        with self.database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE runs SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (status, _iso(datetime.now(UTC)), run_id, current.status),
            ).rowcount
            if changed != 1:
                raise RuntimeError("run state changed concurrently")
        return self.get_run(run_id)

    def fail_run(self, run_id: str, diagnostic: str) -> RunRecord:
        diagnostic = redact_diagnostic(diagnostic)
        current = self.get_run(run_id)
        if RunStatus.FAILED not in ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(f"cannot fail run in {current.status} state")
        with self.database.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE runs
                SET status = ?, diagnostic = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    RunStatus.FAILED,
                    diagnostic,
                    _iso(datetime.now(UTC)),
                    run_id,
                    current.status,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("run state changed during failure recording")
        return self.get_run(run_id)

    def promote_draft(
        self, run_id: str, fault: FaultHook | None = None
    ) -> PublishedDigest:
        inject = fault or (lambda _stage: None)
        published_at = datetime.now(UTC)
        digest_id = str(uuid4())
        with self.database.transaction() as connection:
            run = connection.execute(
                """
                SELECT r.*, e.status AS execution_status, e.claim_owner,
                       e.claim_expiry, e.fence_generation AS execution_fence
                FROM runs r
                JOIN scheduled_executions e ON e.id = r.scheduled_execution_id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None or run["status"] != RunStatus.VALIDATING:
                raise ValueError("run is not ready for publication")
            if (
                run["execution_status"] == "published"
                or run["claim_owner"] is None
                or run["claim_expiry"] <= _iso(published_at)
                or run["execution_fence"] != run["fence_generation"]
            ):
                raise ValueError("scheduled execution claim is not current")

            connection.execute(
                """
                INSERT INTO publication_locks(
                    scheduled_execution_id, owner, fence_generation, lease_expiry
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    run["scheduled_execution_id"],
                    run["claim_owner"],
                    run["fence_generation"],
                    run["claim_expiry"],
                ),
            )
            inject("after_lock")

            draft_row = connection.execute(
                "SELECT content_json FROM drafts WHERE run_id = ?", (run_id,)
            ).fetchone()
            if draft_row is None:
                raise ValueError("run has no draft")
            draft = DigestDraft.model_validate_json(draft_row["content_json"])
            sources = self._validated_sources(connection, run_id, draft)
            inject("after_validation")

            connection.execute(
                """
                INSERT INTO published_digests(
                    id, source_run_id, scheduled_execution_id, market_window,
                    title, summary, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest_id,
                    run_id,
                    run["scheduled_execution_id"],
                    draft.market_window,
                    draft.title,
                    draft.summary,
                    _iso(published_at),
                ),
            )
            for position, item in enumerate(draft.items):
                source = sources[item.source_item_id]
                connection.execute(
                    """
                    INSERT INTO published_digest_items(
                        digest_id, position, headline, summary,
                        source_name, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        digest_id,
                        position,
                        item.headline,
                        item.summary,
                        source["source_name"],
                        source["url"],
                    ),
                )
            inject("after_digest_insert")

            changed = connection.execute(
                """
                UPDATE runs
                SET status = ?, published_digest_id = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    RunStatus.PUBLISHED,
                    digest_id,
                    _iso(published_at),
                    run_id,
                    RunStatus.VALIDATING,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("run state changed during publication")
            inject("after_run_update")

            connection.execute(
                """
                UPDATE scheduled_executions SET status = 'published'
                WHERE id = ? AND status != 'published'
                  AND fence_generation = ? AND claim_owner = ?
                """,
                (
                    run["scheduled_execution_id"],
                    run["fence_generation"],
                    run["claim_owner"],
                ),
            )
            inject("before_commit")

        digest = self.get_published(digest_id)
        if digest is None:
            raise RuntimeError("published digest is not readable")
        return digest

    def get_run(self, run_id: str) -> RunRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_record(row)

    def get_run_context(self, run_id: str) -> RunContext:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, e.market_window, e.scheduled_time_utc,
                       e.run_request_id, e.window_start_utc, e.window_end_utc,
                       e.max_future_skew_seconds
                FROM runs r
                JOIN scheduled_executions e ON e.id = r.scheduled_execution_id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunContext(
            run=self._run_record(row),
            market_window=row["market_window"],
            scheduled_time_utc=_datetime(row["scheduled_time_utc"]),
            run_request_id=row["run_request_id"],
            window_start_utc=(
                _datetime(row["window_start_utc"])
                if row["window_start_utc"]
                else None
            ),
            window_end_utc=(
                _datetime(row["window_end_utc"])
                if row["window_end_utc"]
                else None
            ),
            max_future_skew_seconds=row["max_future_skew_seconds"],
        )

    def latest_run(self) -> RunRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self._run_record(row) if row else None

    def record_catalog_validation(
        self,
        *,
        catalog_version: str,
        validated_at: datetime,
        results: list[dict[str, object]],
    ) -> dict[str, object]:
        safe_results = [
            {
                "source_id": str(result["source_id"]),
                "valid": bool(result["valid"]),
                "item_count": int(result["item_count"]),
            }
            for result in results
        ]
        valid = bool(safe_results) and all(
            result["valid"] for result in safe_results
        )
        payload = {
            "catalog_version": catalog_version,
            "validated_at": _iso(validated_at),
            "valid": valid,
            "results": safe_results,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO rss_catalog_validations(
                    catalog_version, validated_at, valid, result_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    catalog_version,
                    payload["validated_at"],
                    int(valid),
                    json.dumps(safe_results, separators=(",", ":")),
                ),
            )
        return payload

    def latest_catalog_validation(self) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT catalog_version, validated_at, valid, result_json
                FROM rss_catalog_validations ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "catalog_version": row["catalog_version"],
            "validated_at": row["validated_at"],
            "valid": bool(row["valid"]),
            "results": json.loads(row["result_json"]),
        }

    def get_published(self, digest_id: str) -> PublishedDigest | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT d.*
                FROM published_digests d
                JOIN runs r ON r.id = d.source_run_id
                JOIN scheduled_executions e ON e.id = d.scheduled_execution_id
                WHERE d.id = ? AND r.status = ? AND r.published_digest_id = d.id
                  AND e.status = 'published'
                  AND EXISTS (
                      SELECT 1 FROM published_digest_items i
                      WHERE i.digest_id = d.id
                  )
                """,
                (digest_id, RunStatus.PUBLISHED),
            ).fetchone()
            return self._published_from_row(connection, row) if row else None

    def list_published(self, limit: int = 20) -> list[PublishedDigest]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*
                FROM published_digests d
                JOIN runs r ON r.id = d.source_run_id
                JOIN scheduled_executions e ON e.id = d.scheduled_execution_id
                WHERE r.status = ? AND r.published_digest_id = d.id
                  AND e.status = 'published'
                  AND EXISTS (
                      SELECT 1 FROM published_digest_items i
                      WHERE i.digest_id = d.id
                  )
                ORDER BY d.published_at DESC
                LIMIT ?
                """,
                (RunStatus.PUBLISHED, limit),
            ).fetchall()
            return [self._published_from_row(connection, row) for row in rows]

    def reconcile(self, now: datetime) -> ReconciliationResult:
        now_text = _iso(now)
        with self.database.transaction() as connection:
            abandoned = connection.execute(
                """
                UPDATE runs
                SET status = ?, diagnostic = 'run interrupted by restart',
                    updated_at = ?
                WHERE status IN (?, ?, ?, ?)
                  AND scheduled_execution_id IN (
                      SELECT id FROM scheduled_executions
                      WHERE status != 'published' AND claim_expiry <= ?
                  )
                """,
                (
                    RunStatus.FAILED,
                    now_text,
                    RunStatus.QUEUED,
                    RunStatus.FETCHING,
                    RunStatus.GENERATING,
                    RunStatus.VALIDATING,
                    now_text,
                ),
            ).rowcount
            connection.execute(
                """
                UPDATE scheduled_executions
                SET status = 'pending', claim_owner = NULL, claim_expiry = NULL
                WHERE status = 'claimed'
                  AND claim_expiry <= ?
                  AND id IN (
                      SELECT scheduled_execution_id FROM runs
                      WHERE status = ?
                        AND diagnostic = 'run interrupted by restart'
                  )
                """,
                (now_text, RunStatus.FAILED),
            )

            repaired = connection.execute(
                """
                UPDATE runs
                SET status = ?, published_digest_id = (
                    SELECT id FROM published_digests
                    WHERE source_run_id = runs.id
                ), updated_at = ?
                WHERE status = ?
                  AND EXISTS (
                      SELECT 1 FROM published_digests d
                      WHERE d.source_run_id = runs.id
                        AND EXISTS (
                            SELECT 1 FROM published_digest_items i
                            WHERE i.digest_id = d.id
                        )
                  )
                """,
                (
                    RunStatus.PUBLISHED,
                    now_text,
                    RunStatus.VALIDATING,
                ),
            ).rowcount
            connection.execute(
                """
                UPDATE scheduled_executions SET status = 'published'
                WHERE id IN (
                    SELECT scheduled_execution_id FROM runs
                    WHERE status = ?
                )
                """,
                (RunStatus.PUBLISHED,),
            )

            incomplete = connection.execute(
                """
                UPDATE runs
                SET status = ?, diagnostic = 'published digest is incomplete',
                    published_digest_id = NULL, updated_at = ?
                WHERE status = ?
                  AND (
                      published_digest_id IS NULL
                      OR NOT EXISTS (
                          SELECT 1 FROM published_digests d
                          WHERE d.id = runs.published_digest_id
                            AND EXISTS (
                                SELECT 1 FROM published_digest_items i
                                WHERE i.digest_id = d.id
                            )
                      )
                  )
                """,
                (
                    RunStatus.FAILED,
                    now_text,
                    RunStatus.PUBLISHED,
                ),
            ).rowcount
            if incomplete:
                connection.execute(
                    """
                    UPDATE scheduled_executions SET status = 'claimed'
                    WHERE id IN (
                        SELECT scheduled_execution_id FROM runs
                        WHERE status = ? AND diagnostic = 'published digest is incomplete'
                    )
                    """,
                    (RunStatus.FAILED,),
                )

            failed_runs = abandoned + incomplete
            connection.execute(
                """
                INSERT INTO reconciliation_events(
                    occurred_at, failed_runs, repaired_runs
                ) VALUES (?, ?, ?)
                """,
                (now_text, failed_runs, repaired),
            )
        return ReconciliationResult(
            failed_runs=failed_runs,
            repaired_runs=repaired,
        )

    @staticmethod
    def _validated_sources(
        connection: Connection, run_id: str, draft: DigestDraft
    ) -> dict[str, Row]:
        rows = connection.execute(
            "SELECT * FROM intake_items WHERE run_id = ?", (run_id,)
        ).fetchall()
        sources = {row["id"]: row for row in rows}
        missing = {
            item.source_item_id
            for item in draft.items
            if item.source_item_id not in sources
        }
        if missing:
            raise ValueError("draft references missing intake provenance")
        return sources

    @staticmethod
    def _run_record(row: Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            scheduled_execution_id=row["scheduled_execution_id"],
            fence_generation=row["fence_generation"],
            status=RunStatus(row["status"]),
            diagnostic=row["diagnostic"],
            published_digest_id=row["published_digest_id"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _scheduled_execution_record(row: Row) -> ScheduledExecutionRecord:
        return ScheduledExecutionRecord(
            id=row["id"],
            market_window=row["market_window"],
            scheduled_time_utc=_datetime(row["scheduled_time_utc"]),
            status=row["status"],
            claim_owner=row["claim_owner"],
            claim_expiry=(
                _datetime(row["claim_expiry"]) if row["claim_expiry"] else None
            ),
            fence_generation=row["fence_generation"],
            attempt_count=row["attempt_count"],
        )

    @staticmethod
    def _published_from_row(
        connection: Connection, row: Row
    ) -> PublishedDigest:
        items = connection.execute(
            """
            SELECT item.headline, item.summary, item.source_name,
                   item.source_url, intake.published_at
            FROM published_digest_items AS item
            JOIN published_digests AS digest ON digest.id = item.digest_id
            LEFT JOIN intake_items AS intake
              ON intake.run_id = digest.source_run_id
             AND intake.url = item.source_url
            WHERE item.digest_id = ? ORDER BY item.position
            """,
            (row["id"],),
        ).fetchall()
        return PublishedDigest(
            id=row["id"],
            source_run_id=row["source_run_id"],
            market_window=row["market_window"],
            title=row["title"],
            summary=row["summary"],
            published_at=_datetime(row["published_at"]),
            items=[PublishedDigestItem.model_validate(dict(item)) for item in items],
        )
