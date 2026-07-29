from __future__ import annotations

from datetime import UTC, datetime

from app.db.schema import ReconciliationResult
from app.digests.repository import DigestRepository


def reconcile_startup(
    repository: DigestRepository, now: datetime | None = None
) -> ReconciliationResult:
    return repository.reconcile(now or datetime.now(UTC))
