from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.sync.adapter import SourceItem
from backend.app.services.sync.ledger import next_cursor, overlap_start, write_cursor

NOW = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)


def _item(external_id: str, updated_at: datetime) -> SourceItem:
    return SourceItem(
        external_id=external_id,
        updated_at=updated_at,
        filename="resume.pdf",
        content_type="application/pdf",
        jd_code=None,
    )


def test_overlap_looks_back_by_the_configured_window() -> None:
    assert overlap_start(NOW, 300) == NOW - timedelta(seconds=300)


def test_zero_overlap_starts_exactly_at_the_cursor() -> None:
    assert overlap_start(NOW, 0) == NOW


def test_naive_cursor_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        overlap_start(datetime(2026, 7, 27, 5, 0), 300)


def test_cursor_advances_to_the_last_processed_item_not_now() -> None:
    processed = [
        _item("a", NOW - timedelta(minutes=30)),
        _item("b", NOW - timedelta(minutes=10)),
    ]

    # Advancing to `now` would skip anything the source stamped between the
    # last processed item and now but had not yet listed.
    assert next_cursor(NOW - timedelta(hours=1), processed) == NOW - timedelta(minutes=10)


def test_cursor_uses_the_maximum_not_the_last_in_order() -> None:
    processed = [
        _item("a", NOW - timedelta(minutes=10)),
        _item("b", NOW - timedelta(minutes=30)),
    ]

    assert next_cursor(NOW - timedelta(hours=1), processed) == NOW - timedelta(minutes=10)


def test_cursor_never_moves_backwards() -> None:
    processed = [_item("a", NOW - timedelta(days=2))]

    assert next_cursor(NOW, processed) == NOW


def test_empty_run_leaves_the_cursor_untouched() -> None:
    assert next_cursor(NOW, []) == NOW


def test_naive_current_cursor_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_cursor(datetime(2026, 7, 27, 5, 0), [])


async def test_write_cursor_rejects_a_naive_value_before_touching_the_db() -> None:
    # `db=None` proves the guard fires before any DB access: a naive value
    # must never reach `.astimezone()`, which would silently misinterpret it
    # as local system time and corrupt the stored UTC instant.
    with pytest.raises(ValueError, match="timezone-aware"):
        await write_cursor(
            None,  # type: ignore[arg-type]
            "dingtalk",
            value=datetime(2026, 7, 27, 5, 0),
            now=NOW,
        )
