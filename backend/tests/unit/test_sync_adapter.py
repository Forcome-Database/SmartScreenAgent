from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.services.sync.adapter import (
    ItemUnavailable,
    SourceCapabilityUnavailable,
    SourceItem,
    SourceUnavailable,
)

AWARE = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)


def _build(updated_at: datetime) -> SourceItem:
    return SourceItem(
        external_id="cand-1",
        updated_at=updated_at,
        filename="resume.pdf",
        content_type="application/pdf",
        jd_code=None,
    )


def test_an_aware_updated_at_is_accepted() -> None:
    assert _build(AWARE).updated_at == AWARE


def test_a_naive_updated_at_is_rejected_at_the_port() -> None:
    # The cursor compares this against a timezone-aware instant. Left to fail
    # there, the TypeError lands after the run has already committed its items
    # — no cursor written, no `resume_sync_completed` audit, so a run that
    # ingested 40 resumes leaves no record it happened. An adapter that builds
    # a bad item must fail on the item, not on the run.
    with pytest.raises(ValueError, match="timezone-aware"):
        _build(datetime(2026, 7, 27, 5, 0))


def test_a_missing_capability_is_not_a_failed_item() -> None:
    # The replay sweeper spends one of an item's bounded attempts on
    # `ItemUnavailable` and spends nothing on this. Were the capability signal a
    # subclass, an adapter that simply cannot look items up by id would burn
    # every failed row's attempts to zero and make them all terminal — silent,
    # permanent data loss from a missing feature rather than a real failure.
    assert not issubclass(SourceCapabilityUnavailable, ItemUnavailable)
    assert not issubclass(SourceCapabilityUnavailable, SourceUnavailable)
    assert issubclass(SourceCapabilityUnavailable, Exception)


def test_the_port_can_re_derive_an_item_from_its_external_id_alone() -> None:
    """`describe` is what makes bounded replay possible.

    A failed ledger row stores only `source_external_id` — the cursor has moved
    past it and the overlap will not reach back, so re-listing cannot find it.
    Without this method the sweeper has no way to obtain an item to `fetch`.
    """
    from backend.app.services.sync.adapter import ResumeSourceAdapter

    # `__protocol_attrs__` is 3.12+; this repo supports 3.10.
    assert callable(getattr(ResumeSourceAdapter, "describe", None))
