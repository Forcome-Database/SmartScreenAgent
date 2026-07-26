from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.services.sync.adapter import SourceItem

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
