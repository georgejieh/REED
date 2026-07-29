from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    GENERATING = "generating"
    VALIDATING = "validating"
    PUBLISHED = "published"
    FAILED = "failed"


ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.FETCHING, RunStatus.FAILED},
    RunStatus.FETCHING: {RunStatus.GENERATING, RunStatus.FAILED},
    RunStatus.GENERATING: {RunStatus.VALIDATING, RunStatus.FAILED},
    RunStatus.VALIDATING: {RunStatus.PUBLISHED, RunStatus.FAILED},
    RunStatus.PUBLISHED: set(),
    RunStatus.FAILED: set(),
}
