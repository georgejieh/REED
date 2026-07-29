from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.config.configuration import RuntimeConfiguration
from app.db.connection import Database


CONFIGURATION_KEY = "runtime_configuration"


class SettingsStore:
    def __init__(
        self,
        database: Database,
        on_save: Callable[[RuntimeConfiguration], None] | None = None,
    ):
        self.database = database
        self.on_save = on_save

    def load(self) -> RuntimeConfiguration:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key = ?",
                (CONFIGURATION_KEY,),
            ).fetchone()
        if row is None:
            return RuntimeConfiguration()
        return RuntimeConfiguration.model_validate_json(row["value_json"])

    def save(self, configuration: RuntimeConfiguration) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (
                    CONFIGURATION_KEY,
                    configuration.model_dump_json(),
                    timestamp,
                ),
            )
        if self.on_save is not None:
            self.on_save(configuration)
