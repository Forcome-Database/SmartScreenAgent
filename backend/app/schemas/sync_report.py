from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

# Counts, cursors, and a source name only. `source_external_id` is a
# provider-side handle on one real person, and `content_sha256` fingerprints the
# bytes of their resume; neither answers "is sync working, and what is stuck?",
# so neither may ever gain a home in this file — nor may a filename, an object
# key, a ciphertext, or any extracted candidate field.
#
# This is one clause of the WP8 `external_id` policy, which is one rule across
# four modules: a `source_external_id` MAY be stored in the ledger and MAY
# appear in a structlog line; it MUST NOT reach an audit payload, an API
# response (this file), or an exception message. The other three are
# `services/sync/runner.py`, `services/sync/replay.py`, and
# `services/sync/dingtalk.py`.


class SyncSourceReport(BaseModel):
    """One source's position and the state of its ledger.

    The two failure counts are split at `SYNC_MAX_ITEM_ATTEMPTS` because the
    replay sweeper selects on `attempts < max_attempts`: a row below the bound
    is still queued for automatic recovery, and a row at or above it will never
    be looked at again by anything. A single `failed_total` would tell an
    operator a number without telling them whether it is theirs to act on.
    """

    source: str
    # NULL until the source's first run finishes. `run_sync` writes the cursor
    # last, so a source can have ledger rows and no cursor: that combination
    # means every run so far aborted, and it must be visible, not hidden.
    cursor_value: str | None
    last_run_at: datetime | None
    ingested_total: int
    failed_retrying_total: int
    failed_terminal_total: int


class SyncReportResponse(BaseModel):
    items: list[SyncSourceReport]
    # The bound the split above was taken at, so the two counts are readable
    # without also knowing the deployment's configuration.
    max_item_attempts: int
