"""Live probe for the UNVERIFIED DingTalk recruitment endpoints.

Nothing in `backend/app/services/sync/dingtalk.py` has ever been checked against
a real response: the recruitment namespace is absent from the project's DingTalk
OAS (read 2026-07-27) and the API permission has not been granted. The recorded
fixtures under `backend/tests/contracts/dingtalk-recruitment/v1.0/` are
transcriptions of the design document, not evidence.

This module is that evidence, once it can run. It is marked `external_contract`
and so is excluded from the default CI command.

To run it, after the administrator grants the recruitment permission:

    SMARTSCREEN_EXTERNAL_CONTRACT=1 DINGTALK_PROBE_ACCESS_TOKEN=<corp token> \\
        uv run pytest backend/tests/external/test_dingtalk_recruitment_contract.py \\
        -m external_contract

Mint the token with `DingTalkCorpTokenClient` (or the API Explorer) and pass it
in the environment — never commit it, and never paste a response body from this
probe into a fixture without stripping the candidate's name, contact details,
signed URL, and the token itself.

A failure here is informative even when the message is `SourceUnavailable`: the
adapter converts everything, so read the chained `__cause__` in the traceback —
a 404 means the path is wrong, a 403 means the permission is not granted, and a
`ValueError` means the real payload does not match the recorded shape.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import pytest

from backend.app.services.sync.dingtalk import (
    ACCEPTED_SUFFIXES,
    DingTalkRecruitmentAdapter,
)

pytestmark = pytest.mark.external_contract

TOKEN = os.environ.get("DINGTALK_PROBE_ACCESS_TOKEN")


@pytest.mark.skipif(not TOKEN, reason="requires a real corp access token")
@pytest.mark.asyncio
async def test_the_recruitment_endpoint_exists_and_matches_our_parser() -> None:
    adapter = DingTalkRecruitmentAdapter(access_token=TOKEN or "")

    since = datetime.now(timezone.utc) - timedelta(days=7)
    # Returning at all is the first assertion: it proves the path exists, the
    # permission is granted, and the payload parsed against our recorded shape.
    items = await adapter.list_changed(since, 5)

    # We assert shape, not content: this proves our recorded fixtures describe
    # the real API before we trust them.
    for item in items:
        assert item.external_id
        assert item.updated_at.tzinfo is not None
        # The sync path validates on the suffix alone; an item arriving without
        # one would be rejected as `unsupported_attachment` on every run.
        assert PurePosixPath(item.filename).suffix.lower() in ACCEPTED_SUFFIXES


@pytest.mark.skipif(not TOKEN, reason="requires a real corp access token")
@pytest.mark.asyncio
async def test_a_listed_attachment_can_actually_be_downloaded() -> None:
    """The `downloadUrl` contract, which listing alone cannot prove.

    Nothing is asserted about the bytes beyond their length and hash, and
    nothing is written to disk or logged: this downloads a real person's resume.
    """
    adapter = DingTalkRecruitmentAdapter(access_token=TOKEN or "")

    items = await adapter.list_changed(datetime.now(timezone.utc) - timedelta(days=7), 1)
    if not items:
        pytest.skip("no candidate changed in the probe window")

    fetched = await adapter.fetch(items[0])

    assert len(fetched.content) > 0
    assert len(fetched.sha256) == 64
    assert fetched.filename == items[0].filename
