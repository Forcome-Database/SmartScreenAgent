from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.models import SyncCursor, SyncSourceItem

pytestmark = pytest.mark.integration

REPORT = "/api/v1/sync/report"
NOW = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)
SOURCE = "dingtalk_recruitment"

# The bound the sweeper selects on. Read from settings rather than hardcoded to 3:
# the split between "still retrying" and "given up" IS this number, so a test that
# assumed its own copy would keep passing after an operator changed it.
MAX_ATTEMPTS = get_settings().SYNC_MAX_ITEM_ATTEMPTS


def _item(index: int, *, outcome: str, attempts: int) -> SyncSourceItem:
    return SyncSourceItem(
        source=SOURCE,
        source_external_id=f"cand-{index}",
        content_sha256=f"{index}" * 64,
        outcome=outcome,
        attempts=attempts,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


async def _seed(db: AsyncSession) -> None:
    db.add(
        SyncCursor(
            source=SOURCE,
            cursor_value=NOW.isoformat(),
            last_run_at=NOW,
            updated_at=NOW,
        )
    )
    db.add(_item(0, outcome="ingested", attempts=0))
    db.add(_item(1, outcome="ingested", attempts=0))
    # Inside the bound: the sweeper will select these again.
    db.add(_item(2, outcome="failed", attempts=MAX_ATTEMPTS - 1))
    # At the bound: terminal, never selected again, needs a human.
    db.add(_item(3, outcome="failed", attempts=MAX_ATTEMPTS))
    db.add(_item(4, outcome="failed", attempts=MAX_ATTEMPTS + 1))
    await db.commit()


async def test_report_requires_a_read_role(client, db_session, auth_headers) -> None:
    assert (await client.get(REPORT)).status_code == 401
    assert (await client.get(REPORT, headers=await auth_headers("viewer"))).status_code == 403
    for role in ("hr", "hr_lead", "admin"):
        assert (await client.get(REPORT, headers=await auth_headers(role))).status_code == 200


async def test_report_splits_failures_at_the_attempt_bound(
    client, db_session, auth_headers
) -> None:
    await _seed(db_session)

    body = (await client.get(REPORT, headers=await auth_headers("hr"))).json()
    row = next(item for item in body["items"] if item["source"] == SOURCE)

    assert body["max_item_attempts"] == MAX_ATTEMPTS
    assert row["ingested_total"] == 2
    # One row is still under the bound; two have reached or passed it.
    assert row["failed_retrying_total"] == 1
    assert row["failed_terminal_total"] == 2
    assert row["cursor_value"] == NOW.isoformat()
    # `Z` is spelled out because Python 3.10's `fromisoformat` cannot read it.
    assert datetime.fromisoformat(row["last_run_at"].replace("Z", "+00:00")) == NOW


async def test_report_lists_a_source_that_has_only_a_cursor(
    client, db_session, auth_headers
) -> None:
    # A run that listed nothing writes a cursor and no ledger rows. It is the
    # "sync is working, there was nothing to take" case, and an operator asking
    # "is sync working?" must still see the source and its last run.
    db_session.add(
        SyncCursor(
            source=SOURCE,
            cursor_value=NOW.isoformat(),
            last_run_at=NOW,
            updated_at=NOW,
        )
    )
    await db_session.commit()

    body = (await client.get(REPORT, headers=await auth_headers("hr"))).json()
    row = next(item for item in body["items"] if item["source"] == SOURCE)

    assert (row["ingested_total"], row["failed_retrying_total"]) == (0, 0)
    assert row["failed_terminal_total"] == 0
    assert row["last_run_at"] is not None


async def test_report_lists_a_source_whose_first_run_never_wrote_a_cursor(
    client, db_session, auth_headers
) -> None:
    # `run_sync` deliberately leaves the cursor unwritten when a run aborts, so
    # a source's very first run can leave ledger rows and no cursor row at all.
    # Reporting only what `sync_cursors` knows would hide exactly that failure.
    db_session.add(_item(9, outcome="failed", attempts=1))
    await db_session.commit()

    body = (await client.get(REPORT, headers=await auth_headers("hr"))).json()
    row = next(item for item in body["items"] if item["source"] == SOURCE)

    assert (row["cursor_value"], row["last_run_at"]) == (None, None)
    assert row["failed_retrying_total"] == 1


async def test_report_carries_no_candidate_identifiers(client, db_session, auth_headers) -> None:
    await _seed(db_session)

    body = (await client.get(REPORT, headers=await auth_headers("hr"))).json()

    # Structural, not substring-based: the response may carry exactly these keys.
    # `source_external_id` is a provider-side handle on one real person and has no
    # place in a counts report, and no candidate field can be added later without
    # this failing. A substring sweep would not catch a renamed leak.
    assert set(body) == {"items", "max_item_attempts"}
    assert set(body["items"][0]) == {
        "source",
        "cursor_value",
        "last_run_at",
        "ingested_total",
        "failed_retrying_total",
        "failed_terminal_total",
    }


async def test_report_counts_are_scoped_per_source(client, db_session, auth_headers) -> None:
    await _seed(db_session)
    other = SyncSourceItem(
        source="other_source",
        source_external_id="cand-0",
        content_sha256="f" * 64,
        outcome="ingested",
        attempts=0,
        first_seen_at=NOW + timedelta(minutes=1),
        last_seen_at=NOW + timedelta(minutes=1),
    )
    db_session.add(other)
    await db_session.commit()

    body = (await client.get(REPORT, headers=await auth_headers("hr"))).json()

    assert [item["source"] for item in body["items"]] == [SOURCE, "other_source"]
    assert body["items"][1]["ingested_total"] == 1
    assert body["items"][1]["failed_retrying_total"] == 0
