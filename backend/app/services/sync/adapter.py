from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class SourceItem:
    """One changed candidate as the source describes it, before download."""

    external_id: str
    updated_at: datetime
    filename: str
    content_type: str
    jd_code: str | None

    def __post_init__(self) -> None:
        # The cursor compares `updated_at` against a timezone-aware instant. A
        # naive one raises TypeError there — after the whole run has already
        # committed its items, leaving no cursor and no completed audit. The
        # port rejects it here instead, before a single item is processed.
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("SourceItem.updated_at must be timezone-aware")


@dataclass(frozen=True)
class FetchedResume:
    content: bytes
    sha256: str
    filename: str
    content_type: str


@dataclass(frozen=True)
class JobMeta:
    """One JD as the source describes it: identity and descriptive text only.

    Deliberately has no `status` and no rule-version field. Design §10 permits
    JD metadata sync to create a JD and refresh `name`/`description`, and
    forbids it from touching `active_rule_version_id`, `rule_versions.schema_json`,
    or `jds.status` — WP6c's gated publish workflow owns those. Keeping this
    type narrow means there is nothing forbidden for a caller to even read off
    of it, let alone write.
    """

    code: str
    name: str
    description: str


class SourceUnavailable(Exception):
    """Listing failed: credentials, permission, or the provider itself."""


class ItemUnavailable(Exception):
    """One item could not be fetched; the rest of the run continues."""


class SourceCapabilityUnavailable(Exception):
    """The adapter cannot answer this kind of question at all.

    Deliberately NOT a subclass of `ItemUnavailable`. That one means "this item
    failed", and the replay sweeper answers it by spending one of the item's
    bounded attempts. This means "this source has no such lookup", which is a
    fact about the adapter, not about any item — and the remedy is to build the
    binding, not to retry.

    Conflating them would be silent data loss: an adapter that simply cannot
    look items up by id would burn every failed row's attempts down to the
    bound and make them all terminal, destroying exactly the rows the sweeper
    exists to rescue, without a single real failure having occurred.
    """


class ResumeSourceAdapter(Protocol):
    """A resume origin. Implementations own endpoints; nothing else may."""

    source_name: str

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]: ...

    async def fetch(self, item: SourceItem) -> FetchedResume: ...

    async def describe(self, external_id: str) -> SourceItem:
        """Re-derive one item from the source's own identifier.

        This is the only way a failed ledger row can be re-driven. Design §9:
        "A failed item cannot be rediscovered by the cursor — the cursor has
        moved past it and the 300-second overlap will not reach back." The
        ledger keeps `source_external_id` and nothing else usable — no download
        URL (a pre-signed URL is both candidate-identifying and short-lived)
        and no source timestamp — so replay must ask by id.

        Asking by id also means a re-attempt does not require the source's
        `updateTime` to have moved, which matters because the most common real
        recovery is a recruiter attaching the resume later, an edit that need
        not bump the candidate record's timestamp.

        AN IMPLEMENTATION MUST ALSO RECORD WHATEVER `fetch` NEEDS TO REACH THE
        CONTENT. `SourceItem` carries no transport detail by design, so an
        adapter that keeps download URLs in a side-map filled only by
        `list_changed` will return an item its own `fetch` cannot resolve: on
        the replay path the adapter instance is fresh and that map is empty, so
        every described row raises `ItemUnavailable`, spends an attempt, and
        across a sweep drives the whole failed queue terminal — which is the
        permanent silent loss `SourceCapabilityUnavailable` exists to prevent,
        re-entered through another door. Returning a well-formed `SourceItem`
        is only half of satisfying this method.

        Three failures, three meanings, and the sweeper answers each one
        differently — so an implementation that conflates them destroys data:

        - `ItemUnavailable` — the source has the lookup and answered "not
          here". A real per-item failure; the sweeper spends one of that item's
          bounded attempts.
        - `SourceCapabilityUnavailable` — this adapter has no such lookup at
          all. A fact about the adapter; the sweeper spends nothing and stops.
        - `SourceUnavailable` — the provider itself is down, or the credential
          is gone. A fact about the source, true of every item and of none in
          particular. The sweeper spends nothing and aborts the pass, exactly
          as `run_sync` does when `list_changed` raises it.

        The last one is the trap. A transport error — connection refused, 500,
        timeout — is NOT `ItemUnavailable`, however per-item the call site
        looks: raised once per row during an outage it burns every failed row's
        attempts down to `SYNC_MAX_ITEM_ATTEMPTS` and makes them all terminal,
        which is permanent silent loss of precisely the rows replay exists to
        rescue, caused by a condition that was about no item at all. Map
        transport failures to `SourceUnavailable` and let the pass abort.
        """
        ...


class JobSourceAdapter(Protocol):
    """A JD-metadata origin — deliberately a second, separate port.

    Not a method added to `ResumeSourceAdapter`. That port is implemented by
    `runner.py`'s `run_sync`, `replay.py`, and `dingtalk.py`, all reviewed and
    approved, and is about resumes: listing changed candidates, fetching one,
    re-deriving one by id. JD metadata is a different capability with a
    different shape (no cursor, no per-item fetch, no replay), and widening the
    resume port would force every existing implementation and test double —
    including the ones already approved — to grow a `list_jobs` it has no use
    for. `DingTalkRecruitmentAdapter` satisfies both ports; nothing requires an
    adapter to.
    """

    source_name: str

    async def list_jobs(self) -> list[JobMeta]: ...
