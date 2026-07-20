import pytest

from backend.app.services.ingestion.states import (
    PROCESSING_STATES,
    TERMINAL_STATES,
    IngestionState,
    InvalidTransitionError,
    assert_transition,
)


def test_processing_and_terminal_sets():
    assert PROCESSING_STATES == frozenset(
        {IngestionState.PARSING, IngestionState.EXTRACTING, IngestionState.SCORING}
    )
    assert IngestionState.COMPLETED in TERMINAL_STATES
    assert IngestionState.TERMINAL_FAILED in TERMINAL_STATES
    assert IngestionState.QUEUED not in TERMINAL_STATES


@pytest.mark.parametrize(
    "current,target",
    [
        (IngestionState.QUEUED, IngestionState.PARSING),
        (IngestionState.PARSING, IngestionState.EXTRACTING),
        (IngestionState.EXTRACTING, IngestionState.READY),
        (IngestionState.EXTRACTING, IngestionState.SCORING),
        (IngestionState.SCORING, IngestionState.COMPLETED),
        (IngestionState.PARSING, IngestionState.RETRYABLE_FAILED),
        (IngestionState.RETRYABLE_FAILED, IngestionState.QUEUED),
        (IngestionState.RETRYABLE_FAILED, IngestionState.TERMINAL_FAILED),
        (IngestionState.SCORING, IngestionState.TERMINAL_FAILED),
    ],
)
def test_allowed_transitions(current, target):
    assert_transition(current, target)  # no raise


@pytest.mark.parametrize(
    "current,target",
    [
        (IngestionState.QUEUED, IngestionState.COMPLETED),
        (IngestionState.COMPLETED, IngestionState.PARSING),
        (IngestionState.TERMINAL_FAILED, IngestionState.QUEUED),
        (IngestionState.PARSING, IngestionState.COMPLETED),
    ],
)
def test_illegal_transitions_raise(current, target):
    with pytest.raises(InvalidTransitionError):
        assert_transition(current, target)
