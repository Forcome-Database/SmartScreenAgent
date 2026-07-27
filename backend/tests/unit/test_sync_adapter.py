from __future__ import annotations

import inspect
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
    Without this method the sweeper has no way to obtain an item to `fetch`,
    and the id is the ONLY thing it can be asked for.
    """
    from backend.app.services.sync.adapter import ResumeSourceAdapter

    # `__protocol_attrs__` is 3.12+ and this repo supports 3.10, but
    # `inspect.signature` pins the shape portably — and the shape is the point:
    # `callable(...)` alone would accept a zero-argument `describe`, which
    # cannot re-derive anything.
    signature = inspect.signature(ResumeSourceAdapter.describe)

    assert list(signature.parameters) == ["self", "external_id"]
    assert signature.parameters["external_id"].annotation == "str"
    assert signature.return_annotation == "SourceItem"


def test_the_port_tells_implementers_what_a_provider_outage_looks_like() -> None:
    """All three failure meanings, named where an implementer will read them.

    `describe` is called once per failed row. An adapter that maps a transport
    error to `ItemUnavailable` — the natural reading of a per-item call — spends
    one attempt per row per sweep during an outage, and about
    `SYNC_MAX_ITEM_ATTEMPTS` sweeps later the entire failed queue is terminal
    with no genuine per-item failure anywhere. The contract has to say so.
    """
    from backend.app.services.sync.adapter import ResumeSourceAdapter

    contract = ResumeSourceAdapter.describe.__doc__ or ""

    assert "ItemUnavailable" in contract
    assert "SourceCapabilityUnavailable" in contract
    assert "SourceUnavailable" in contract
