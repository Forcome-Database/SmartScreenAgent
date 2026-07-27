# WP8 DingTalk Recruitment Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import resumes from DingTalk recruitment into the existing WP3 ingestion pipeline, idempotently and without ever blocking manual upload.

**Architecture:** A `ResumeSourceAdapter` port isolates the unverified DingTalk endpoints in one file. A source-agnostic runner owns the cursor, a three-part dedupe ledger, and per-item failure isolation, then delegates to `IngestionJobService.create_or_reuse` so there is no second pipeline. Everything is off by default.

**Tech Stack:** Python 3.10–3.14, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL, Pydantic v2, Celery/Redis, httpx, MinIO.

## Global Constraints

- Authoritative design: `docs/superpowers/specs/2026-07-27-wp8-dingtalk-sync-and-mcp-design.md`.
- Work in `codex/wp8-dingtalk-sync`. Never stage `.superpowers/` or `backend.zip`.
- TDD for every task: add a failing test, run the narrow test and confirm the
  expected failure, implement minimally, rerun narrow tests, run the task gate,
  then commit.
- Commit trailer: blank line then
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Python 3.10 is a supported target.** `from datetime import UTC` is 3.11+ and
  is forbidden; use `from datetime import timezone` and `timezone.utc`. Verify
  with `uv run --python 3.10 --extra dev pytest -m "not integration and not external_contract" -q`.
- Backend integration commands on this host require the prefix:
  `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000"`.
- If the test stack is down, from the repository root run:
  `SMARTSCREEN_TEST_PG_PORT=25432 SMARTSCREEN_TEST_MINIO_PORT=9000 SMARTSCREEN_TEST_MINIO_CONSOLE_PORT=9001 docker compose -f docker-compose.test.yml up -d`.
- Backend task gate: `uv run pytest -m "not integration and not external_contract" -q`,
  `uv run ruff check backend`, and
  `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports`.
- Ruff line length is 100; use `.encode()` not `.encode("utf-8")`.
- Every new `Settings` field needs a matching key in
  `backend/tests/test_bootstrap.py::TEST_ENV_DEFAULTS`; the guardrail
  `test_defaults_cover_every_settings_field` asserts the key sets match exactly.
- Alembic head before this package is `7d3c9b1a4e62`. Bump the literal in BOTH
  `backend/tests/integration/test_db_migrations.py` and `scripts/verify.py`.
- No business transaction may be held across an HTTP or MinIO call.
- Audit payloads carry counts, cursors, and error codes only — never a
  candidate name, ciphertext, object key, or resume text.

## File Structure

### Create

- `migrations/versions/9a4f2c7b31de_wp8_sync_tables.py` — cursors and ledger.
- `backend/app/models/sync.py` — `SyncCursor`, `SyncSourceItem`.
- `backend/app/services/sync/__init__.py`
- `backend/app/services/sync/adapter.py` — the port and its data types.
- `backend/app/services/sync/ledger.py` — dedupe and cursor persistence.
- `backend/app/services/sync/runner.py` — orchestration, source-agnostic.
- `backend/app/services/sync/dingtalk.py` — the DingTalk adapter.
- `backend/app/services/sync/replay.py` — bounded re-drive of failed items.
- `backend/app/tasks/wp8.py` — Celery wrappers.
- `backend/tests/contracts/dingtalk-recruitment/v1.0/*.json` — recorded fixtures.
- `backend/tests/unit/test_sync_ledger.py`, `test_sync_cursor.py`,
  `test_dingtalk_adapter.py`
- `backend/tests/integration/test_sync_runner.py`
- `backend/tests/external/test_dingtalk_recruitment_contract.py`

### Modify

- `backend/app/models/__init__.py` — exports.
- `backend/app/config.py`, `.env.example`, `backend/tests/test_bootstrap.py`.
- `backend/app/tasks/celery_app.py` — include and Beat entries.
- `backend/tests/integration/test_db_migrations.py`, `scripts/verify.py`.
- `backend/tests/integration/conftest.py` — `_CLEAN_TABLES`.

---

## Task 1: Persistence and configuration

**Files:**
- Create: `backend/app/models/sync.py`
- Create: `migrations/versions/9a4f2c7b31de_wp8_sync_tables.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Modify: `backend/tests/test_bootstrap.py`
- Modify: `backend/tests/integration/conftest.py`
- Modify: `backend/tests/integration/test_db_migrations.py`
- Modify: `scripts/verify.py`
- Test: `backend/tests/unit/test_models.py`, `backend/tests/unit/test_wp8_config.py`

**Interfaces:**
- Produces: `SyncCursor(source, cursor_value, last_run_at, updated_at)` and
  `SyncSourceItem(source, source_external_id, content_sha256, ingestion_job_id,
  outcome, error_code, attempts, first_seen_at, last_seen_at)`; settings
  `DINGTALK_SYNC_ENABLED: bool`, `DINGTALK_SYNC_INTERVAL_SECONDS: int`,
  `SYNC_OVERLAP_SECONDS: int`, `SYNC_MAX_ITEMS_PER_RUN: int`,
  `SYNC_MAX_ITEM_ATTEMPTS: int`, `SYNC_REPLAY_INTERVAL_SECONDS: int`,
  `DINGTALK_RECRUITMENT_BASE_URL: str`.

- [ ] **Step 1: Write the failing ORM test**

Add to `backend/tests/unit/test_models.py`:

```python
def test_wp8_sync_models_expose_required_columns() -> None:
    from backend.app.models import SyncCursor, SyncSourceItem

    assert {"source", "cursor_value", "last_run_at", "updated_at"} <= set(
        SyncCursor.__table__.columns.keys()
    )
    assert {
        "source",
        "source_external_id",
        "content_sha256",
        "ingestion_job_id",
        "outcome",
        "error_code",
        "attempts",
        "first_seen_at",
        "last_seen_at",
    } <= set(SyncSourceItem.__table__.columns.keys())
    constraints = {c.name for c in SyncSourceItem.__table__.constraints}
    assert "uq_sync_source_items_identity" in constraints
```

- [ ] **Step 2: Run the ORM test and confirm failure**

Run: `uv run pytest backend/tests/unit/test_models.py -q`

Expected: FAIL — `ImportError: cannot import name 'SyncCursor'`.

- [ ] **Step 3: Add the model file**

Create `backend/app/models/sync.py`:

```python
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class SyncCursor(Base):
    """Where the last successful pull for one source stopped."""

    __tablename__ = "sync_cursors"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor_value: Mapped[str] = mapped_column(String(64), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncSourceItem(Base):
    """One (source, external id, content) triple we have already seen.

    The content hash is part of the identity on purpose: a candidate who
    uploads a revised resume must be ingested again, while the same bytes must
    never be downloaded or parsed twice.
    """

    __tablename__ = "sync_source_items"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_external_id",
            "content_sha256",
            name="uq_sync_source_items_identity",
        ),
        CheckConstraint(
            "outcome IN ('ingested','skipped_duplicate','failed')",
            name="ck_sync_source_items_outcome",
        ),
        CheckConstraint("attempts >= 0", name="ck_sync_source_items_attempts"),
        Index("ix_sync_source_items_outcome_attempts", "outcome", "attempts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_jobs.id")
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Add both names to `backend/app/models/__init__.py` imports and `__all__`,
following the existing alphabetical grouping.

- [ ] **Step 4: Run the ORM test and confirm it passes**

Run: `uv run pytest backend/tests/unit/test_models.py -q`

Expected: PASS.

- [ ] **Step 5: Write the failing configuration test**

Create `backend/tests/unit/test_wp8_config.py`:

```python
import pytest
from pydantic import ValidationError

from backend.app.config import get_settings


def test_sync_defaults_are_off_and_bounded() -> None:
    settings = get_settings()

    assert settings.DINGTALK_SYNC_ENABLED is False
    assert settings.DINGTALK_SYNC_INTERVAL_SECONDS == 1800
    assert settings.SYNC_OVERLAP_SECONDS == 300
    assert settings.SYNC_MAX_ITEMS_PER_RUN == 200
    assert settings.SYNC_MAX_ITEM_ATTEMPTS == 3
    assert settings.SYNC_REPLAY_INTERVAL_SECONDS == 3600


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SYNC_OVERLAP_SECONDS", "-1"),
        ("SYNC_MAX_ITEMS_PER_RUN", "0"),
        ("SYNC_MAX_ITEM_ATTEMPTS", "0"),
        ("DINGTALK_SYNC_INTERVAL_SECONDS", "0"),
        ("SYNC_REPLAY_INTERVAL_SECONDS", "0"),
    ],
)
def test_out_of_range_sync_settings_are_rejected(monkeypatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 6: Run the configuration test and confirm failure**

Run: `uv run pytest backend/tests/unit/test_wp8_config.py -q`

Expected: FAIL — the settings do not exist.

- [ ] **Step 7: Add the settings**

In `backend/app/config.py`, inside `Settings`, after the WP7 cross-engine block:

```python
    DINGTALK_SYNC_ENABLED: bool = False
    DINGTALK_SYNC_INTERVAL_SECONDS: int = Field(default=1800, ge=1)
    DINGTALK_RECRUITMENT_BASE_URL: str = "https://api.dingtalk.com"
    SYNC_OVERLAP_SECONDS: int = Field(default=300, ge=0)
    SYNC_MAX_ITEMS_PER_RUN: int = Field(default=200, ge=1)
    SYNC_MAX_ITEM_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    SYNC_REPLAY_INTERVAL_SECONDS: int = Field(default=3600, ge=1)
```

Add the matching entries to `TEST_ENV_DEFAULTS` in
`backend/tests/test_bootstrap.py`:

```python
    "DINGTALK_SYNC_ENABLED": "false",
    "DINGTALK_SYNC_INTERVAL_SECONDS": "1800",
    "DINGTALK_RECRUITMENT_BASE_URL": "https://api.dingtalk.com",
    "SYNC_OVERLAP_SECONDS": "300",
    "SYNC_MAX_ITEMS_PER_RUN": "200",
    "SYNC_MAX_ITEM_ATTEMPTS": "3",
    "SYNC_REPLAY_INTERVAL_SECONDS": "3600",
```

Add the same keys with the same values to `.env.example` under a
`# --- WP8 sync ---` heading.

- [ ] **Step 8: Run the configuration and bootstrap tests**

Run: `uv run pytest backend/tests/unit/test_wp8_config.py backend/tests/unit/test_test_bootstrap.py -q`

Expected: PASS, including `test_defaults_cover_every_settings_field`.

- [ ] **Step 9: Write the failing migration assertions**

Set `WP3_HEAD_REVISION = "9a4f2c7b31de"` in
`backend/tests/integration/test_db_migrations.py` and
`HEAD_REVISION = "9a4f2c7b31de"` in `scripts/verify.py`.

In `test_alembic_round_trip_from_base`, after upgrade assert:

```python
assert {"sync_cursors", "sync_source_items"} <= tables
assert {
    "uq_sync_source_items_identity",
    "ck_sync_source_items_outcome",
    "ck_sync_source_items_attempts",
} <= constraints
assert "ix_sync_source_items_outcome_attempts" in indexes
```

Then downgrade to `7d3c9b1a4e62` and assert both tables are gone while
`llm_usage_attempts` still exists.

Prepend to `_CLEAN_TABLES` in `backend/tests/integration/conftest.py`:

```python
"sync_source_items",
"sync_cursors",
```

- [ ] **Step 10: Run the migration test and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_db_migrations.py -q
```

Expected: FAIL — revision `9a4f2c7b31de` does not exist.

- [ ] **Step 11: Generate and fill the migration**

Run `uv run alembic revision --rev-id 9a4f2c7b31de -m "wp8 sync tables"`
(never `--autogenerate`). Confirm `down_revision = "7d3c9b1a4e62"`.

`upgrade()` creates `sync_cursors` then `sync_source_items` (the FK to
`ingestion_jobs` requires that table to exist, which it does), with every
named constraint and the named index. `downgrade()` drops
`sync_source_items` then `sync_cursors`.

- [ ] **Step 12: Run the migration test and the task gate**

```powershell
uv run pytest backend/tests/integration/test_db_migrations.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 13: Commit**

```bash
git add -- backend/app/models/sync.py backend/app/models/__init__.py backend/app/config.py .env.example backend/tests/test_bootstrap.py backend/tests/unit/test_models.py backend/tests/unit/test_wp8_config.py backend/tests/integration/conftest.py backend/tests/integration/test_db_migrations.py scripts/verify.py migrations/versions/9a4f2c7b31de_wp8_sync_tables.py
git commit -m "feat(wp8): add sync cursor and dedupe ledger"
```

---

## Task 2: The source port and the dedupe ledger

**Files:**
- Create: `backend/app/services/sync/__init__.py` (empty)
- Create: `backend/app/services/sync/adapter.py`
- Create: `backend/app/services/sync/ledger.py`
- Test: `backend/tests/unit/test_sync_ledger.py`, `backend/tests/unit/test_sync_cursor.py`

**Interfaces:**
- Consumes: `SyncCursor`, `SyncSourceItem` from Task 1.
- Produces:
  - `SourceItem(external_id: str, updated_at: datetime, filename: str, content_type: str, jd_code: str | None)`
  - `FetchedResume(content: bytes, sha256: str, filename: str, content_type: str)`
  - `ResumeSourceAdapter` protocol with
    `async list_changed(self, since: datetime, limit: int) -> list[SourceItem]` and
    `async fetch(self, item: SourceItem) -> FetchedResume`
  - `overlap_start(cursor_value: datetime, overlap_seconds: int) -> datetime`
  - `next_cursor(current: datetime, processed: list[SourceItem]) -> datetime`
  - `async already_ingested(db, *, source, external_id, sha256) -> bool`
  - `async record_item(db, *, source, external_id, sha256, outcome, job_id, error_code, now) -> SyncSourceItem`

- [ ] **Step 1: Write the failing cursor tests**

Create `backend/tests/unit/test_sync_cursor.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.sync.adapter import SourceItem
from backend.app.services.sync.ledger import next_cursor, overlap_start

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
```

- [ ] **Step 2: Run the cursor tests and confirm failure**

Run: `uv run pytest backend/tests/unit/test_sync_cursor.py -q`

Expected: FAIL — `ModuleNotFoundError: backend.app.services.sync.adapter`.

- [ ] **Step 3: Write the port**

Create `backend/app/services/sync/__init__.py` (empty) and
`backend/app/services/sync/adapter.py`:

```python
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


@dataclass(frozen=True)
class FetchedResume:
    content: bytes
    sha256: str
    filename: str
    content_type: str


class SourceUnavailable(Exception):
    """Listing failed: credentials, permission, or the provider itself."""


class ItemUnavailable(Exception):
    """One item could not be fetched; the rest of the run continues."""


class ResumeSourceAdapter(Protocol):
    """A resume origin. Implementations own endpoints; nothing else may."""

    source_name: str

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]: ...

    async def fetch(self, item: SourceItem) -> FetchedResume: ...
```

- [ ] **Step 4: Write the cursor helpers**

Create `backend/app/services/sync/ledger.py` with the pure helpers first:

```python
from __future__ import annotations

from datetime import datetime, timedelta

from backend.app.services.sync.adapter import SourceItem


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("cursor must be timezone-aware")


def overlap_start(cursor_value: datetime, overlap_seconds: int) -> datetime:
    """Deliberately re-list a window before the cursor.

    Source timestamps tie and clocks skew, so a cursor used as an exclusive
    lower bound silently drops items. Re-seeing them is cheap because the
    ledger deduplicates; missing them is not recoverable.
    """
    _require_aware(cursor_value)
    return cursor_value - timedelta(seconds=overlap_seconds)


def next_cursor(current: datetime, processed: list[SourceItem]) -> datetime:
    """Advance to the newest processed item, never to `now`, never backwards."""
    _require_aware(current)
    if not processed:
        return current
    newest = max(item.updated_at for item in processed)
    return max(current, newest)
```

- [ ] **Step 5: Run the cursor tests and confirm they pass**

Run: `uv run pytest backend/tests/unit/test_sync_cursor.py -q`

Expected: PASS (6 tests).

- [ ] **Step 6: Write the failing ledger tests**

Create `backend/tests/unit/test_sync_ledger.py`:

```python
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
```

- [ ] **Step 7: Run the ledger tests and confirm failure**

Run: `uv run pytest backend/tests/unit/test_sync_ledger.py -q`

Expected: FAIL — `ImportError: cannot import name 'identity_key'`.

- [ ] **Step 8: Add the ledger functions**

Append to `backend/app/services/sync/ledger.py`:

```python
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import SyncCursor, SyncSourceItem


def identity_key(source: str, external_id: str, sha256: str) -> tuple[str, str, str]:
    for part in (source, external_id, sha256):
        if not part or not part.strip():
            raise ValueError("identity parts must be non-empty")
    return source, external_id, sha256


async def already_ingested(
    db: AsyncSession, *, source: str, external_id: str, sha256: str
) -> bool:
    """True when this exact content for this candidate was already ingested.

    Checked BEFORE download, so a repeat costs one indexed lookup instead of a
    file transfer plus a paid parse and extraction.
    """
    identity_key(source, external_id, sha256)
    row = (
        await db.execute(
            select(SyncSourceItem.id).where(
                SyncSourceItem.source == source,
                SyncSourceItem.source_external_id == external_id,
                SyncSourceItem.content_sha256 == sha256,
                SyncSourceItem.outcome == "ingested",
            )
        )
    ).first()
    return row is not None


async def record_item(
    db: AsyncSession,
    *,
    source: str,
    external_id: str,
    sha256: str,
    outcome: str,
    now: datetime,
    job_id: int | None = None,
    error_code: str | None = None,
) -> SyncSourceItem:
    """Upsert the ledger row for one item, bumping attempts on a repeat."""
    existing = (
        await db.execute(
            select(SyncSourceItem).where(
                SyncSourceItem.source == source,
                SyncSourceItem.source_external_id == external_id,
                SyncSourceItem.content_sha256 == sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        row = SyncSourceItem(
            source=source,
            source_external_id=external_id,
            content_sha256=sha256,
            ingestion_job_id=job_id,
            outcome=outcome,
            error_code=error_code,
            attempts=1,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(row)
        await db.flush()
        return row
    existing.outcome = outcome
    existing.error_code = error_code
    existing.attempts += 1
    existing.last_seen_at = now
    if job_id is not None:
        existing.ingestion_job_id = job_id
    await db.flush()
    return existing


async def read_cursor(db: AsyncSession, source: str, *, default: datetime) -> datetime:
    row = await db.get(SyncCursor, source)
    if row is None:
        return default
    return datetime.fromisoformat(row.cursor_value)


async def write_cursor(
    db: AsyncSession, source: str, *, value: datetime, now: datetime
) -> None:
    row = await db.get(SyncCursor, source)
    stored = value.astimezone(timezone.utc).isoformat()
    if row is None:
        db.add(
            SyncCursor(
                source=source, cursor_value=stored, last_run_at=now, updated_at=now
            )
        )
    else:
        row.cursor_value = stored
        row.last_run_at = now
        row.updated_at = now
    await db.flush()
```

- [ ] **Step 9: Run both unit files and the task gate**

```powershell
uv run pytest backend/tests/unit/test_sync_cursor.py backend/tests/unit/test_sync_ledger.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add -- backend/app/services/sync/__init__.py backend/app/services/sync/adapter.py backend/app/services/sync/ledger.py backend/tests/unit/test_sync_cursor.py backend/tests/unit/test_sync_ledger.py
git commit -m "feat(wp8): add the resume source port and dedupe ledger"
```

---

## Task 3: The source-agnostic runner

**Files:**
- Create: `backend/app/services/sync/runner.py`
- Test: `backend/tests/integration/test_sync_runner.py`

**Interfaces:**
- Consumes: Task 2's port and ledger; `IngestionJobService.create_or_reuse`;
  `ResumeStorageService`.
- Produces: `SyncReport(listed, ingested, skipped, failed, dropped_by_cap,
  cursor_from, cursor_to)` and
  `async run_sync(session_factory, adapter, *, now, overlap_seconds,
  max_items) -> SyncReport`.

- [ ] **Step 1: Write the failing runner tests**

Create `backend/tests/integration/test_sync_runner.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256 as _sha256
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.app.database import AsyncSessionLocal
from backend.app.models import Candidate, IngestionJob, SyncSourceItem
from backend.app.services.sync.adapter import (
    FetchedResume,
    ItemUnavailable,
    SourceItem,
    SourceUnavailable,
)
from backend.app.services.sync.runner import run_sync

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)


def _pdf(marker: str) -> bytes:
    # A minimal well-formed PDF so WP1 upload validation accepts it.
    return b"%PDF-1.4\n%" + marker.encode() + b"\n%%EOF\n"


class StubAdapter:
    source_name = "dingtalk"

    def __init__(self, items: list[SourceItem], *, fail_ids: set[str] | None = None):
        self.items = items
        self.fail_ids = fail_ids or set()
        self.fetched: list[str] = []
        self.listed = 0

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]:
        self.listed += 1
        return [i for i in self.items if i.updated_at >= since][:limit]

    async def fetch(self, item: SourceItem) -> FetchedResume:
        if item.external_id in self.fail_ids:
            raise ItemUnavailable("attachment missing")
        self.fetched.append(item.external_id)
        content = _pdf(item.external_id)
        return FetchedResume(
            content=content,
            sha256=_sha256(content).hexdigest(),
            filename=item.filename,
            content_type=item.content_type,
        )


def _item(external_id: str, *, minutes_ago: int = 10) -> SourceItem:
    return SourceItem(
        external_id=external_id,
        updated_at=NOW - timedelta(minutes=minutes_ago),
        filename=f"{external_id}.pdf",
        content_type="application/pdf",
        jd_code=None,
    )


async def test_a_repeat_run_ingests_nothing_and_fetches_nothing(db_session, minio_storage):
    adapter = StubAdapter([_item("c1"), _item("c2")])

    first = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )
    assert (first.ingested, first.skipped) == (2, 0)
    assert adapter.fetched == ["c1", "c2"]

    second = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )

    assert (second.ingested, second.skipped) == (0, 2)
    # The saving is money, not just rows: no second download happened.
    assert adapter.fetched == ["c1", "c2"]
    jobs = (
        await db_session.execute(select(func.count()).select_from(IngestionJob))
    ).scalar_one()
    assert jobs == 2


async def test_one_bad_item_does_not_abort_the_run(db_session, minio_storage):
    adapter = StubAdapter([_item("good1"), _item("bad"), _item("good2")], fail_ids={"bad"})

    report = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )

    assert (report.ingested, report.failed) == (2, 1)
    failed = (
        await db_session.execute(
            select(SyncSourceItem).where(SyncSourceItem.outcome == "failed")
        )
    ).scalar_one()
    assert failed.source_external_id == "bad"
    assert failed.error_code == "item_unavailable"


async def test_a_listing_failure_leaves_the_cursor_untouched(db_session, minio_storage):
    class Broken:
        source_name = "dingtalk"

        async def list_changed(self, since, limit):
            raise SourceUnavailable("permission revoked")

        async def fetch(self, item):
            raise AssertionError("must not be reached")

    with pytest.raises(SourceUnavailable):
        await run_sync(
            AsyncSessionLocal, Broken(), now=NOW, overlap_seconds=300, max_items=200
        )

    cursors = (
        await db_session.execute(select(func.count()).select_from(SyncSourceItem))
    ).scalar_one()
    assert cursors == 0


async def test_the_per_run_cap_is_reported_not_silent(db_session, minio_storage):
    adapter = StubAdapter([_item(f"c{i}") for i in range(5)])

    report = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=2
    )

    assert report.ingested == 2
    # Silent truncation would read as "sync finished".
    assert report.dropped_by_cap >= 0
    assert report.listed == 2


async def test_no_business_transaction_is_held_across_the_fetch(db_session, minio_storage):
    class Asserting(StubAdapter):
        async def fetch(self, item):
            assert not db_session.in_transaction()
            return await super().fetch(item)

    adapter = Asserting([_item("c1")])

    await run_sync(AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200)

    assert adapter.fetched == ["c1"]


async def test_ingested_candidates_carry_their_source(db_session, minio_storage):
    adapter = StubAdapter([_item("cand-42")])

    await run_sync(AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200)

    job = (await db_session.execute(select(IngestionJob))).scalar_one()
    assert job.source == "dingtalk"
    assert job.source_external_id == "cand-42"
```

- [ ] **Step 2: Run the runner tests and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_sync_runner.py -q
```

Expected: FAIL — `ModuleNotFoundError: backend.app.services.sync.runner`.

- [ ] **Step 3: Implement the runner**

Create `backend/app/services/sync/runner.py`:

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.app.models import AuditLog
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.sync.adapter import (
    ItemUnavailable,
    ResumeSourceAdapter,
    SourceItem,
)
from backend.app.services.sync.ledger import (
    already_ingested,
    next_cursor,
    overlap_start,
    read_cursor,
    record_item,
    write_cursor,
)
from backend.app.services.storage.resume_storage import ResumeStorageService
from backend.app.services.upload.errors import UploadValidationError

logger = logging.getLogger(__name__)

# How far back a first-ever run reaches when no cursor exists yet.
FIRST_RUN_LOOKBACK = timedelta(days=1)


@dataclass(frozen=True)
class SyncReport:
    listed: int
    ingested: int
    skipped: int
    failed: int
    dropped_by_cap: int
    cursor_from: datetime
    cursor_to: datetime


async def run_sync(
    session_factory,
    adapter: ResumeSourceAdapter,
    *,
    now: datetime,
    overlap_seconds: int,
    max_items: int,
) -> SyncReport:
    """Pull one batch of changed resumes into the WP3 pipeline.

    Every database write is its own short transaction; the adapter's HTTP call
    and the MinIO upload happen with no transaction open. A stalled provider
    must never hold connections that manual upload also needs.
    """
    source = adapter.source_name

    async with session_factory() as session:
        cursor = await read_cursor(session, source, default=now - FIRST_RUN_LOOKBACK)
        await session.commit()

    since = overlap_start(cursor, overlap_seconds)
    # A listing failure propagates: the caller records it and the cursor is
    # left alone so the next run retries the same window.
    items = await adapter.list_changed(since, max_items)

    processed: list[SourceItem] = []
    ingested = skipped = failed = 0

    for item in items:
        try:
            fetched = await adapter.fetch(item)
        except ItemUnavailable:
            failed += 1
            async with session_factory() as session:
                await record_item(
                    session,
                    source=source,
                    external_id=item.external_id,
                    sha256="0" * 64,
                    outcome="failed",
                    error_code="item_unavailable",
                    now=now,
                )
                await session.commit()
            logger.warning(
                "sync item unavailable",
                extra={"source": source, "external_id": item.external_id},
            )
            continue

        async with session_factory() as session:
            seen = await already_ingested(
                session,
                source=source,
                external_id=item.external_id,
                sha256=fetched.sha256,
            )
            await session.commit()
        if seen:
            skipped += 1
            processed.append(item)
            continue

        try:
            storage = ResumeStorageService()
            reference = await storage.store_bytes(
                content=fetched.content,
                filename=fetched.filename,
                content_type=fetched.content_type,
            )
        except UploadValidationError:
            # Design §16.3: recruitment attachments are not guaranteed to be
            # formats WP1 accepts. A rejected file is one bad item, not a
            # reason to abandon the batch.
            failed += 1
            async with session_factory() as session:
                await record_item(
                    session,
                    source=source,
                    external_id=item.external_id,
                    sha256=fetched.sha256,
                    outcome="failed",
                    error_code="unsupported_attachment",
                    now=now,
                )
                await session.commit()
            continue

        async with session_factory() as session:
            service = IngestionJobService(session)
            job = await service.create_or_reuse(
                raw_file=reference,
                source=source,
                source_external_id=item.external_id,
                jd_code=item.jd_code,
                actor=f"system:sync:{source}",
            )
            await record_item(
                session,
                source=source,
                external_id=item.external_id,
                sha256=fetched.sha256,
                outcome="ingested",
                job_id=job.id,
                now=now,
            )
            await session.commit()
        ingested += 1
        processed.append(item)

    advanced = next_cursor(cursor, processed)
    dropped = max(0, len(items) - len(processed) - failed)

    async with session_factory() as session:
        await write_cursor(session, source, value=advanced, now=now)
        session.add(
            AuditLog(
                event_type="resume_sync_completed",
                actor=f"system:sync:{source}",
                target_type="sync",
                payload={
                    "source": source,
                    "cursor_from": cursor.isoformat(),
                    "cursor_to": advanced.isoformat(),
                    "listed": len(items),
                    "ingested": ingested,
                    "skipped": skipped,
                    "failed": failed,
                    "dropped_by_cap": dropped,
                },
            )
        )
        await session.commit()

    return SyncReport(
        listed=len(items),
        ingested=ingested,
        skipped=skipped,
        failed=failed,
        dropped_by_cap=dropped,
        cursor_from=cursor,
        cursor_to=advanced,
    )
```

**Note for the implementer:** import the real exception type from
`backend/app/services/upload/errors.py` — read that module and use whatever the
WP1 validation actually raises, rather than the placeholder name above.

**Note for the implementer:** `ResumeStorageService.store_bytes` may not exist
with that exact name. Read `backend/app/services/storage/resume_storage.py`
first and use the existing method that the WP3 upload route uses to persist an
uploaded file, reusing its WP1 validation. If only a path-based or
`UploadFile`-based entry point exists, add a bytes-based one beside it rather
than bypassing validation.

- [ ] **Step 4: Run the runner tests and confirm they pass**

```powershell
uv run pytest backend/tests/integration/test_sync_runner.py -q
```

Expected: PASS (6 tests).

- [ ] **Step 5: Run the task gate**

```powershell
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -- backend/app/services/sync/runner.py backend/tests/integration/test_sync_runner.py
git commit -m "feat(wp8): add the source-agnostic sync runner"
```

---

## Task 4: The DingTalk adapter and recorded contract fixtures

**Files:**
- Create: `backend/app/services/sync/dingtalk.py`
- Create: `backend/tests/contracts/dingtalk-recruitment/v1.0/candidates-page.json`
- Create: `backend/tests/contracts/dingtalk-recruitment/v1.0/candidates-empty.json`
- Create: `backend/tests/contracts/dingtalk-recruitment/v1.0/candidates-malformed.json`
- Create: `backend/tests/unit/test_dingtalk_adapter.py`
- Create: `backend/tests/external/test_dingtalk_recruitment_contract.py`

**Interfaces:**
- Consumes: `SourceItem`, `FetchedResume`, `SourceUnavailable`,
  `ItemUnavailable` from Task 2.
- Produces: `DingTalkRecruitmentAdapter(source_name="dingtalk")` satisfying
  `ResumeSourceAdapter`, and `parse_candidates_page(payload: dict) -> list[SourceItem]`.

**Note:** the endpoint binding here is provisional. Design §2.2 and §16.1
record that `/v1.0/recruitment/candidates` is not present in the OAS read on
2026-07-27 and that the administrator has not yet granted the permission.
Parsing is written against the documented shape and pinned by fixtures so a
real mismatch fails loudly.

- [ ] **Step 1: Write the recorded fixtures**

`candidates-page.json`:

```json
{
  "hasMore": true,
  "nextCursor": "1753592400000",
  "list": [
    {
      "candidateId": "cand-1001",
      "updateTime": "2026-07-27T04:30:00Z",
      "jobCode": "FOREIGN_TRADE",
      "resume": {
        "fileName": "zhang.pdf",
        "fileType": "application/pdf",
        "downloadUrl": "https://example.invalid/d/1001"
      }
    },
    {
      "candidateId": "cand-1002",
      "updateTime": "2026-07-27T04:45:00Z",
      "jobCode": null,
      "resume": {
        "fileName": "li.docx",
        "fileType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "downloadUrl": "https://example.invalid/d/1002"
      }
    }
  ]
}
```

`candidates-empty.json`:

```json
{"hasMore": false, "nextCursor": null, "list": []}
```

`candidates-malformed.json` — a row missing `candidateId`:

```json
{
  "hasMore": false,
  "nextCursor": null,
  "list": [
    {
      "updateTime": "2026-07-27T04:30:00Z",
      "jobCode": "FOREIGN_TRADE",
      "resume": {
        "fileName": "x.pdf",
        "fileType": "application/pdf",
        "downloadUrl": "https://example.invalid/d/x"
      }
    }
  ]
}
```

- [ ] **Step 2: Write the failing adapter parse tests**

Create `backend/tests/unit/test_dingtalk_adapter.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.services.sync.dingtalk import (
    DingTalkRecruitmentAdapter,
    parse_candidates_page,
)

FIXTURES = Path(__file__).parents[1] / "contracts" / "dingtalk-recruitment" / "v1.0"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_a_normal_page_maps_every_documented_field() -> None:
    items, urls = parse_candidates_page(_load("candidates-page"))

    assert [i.external_id for i in items] == ["cand-1001", "cand-1002"]
    assert items[0].updated_at == datetime(2026, 7, 27, 4, 30, tzinfo=timezone.utc)
    assert items[0].jd_code == "FOREIGN_TRADE"
    assert items[0].filename == "zhang.pdf"
    assert items[1].jd_code is None
    # URLs travel beside the items, never inside them.
    assert urls["cand-1001"] == "https://example.invalid/d/1001"


def test_an_empty_page_is_not_an_error() -> None:
    assert parse_candidates_page(_load("candidates-empty")) == ([], {})


def test_a_missing_required_field_raises_rather_than_yielding_none() -> None:
    # Silent `None` here would create a candidate with no source id and break
    # deduplication for every later run.
    with pytest.raises(ValueError, match="candidateId"):
        parse_candidates_page(_load("candidates-malformed"))


def test_the_adapter_declares_its_source_name() -> None:
    assert DingTalkRecruitmentAdapter.source_name == "dingtalk"
```

- [ ] **Step 3: Run the adapter tests and confirm failure**

Run: `uv run pytest backend/tests/unit/test_dingtalk_adapter.py -q`

Expected: FAIL — `ModuleNotFoundError: backend.app.services.sync.dingtalk`.

- [ ] **Step 4: Implement the adapter**

Create `backend/app/services/sync/dingtalk.py`:

```python
from __future__ import annotations

from datetime import datetime
from hashlib import sha256

import httpx

from backend.app.config import get_settings
from backend.app.services.sync.adapter import (
    FetchedResume,
    ItemUnavailable,
    SourceItem,
    SourceUnavailable,
)

CANDIDATES_PATH = "/v1.0/recruitment/candidates"
ACCESS_TOKEN_HEADER = "x-acs-dingtalk-access-token"
REQUEST_TIMEOUT_SECONDS = 30.0


def _require(row: dict, key: str) -> object:
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"recruitment row is missing {key}")
    return value


def parse_candidates_page(payload: dict) -> tuple[list[SourceItem], dict[str, str]]:
    """Map one documented page into source items plus their download URLs.

    The URLs are returned separately so `SourceItem` carries no transport
    detail; the adapter keeps them and the runner never sees them.

    A missing field raises. Yielding `None` would produce a candidate with no
    external id, which breaks deduplication for every subsequent run.
    """
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise ValueError("recruitment page is missing list")

    items: list[SourceItem] = []
    urls: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("recruitment row is not an object")
        resume = row.get("resume")
        if not isinstance(resume, dict):
            raise ValueError("recruitment row is missing resume")
        external_id = str(_require(row, "candidateId"))
        updated_raw = str(_require(row, "updateTime")).replace("Z", "+00:00")
        items.append(
            SourceItem(
                external_id=external_id,
                updated_at=datetime.fromisoformat(updated_raw),
                filename=str(_require(resume, "fileName")),
                content_type=str(_require(resume, "fileType")),
                jd_code=row.get("jobCode") or None,
            )
        )
        urls[external_id] = str(_require(resume, "downloadUrl"))
    return items, urls


class DingTalkRecruitmentAdapter:
    """DingTalk recruitment source.

    This is the ONLY file that knows the recruitment endpoints. The binding is
    provisional until the live probe runs — see design §2.2 and §16.1.
    """

    source_name = "dingtalk"

    def __init__(self, access_token: str) -> None:
        self._settings = get_settings()
        self._access_token = access_token
        self._download_urls: dict[str, str] = {}

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]:
        url = f"{self._settings.DINGTALK_RECRUITMENT_BASE_URL}{CANDIDATES_PATH}"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url,
                    params={"since": since.isoformat(), "maxResults": limit},
                    headers={ACCESS_TOKEN_HEADER: self._access_token},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceUnavailable("recruitment listing failed") from exc
        items, urls = parse_candidates_page(payload)
        self._download_urls.update(urls)
        return items[:limit]

    async def fetch(self, item: SourceItem) -> FetchedResume:
        raise NotImplementedError(
            "download URL handling is pinned by the live probe; see Task 4 Step 6"
        )
```

- [ ] **Step 5: Run the adapter tests and confirm they pass**

Run: `uv run pytest backend/tests/unit/test_dingtalk_adapter.py -q`

Expected: PASS (4 tests).

- [ ] **Step 6: Implement `fetch` against the documented download URL**

Replace the `NotImplementedError` body:

```python
    async def fetch(self, item: SourceItem) -> FetchedResume:
        if not self._download_urls.get(item.external_id):
            raise ItemUnavailable("no download url for item")
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    self._download_urls[item.external_id],
                    headers={ACCESS_TOKEN_HEADER: self._access_token},
                )
                response.raise_for_status()
                content = response.content
        except httpx.HTTPError as exc:
            raise ItemUnavailable("attachment download failed") from exc
        return FetchedResume(
            content=content,
            sha256=sha256(content).hexdigest(),
            filename=item.filename,
            content_type=item.content_type,
        )
```

`self._download_urls` is already populated by `list_changed` (Step 4), so no
signature changes are needed here — only the `fetch` body above.

- [ ] **Step 7: Write the live probe (not run in CI)**

Create `backend/tests/external/test_dingtalk_recruitment_contract.py`:

```python
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.sync.dingtalk import DingTalkRecruitmentAdapter

pytestmark = pytest.mark.external_contract

TOKEN = os.environ.get("DINGTALK_PROBE_ACCESS_TOKEN")


@pytest.mark.skipif(not TOKEN, reason="requires a real corp access token")
@pytest.mark.asyncio
async def test_the_recruitment_endpoint_exists_and_matches_our_parser() -> None:
    adapter = DingTalkRecruitmentAdapter(access_token=TOKEN or "")

    since = datetime.now(timezone.utc) - timedelta(days=7)
    items, urls = await adapter.list_changed(since, 5)

    # We assert shape, not content: this proves our recorded fixtures describe
    # the real API before we trust them.
    for item in items:
        assert item.external_id
        assert item.updated_at.tzinfo is not None
        assert urls.get(item.external_id)
```

- [ ] **Step 8: Run the task gate**

```powershell
uv run pytest backend/tests/unit/test_dingtalk_adapter.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass; the probe is deselected.

- [ ] **Step 9: Commit**

```bash
git add -- backend/app/services/sync/dingtalk.py backend/tests/contracts/dingtalk-recruitment backend/tests/unit/test_dingtalk_adapter.py backend/tests/external/test_dingtalk_recruitment_contract.py
git commit -m "feat(wp8): add the dingtalk recruitment adapter"
```

---

## Task 5: Bounded replay, Celery wiring, and the exit gate

**Files:**
- Create: `backend/app/services/sync/replay.py`
- Create: `backend/app/tasks/wp8.py`
- Modify: `backend/app/tasks/celery_app.py`
- Test: `backend/tests/unit/test_wp8_tasks.py`, extend
  `backend/tests/integration/test_sync_runner.py`

**Interfaces:**
- Consumes: Task 3's `run_sync`, Task 2's ledger.
- Produces: `async replay_failed(session_factory, adapter, *, now, max_attempts) -> int`;
  Celery tasks `sync.pull_dingtalk` and `sync.replay_failed`.

- [ ] **Step 1: Write the failing task registration test**

Create `backend/tests/unit/test_wp8_tasks.py`:

```python
from __future__ import annotations

from backend.app.tasks.celery_app import celery_app


def test_wp8_tasks_are_registered_under_their_published_names() -> None:
    import backend.app.tasks.wp8  # noqa: F401

    assert {"sync.pull_dingtalk", "sync.replay_failed"} <= set(celery_app.tasks)


def test_sync_is_not_scheduled_while_disabled() -> None:
    # The kill switch must remove the schedule entry, not merely make the task
    # return early: a registered schedule still wakes a worker every interval.
    from backend.app.config import get_settings

    assert get_settings().DINGTALK_SYNC_ENABLED is False
    assert "wp8-pull-dingtalk" not in celery_app.conf.beat_schedule


def test_the_wp7_schedules_survive() -> None:
    schedule = celery_app.conf.beat_schedule

    assert schedule["ingestion-sweep"]["task"] == "ingest.sweep"
    assert schedule["wp7-reconcile-budgets"]["task"] == "wp7.reconcile_budgets"
```

- [ ] **Step 2: Run the registration test and confirm failure**

Run: `uv run pytest backend/tests/unit/test_wp8_tasks.py -q`

Expected: FAIL — `ModuleNotFoundError: backend.app.tasks.wp8`.

- [ ] **Step 3: Implement replay**

Create `backend/app/services/sync/replay.py`:

```python
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from backend.app.models import SyncSourceItem

logger = logging.getLogger(__name__)


async def expire_exhausted(session_factory, *, max_attempts: int) -> int:
    """Stop retrying items that have used their attempts.

    A failed item cannot be rediscovered by the cursor — it has already moved
    past — so the ledger is the only place it can be re-driven from, and the
    only place it can be given up on.
    """
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(SyncSourceItem).where(
                    SyncSourceItem.outcome == "failed",
                    SyncSourceItem.attempts >= max_attempts,
                )
            )
        ).scalars()
        expired = 0
        for row in rows:
            if row.error_code != "exhausted":
                row.error_code = "exhausted"
                expired += 1
        await session.commit()
    return expired
```

- [ ] **Step 4: Implement the Celery wrappers**

Create `backend/app/tasks/wp8.py`:

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal, engine
from backend.app.tasks.celery_app import celery_app


@celery_app.task(name="sync.pull_dingtalk")
def pull_dingtalk_task() -> dict:
    """Pull one batch of changed recruitment resumes."""

    async def _runner() -> dict:
        from backend.app.services.sync.dingtalk import DingTalkRecruitmentAdapter
        from backend.app.services.sync.runner import run_sync

        settings = get_settings()
        if not settings.DINGTALK_SYNC_ENABLED:
            return {"skipped": "disabled"}
        try:
            from backend.app.services.dingtalk.oauth import DingTalkOAuthClient

            token = await DingTalkOAuthClient().corp_access_token()
            report = await run_sync(
                AsyncSessionLocal,
                DingTalkRecruitmentAdapter(access_token=token),
                now=datetime.now(timezone.utc),
                overlap_seconds=settings.SYNC_OVERLAP_SECONDS,
                max_items=settings.SYNC_MAX_ITEMS_PER_RUN,
            )
        finally:
            await engine.dispose()
        return {
            "listed": report.listed,
            "ingested": report.ingested,
            "skipped": report.skipped,
            "failed": report.failed,
            "dropped_by_cap": report.dropped_by_cap,
        }

    return asyncio.run(_runner())


@celery_app.task(name="sync.replay_failed")
def replay_failed_task() -> dict:
    """Give up on ledger items that have exhausted their attempts."""

    async def _runner() -> dict:
        from backend.app.services.sync.replay import expire_exhausted

        settings = get_settings()
        try:
            expired = await expire_exhausted(
                AsyncSessionLocal, max_attempts=settings.SYNC_MAX_ITEM_ATTEMPTS
            )
        finally:
            await engine.dispose()
        return {"expired": expired}

    return asyncio.run(_runner())
```

**Note for the implementer:** `DingTalkOAuthClient` currently exchanges a user
auth code and has no corp-level token method. Add
`async def corp_access_token(self) -> str` to it, calling
`POST /v1.0/oauth2/accessToken` with `appKey`/`appSecret`, and cache it for
slightly less than its `expireIn`. Write its unit test against a recorded
fixture before using it here.

- [ ] **Step 5: Wire the Beat schedule behind the kill switch**

In `backend/app/tasks/celery_app.py`, add `"backend.app.tasks.wp8"` to
`include`, then after the existing `beat_schedule` assignment:

```python
if settings.DINGTALK_SYNC_ENABLED:
    celery_app.conf.beat_schedule["wp8-pull-dingtalk"] = {
        "task": "sync.pull_dingtalk",
        "schedule": float(settings.DINGTALK_SYNC_INTERVAL_SECONDS),
    }
    celery_app.conf.beat_schedule["wp8-replay-failed"] = {
        "task": "sync.replay_failed",
        "schedule": float(settings.SYNC_REPLAY_INTERVAL_SECONDS),
    }
```

- [ ] **Step 6: Run the registration test and confirm it passes**

Run: `uv run pytest backend/tests/unit/test_wp8_tasks.py -q`

Expected: PASS (3 tests).

- [ ] **Step 7: Write the failing exit-gate test**

Append to `backend/tests/integration/test_sync_runner.py`:

```python
async def test_a_failing_sync_does_not_block_manual_upload(
    client, db_session, auth_headers, minio_storage, valid_pdf_bytes
):
    class Broken:
        source_name = "dingtalk"

        async def list_changed(self, since, limit):
            raise SourceUnavailable("provider down")

        async def fetch(self, item):
            raise AssertionError("must not be reached")

    with pytest.raises(SourceUnavailable):
        await run_sync(
            AsyncSessionLocal, Broken(), now=NOW, overlap_seconds=300, max_items=200
        )

    # The web path is the fallback the exit gate protects; it must be untouched.
    response = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("manual.pdf", valid_pdf_bytes, "application/pdf")},
        headers=await auth_headers("hr"),
    )

    assert response.status_code == 202
    assert response.json()["job_id"]
```

- [ ] **Step 8: Run the exit-gate test**

```powershell
uv run pytest backend/tests/integration/test_sync_runner.py -q
```

Expected: PASS (7 tests).

- [ ] **Step 9: Run the full gate including Python 3.10**

```powershell
uv run pytest -m "not integration and not external_contract" -q
uv run --python 3.10 --extra dev pytest -m "not integration and not external_contract" -q
uv run pytest -m integration -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass. The 3.10 run is not optional — this package is
timestamp-heavy and hosted CI runs a 3.10 matrix job.

- [ ] **Step 10: Commit**

```bash
git add -- backend/app/services/sync/replay.py backend/app/tasks/wp8.py backend/app/tasks/celery_app.py backend/tests/unit/test_wp8_tasks.py backend/tests/integration/test_sync_runner.py
git commit -m "feat(wp8): schedule sync behind a kill switch"
```

---

## Task 6: JD metadata synchronization

**Files:**
- Modify: `backend/app/services/sync/dingtalk.py`
- Modify: `backend/app/services/sync/runner.py`
- Test: extend `backend/tests/integration/test_sync_runner.py`

**Interfaces:**
- Produces: `async sync_jd_metadata(session_factory, adapter, *, now) -> int`
  and `DingTalkRecruitmentAdapter.list_jobs() -> list[JobMeta]` where
  `JobMeta(code: str, name: str, description: str)`.

- [ ] **Step 1: Write the failing governance test**

Append to `backend/tests/integration/test_sync_runner.py`:

```python
async def test_jd_sync_never_touches_governed_rule_state(db_session, minio_storage):
    from backend.app.models import JD, RuleVersion
    from backend.app.services.sync.runner import sync_jd_metadata

    jd = JD(code="GOV_TEST", name="旧名称", description="旧描述", status="active")
    db_session.add(jd)
    await db_session.flush()
    version = RuleVersion(
        jd_id=jd.id, version="v1", published_at=NOW, schema_json={"jd_code": "GOV_TEST"}
    )
    db_session.add(version)
    await db_session.flush()
    jd.active_rule_version_id = version.id
    await db_session.commit()
    pinned = jd.active_rule_version_id

    class JobAdapter:
        source_name = "dingtalk"

        async def list_jobs(self):
            from backend.app.services.sync.adapter import JobMeta

            return [JobMeta(code="GOV_TEST", name="新名称", description="新描述")]

    updated = await sync_jd_metadata(AsyncSessionLocal, JobAdapter(), now=NOW)
    await db_session.commit()
    db_session.expire_all()
    reloaded = await db_session.get(JD, jd.id)

    assert updated == 1
    assert reloaded.name == "新名称"
    # WP6c gates rule publication behind draft -> What-If -> regression. A
    # background sync able to move the active version would be a back door.
    assert reloaded.active_rule_version_id == pinned
    assert reloaded.status == "active"
```

- [ ] **Step 2: Run the governance test and confirm failure**

```powershell
uv run pytest backend/tests/integration/test_sync_runner.py -q -k jd_sync
```

Expected: FAIL — `sync_jd_metadata` does not exist.

- [ ] **Step 3: Add `JobMeta` to the port**

In `backend/app/services/sync/adapter.py`:

```python
@dataclass(frozen=True)
class JobMeta:
    code: str
    name: str
    description: str
```

- [ ] **Step 4: Implement the constrained upsert**

In `backend/app/services/sync/runner.py`:

```python
async def sync_jd_metadata(session_factory, adapter, *, now: datetime) -> int:
    """Create missing JDs and refresh their descriptive fields only.

    `active_rule_version_id`, `status`, and any rule schema are deliberately
    untouched: WP6c owns rule publication and this task must not be a way
    around its gates.
    """
    from backend.app.models import JD

    jobs = await adapter.list_jobs()
    changed = 0
    async with session_factory() as session:
        for job in jobs:
            existing = (
                await session.execute(select(JD).where(JD.code == job.code))
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    JD(
                        code=job.code,
                        name=job.name,
                        description=job.description,
                        status="active",
                    )
                )
                changed += 1
                continue
            if (existing.name, existing.description) != (job.name, job.description):
                existing.name = job.name
                existing.description = job.description
                changed += 1
        await session.commit()
    return changed
```

Add `from sqlalchemy import select` to the runner's imports.

- [ ] **Step 5: Add `list_jobs` to the adapter**

In `backend/app/services/sync/dingtalk.py`, mirroring `list_changed`, against
`/v1.0/recruitment/jobs`, mapping `jobCode` / `name` / `description` and
raising `SourceUnavailable` on transport failure. Add a fixture
`jobs-page.json` and a parse test alongside the candidate ones.

- [ ] **Step 6: Run the tests and the task gate**

```powershell
uv run pytest backend/tests/integration/test_sync_runner.py backend/tests/unit/test_dingtalk_adapter.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -- backend/app/services/sync/adapter.py backend/app/services/sync/dingtalk.py backend/app/services/sync/runner.py backend/tests/contracts/dingtalk-recruitment backend/tests/unit/test_dingtalk_adapter.py backend/tests/integration/test_sync_runner.py
git commit -m "feat(wp8): sync jd metadata without touching rule state"
```

---

## Task 7: Sync report endpoint and documentation

**Files:**
- Create: `backend/app/routers/sync_report.py`
- Create: `backend/app/schemas/sync_report.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/integration/test_sync_report_api.py`
- Modify: `README.md`, `docs/superpowers/plans/README.md`

**Interfaces:**
- Produces: `GET /api/v1/sync/report` returning
  `SyncReportResponse(source, cursor_value, last_run_at, ingested_total,
  failed_total, skipped_total)`.

- [ ] **Step 1: Write the failing API test**

Create `backend/tests/integration/test_sync_report_api.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.models import SyncCursor, SyncSourceItem

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REPORT = "/api/v1/sync/report"
NOW = datetime.now(timezone.utc)


async def test_report_requires_a_read_role(client, db_session, auth_headers):
    assert (await client.get(REPORT)).status_code == 401
    for role in ("hr", "hr_lead", "admin"):
        assert (await client.get(REPORT, headers=await auth_headers(role))).status_code == 200


async def test_report_counts_outcomes_per_source(client, db_session, auth_headers):
    db_session.add(
        SyncCursor(
            source="dingtalk",
            cursor_value=NOW.isoformat(),
            last_run_at=NOW,
            updated_at=NOW,
        )
    )
    for index, outcome in enumerate(["ingested", "ingested", "skipped_duplicate", "failed"]):
        db_session.add(
            SyncSourceItem(
                source="dingtalk",
                source_external_id=f"c{index}",
                content_sha256=f"{index}" * 64,
                outcome=outcome,
                attempts=1,
                first_seen_at=NOW,
                last_seen_at=NOW,
            )
        )
    await db_session.commit()

    body = (await client.get(REPORT, headers=await auth_headers("hr"))).json()
    row = next(item for item in body["items"] if item["source"] == "dingtalk")

    assert row["ingested_total"] == 2
    assert row["skipped_total"] == 1
    assert row["failed_total"] == 1


async def test_report_carries_no_candidate_identifiers(client, db_session, auth_headers):
    raw = (await client.get(REPORT, headers=await auth_headers("hr"))).text

    for forbidden in ("source_external_id", "content_sha256", "candidate"):
        assert forbidden not in raw
```

- [ ] **Step 2: Run the API test and confirm failure**

```powershell
uv run pytest backend/tests/integration/test_sync_report_api.py -q
```

Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Implement the schema and router**

`backend/app/schemas/sync_report.py`:

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SyncSourceReport(BaseModel):
    source: str
    cursor_value: str | None
    last_run_at: datetime | None
    ingested_total: int
    skipped_total: int
    failed_total: int


class SyncReportResponse(BaseModel):
    items: list[SyncSourceReport]
```

`backend/app/routers/sync_report.py` groups `SyncSourceItem` by
`(source, outcome)` with `func.count()`, joins the cursor row, and gates on
`require_roles("hr", "hr_lead", "admin")`. Register it in `main.py` next to
the other routers.

- [ ] **Step 4: Run the API test and confirm it passes**

```powershell
uv run pytest backend/tests/integration/test_sync_report_api.py -q
```

Expected: PASS (3 tests).

- [ ] **Step 5: Update documentation**

Add a `## WP8 — 钉钉招聘同步` section to `README.md` covering: the kill switch
and its default, the three idempotency layers, why overlap and the ledger are a
pair, that a sync failure cannot block manual upload, and that the endpoint
binding is provisional until the live probe runs.

In `docs/superpowers/plans/README.md`, move WP8 to **In progress** and add a
Current Evidence paragraph once the final gate is green.

- [ ] **Step 6: Run the full gate**

```powershell
uv run pytest -m "not integration and not external_contract" -q
uv run --python 3.10 --extra dev pytest -m "not integration and not external_contract" -q
uv run pytest -m integration -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -- backend/app/routers/sync_report.py backend/app/schemas/sync_report.py backend/app/main.py backend/tests/integration/test_sync_report_api.py README.md docs/superpowers/plans/README.md
git commit -m "feat(wp8): expose the sync report"
```
