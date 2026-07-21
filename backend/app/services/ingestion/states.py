from __future__ import annotations

from enum import Enum


class IngestionState(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    READY = "ready"
    SCORING = "scoring"
    COMPLETED = "completed"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    DELETED = "deleted"


PROCESSING_STATES: frozenset[IngestionState] = frozenset(
    {IngestionState.PARSING, IngestionState.EXTRACTING, IngestionState.SCORING}
)

TERMINAL_STATES: frozenset[IngestionState] = frozenset(
    {
        IngestionState.READY,
        IngestionState.COMPLETED,
        IngestionState.TERMINAL_FAILED,
        IngestionState.DELETED,
    }
)

_ALLOWED: dict[IngestionState, frozenset[IngestionState]] = {
    IngestionState.QUEUED: frozenset({IngestionState.PARSING, IngestionState.DELETED}),
    IngestionState.PARSING: frozenset(
        {IngestionState.EXTRACTING, IngestionState.RETRYABLE_FAILED, IngestionState.TERMINAL_FAILED}
    ),
    IngestionState.EXTRACTING: frozenset(
        {
            IngestionState.READY,
            IngestionState.SCORING,
            IngestionState.RETRYABLE_FAILED,
            IngestionState.TERMINAL_FAILED,
        }
    ),
    IngestionState.READY: frozenset({IngestionState.DELETED}),
    IngestionState.SCORING: frozenset(
        {IngestionState.COMPLETED, IngestionState.RETRYABLE_FAILED, IngestionState.TERMINAL_FAILED}
    ),
    IngestionState.COMPLETED: frozenset({IngestionState.DELETED}),
    IngestionState.RETRYABLE_FAILED: frozenset(
        {IngestionState.QUEUED, IngestionState.TERMINAL_FAILED}
    ),
    IngestionState.TERMINAL_FAILED: frozenset({IngestionState.DELETED}),
    IngestionState.DELETED: frozenset(),
}


class InvalidTransitionError(RuntimeError):
    def __init__(self, current: IngestionState, target: IngestionState) -> None:
        super().__init__(f"illegal ingestion transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def assert_transition(current: IngestionState, target: IngestionState) -> None:
    if target not in _ALLOWED[current]:
        raise InvalidTransitionError(current, target)
