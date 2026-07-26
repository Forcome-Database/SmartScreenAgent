from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.services.sync.ledger import identity_key

NOW = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)


def test_identity_includes_the_content_hash() -> None:
    first = identity_key("dingtalk", "cand-1", "a" * 64)
    revised = identity_key("dingtalk", "cand-1", "b" * 64)

    # A candidate who uploads a revised resume must be ingested again.
    assert first != revised


def test_identity_separates_sources() -> None:
    assert identity_key("dingtalk", "1", "a" * 64) != identity_key("boss", "1", "a" * 64)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_identity_parts_are_rejected(blank: str) -> None:
    with pytest.raises(ValueError):
        identity_key("dingtalk", blank, "a" * 64)
