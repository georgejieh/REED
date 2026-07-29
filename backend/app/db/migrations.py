from __future__ import annotations

from app.db.connection import Database


MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rss_catalog_versions (
    id INTEGER PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rss_sources (
    id TEXT PRIMARY KEY,
    catalog_version_id INTEGER REFERENCES rss_catalog_versions(id),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    UNIQUE (catalog_version_id, url)
);
CREATE TABLE IF NOT EXISTS scheduled_executions (
    id TEXT PRIMARY KEY,
    market_window TEXT NOT NULL,
    scheduled_time_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    claim_owner TEXT,
    claim_expiry TEXT,
    fence_generation INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (market_window, scheduled_time_utc)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    scheduled_execution_id TEXT NOT NULL REFERENCES scheduled_executions(id),
    fence_generation INTEGER NOT NULL,
    status TEXT NOT NULL,
    diagnostic TEXT,
    published_digest_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    published_at TEXT NOT NULL,
    UNIQUE (run_id, url)
);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS published_digests (
    id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
    scheduled_execution_id TEXT NOT NULL UNIQUE REFERENCES scheduled_executions(id),
    market_window TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS published_digest_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_id TEXT NOT NULL REFERENCES published_digests(id),
    position INTEGER NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    UNIQUE (digest_id, position)
);
CREATE TABLE IF NOT EXISTS scheduler_leases (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    lease_expiry TEXT NOT NULL,
    fence_generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_locks (
    scheduled_execution_id TEXT PRIMARY KEY REFERENCES scheduled_executions(id),
    owner TEXT NOT NULL,
    fence_generation INTEGER NOT NULL,
    lease_expiry TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    failed_runs INTEGER NOT NULL,
    repaired_runs INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_published_digests_published_at
    ON published_digests(published_at DESC);
CREATE TRIGGER IF NOT EXISTS published_digests_no_update
BEFORE UPDATE ON published_digests
BEGIN
    SELECT RAISE(ABORT, 'published digest is immutable');
END;
CREATE TRIGGER IF NOT EXISTS published_digests_no_delete
BEFORE DELETE ON published_digests
BEGIN
    SELECT RAISE(ABORT, 'published digest is immutable');
END;
CREATE TRIGGER IF NOT EXISTS published_digest_items_no_update
BEFORE UPDATE ON published_digest_items
BEGIN
    SELECT RAISE(ABORT, 'published digest item is immutable');
END;
CREATE TRIGGER IF NOT EXISTS published_digest_items_no_delete
BEFORE DELETE ON published_digest_items
BEGIN
    SELECT RAISE(ABORT, 'published digest item is immutable');
END;
CREATE TRIGGER IF NOT EXISTS published_digest_items_no_insert_after_publish
BEFORE INSERT ON published_digest_items
WHEN EXISTS (
    SELECT 1
    FROM published_digests d
    JOIN runs r ON r.id = d.source_run_id
    WHERE d.id = NEW.digest_id
      AND r.status = 'published'
      AND r.published_digest_id = d.id
)
BEGIN
    SELECT RAISE(ABORT, 'published digest item is immutable');
END;
"""

MIGRATION_2 = """
ALTER TABLE scheduled_executions ADD COLUMN run_request_id TEXT;
ALTER TABLE scheduled_executions ADD COLUMN window_start_utc TEXT;
ALTER TABLE scheduled_executions ADD COLUMN window_end_utc TEXT;
ALTER TABLE scheduled_executions ADD COLUMN max_future_skew_seconds INTEGER;
CREATE TABLE intake_items_v2 (
    id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    published_at TEXT NOT NULL,
    feed_id TEXT,
    source_url TEXT,
    retrieved_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    validation_outcome TEXT NOT NULL DEFAULT 'valid',
    PRIMARY KEY (run_id, id),
    UNIQUE (run_id, url)
);
INSERT INTO intake_items_v2(
    id, run_id, title, url, source_name, published_at
)
SELECT id, run_id, title, url, source_name, published_at
FROM intake_items;
DROP TABLE intake_items;
ALTER TABLE intake_items_v2 RENAME TO intake_items;
CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduled_run_request
    ON scheduled_executions(run_request_id)
    WHERE run_request_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS source_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    state TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    diagnostic TEXT,
    UNIQUE (run_id, source_type, source_id)
);
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS rss_catalog_validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_version TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
    result_json TEXT NOT NULL
);
"""


def migrate(database: Database) -> None:
    with database.connect() as connection:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + MIGRATION_1
            + """
            INSERT OR IGNORE INTO schema_migrations(version, applied_at)
            VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
            COMMIT;
            """
        )
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        if version < 2:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_2
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
            version = 2
        if version < 3:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_3
                + """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                COMMIT;
                """
            )
