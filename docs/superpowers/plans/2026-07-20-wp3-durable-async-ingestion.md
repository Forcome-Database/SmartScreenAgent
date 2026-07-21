# WP3 Durable Asynchronous Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the resume parse/extract/score workflow out of the HTTP request behind a durable `ingestion_jobs` state machine with async upload, Celery worker orchestration, a Beat retry/reclaim sweeper, idempotent uploads and scores, batch upload, and thin status endpoints.

**Architecture:** Upload validates and persists the raw MinIO object (WP1), deduplicates by `raw_file_sha256`, records an `ingestion_jobs` row in `queued`, enqueues a Celery task, and returns `202`. The worker claims the job under a lease, downloads the object, and advances `queued → parsing → extracting → (ready | scoring → completed)`, committing each transition separately from the external call before it. A Beat sweeper re-enqueues `retryable_failed` jobs under a cap and reclaims lease-expired processing jobs. A `unique(candidate_id, jd_id, rule_version_id)` constraint makes scoring an idempotent upsert.

**Tech Stack:** Python 3.10–3.14, FastAPI, Pydantic v2 / pydantic-settings, SQLAlchemy 2.0 async, Alembic, Celery + Redis, MinIO, pytest, respx, Ruff, mypy.

## Global Constraints

- Default CI runs `pytest -m "not integration and not external_contract"`; unit tests stay offline and deterministic. Never call MinerU or the LLM gateway from unit tests.
- Keep the WP1 upload boundary: PDF, DOCX, PNG, JPEG only; `MAX_RESUME_FILE_BYTES` default 20 MiB.
- Keep the WP2 stable error codes; do not leak provider bodies, PII plaintext, local paths, or signed URLs in responses or logs.
- SQLAlchemy 2.0 typed models: `Mapped[...]` + `mapped_column`, inherit `Base, TimestampMixin`, `BigInteger` primary keys.
- Alembic revision chain: current head is `b57c2f9e1a6d`; the WP3 migration's `down_revision` is `b57c2f9e1a6d`.
- Migrations must round-trip from an empty database and from `b57c2f9e1a6d`.
- Scoped conventional commits; exclude `.superpowers/`, `backend.zip`, `.firecrawl/`.
- Run `uv run ruff check backend` and `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports` before each commit.

---

## File Structure

**Create:**
- `backend/app/models/ingestion_job.py` — `IngestionJob` ORM model.
- `backend/app/services/ingestion/__init__.py` — package exports.
- `backend/app/services/ingestion/states.py` — `IngestionState` enum, processing/terminal sets, allowed-transition map, `assert_transition`, `InvalidTransitionError`.
- `backend/app/services/ingestion/jobs.py` — `IngestionJobService`: create-or-reuse (sha256 idempotency), claim, transition, backfill, batch aggregate.
- `backend/app/services/ingestion/sweeper.py` — reclaim + re-enqueue + terminate logic.
- `backend/app/tasks/sweep.py` — Celery Beat task calling the sweeper.
- `migrations/versions/<hash>_wp3_ingestion_jobs.py` — table + scores unique constraint.
- Unit tests: `backend/tests/unit/test_ingestion_states.py`, `test_ingestion_jobs.py`, `test_ingestion_sweeper.py`, `test_candidates_upload_async.py`.
- Integration tests: `backend/tests/integration/test_ingestion_async.py`.

**Modify:**
- `backend/app/config.py` — WP3 settings.
- `backend/app/models/__init__.py` — register `IngestionJob`.
- `backend/app/models/score.py` — declare the uniqueness constraint on the model.
- `backend/app/tasks/celery_app.py` — include new modules + Beat schedule.
- `backend/app/tasks/ingest.py` — worker orchestration with job state + score upsert.
- `backend/app/routers/candidates.py` — async upload (`202`), batch upload, job/batch status.
- `backend/app/main.py` — register any new router prefixes if needed (candidates router already registered; no new router).
- `scripts/verify.py` — start a Beat process (or run one sweep) in the integration gate.
- `docker-compose.yml`, `.env.example`, `README.md` — Beat process, new settings, async contract.
- Tests: `backend/tests/integration/test_candidates_api.py`, `test_tasks_ingest.py` for the new async contract.

---

## Task 1: WP3 settings and the IngestionState machine

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/services/ingestion/__init__.py`, `backend/app/services/ingestion/states.py`
- Test: `backend/tests/unit/test_ingestion_states.py`

**Interfaces:**
- Produces: `IngestionState` (str enum), `PROCESSING_STATES: frozenset[IngestionState]`, `TERMINAL_STATES: frozenset[IngestionState]`, `assert_transition(current: IngestionState, target: IngestionState) -> None`, `InvalidTransitionError(RuntimeError)`; `Settings.INGESTION_MAX_ATTEMPTS: int`, `INGESTION_LEASE_SECONDS: int`, `INGESTION_SWEEP_INTERVAL_SECONDS: int`, `INGESTION_BATCH_MAX_FILES: int`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_ingestion_states.py
import pytest

from backend.app.services.ingestion.states import (
    InvalidTransitionError,
    IngestionState,
    PROCESSING_STATES,
    TERMINAL_STATES,
    assert_transition,
)


def test_processing_and_terminal_sets():
    assert PROCESSING_STATES == frozenset(
        {IngestionState.PARSING, IngestionState.EXTRACTING, IngestionState.SCORING}
    )
    assert IngestionState.COMPLETED in TERMINAL_STATES
    assert IngestionState.TERMINAL_FAILED in TERMINAL_STATES
    assert IngestionState.QUEUED not in TERMINAL_STATES


@pytest.mark.parametrize(
    "current,target",
    [
        (IngestionState.QUEUED, IngestionState.PARSING),
        (IngestionState.PARSING, IngestionState.EXTRACTING),
        (IngestionState.EXTRACTING, IngestionState.READY),
        (IngestionState.EXTRACTING, IngestionState.SCORING),
        (IngestionState.SCORING, IngestionState.COMPLETED),
        (IngestionState.PARSING, IngestionState.RETRYABLE_FAILED),
        (IngestionState.RETRYABLE_FAILED, IngestionState.QUEUED),
        (IngestionState.RETRYABLE_FAILED, IngestionState.TERMINAL_FAILED),
        (IngestionState.SCORING, IngestionState.TERMINAL_FAILED),
    ],
)
def test_allowed_transitions(current, target):
    assert_transition(current, target)  # no raise


@pytest.mark.parametrize(
    "current,target",
    [
        (IngestionState.QUEUED, IngestionState.COMPLETED),
        (IngestionState.COMPLETED, IngestionState.PARSING),
        (IngestionState.TERMINAL_FAILED, IngestionState.QUEUED),
        (IngestionState.PARSING, IngestionState.COMPLETED),
    ],
)
def test_illegal_transitions_raise(current, target):
    with pytest.raises(InvalidTransitionError):
        assert_transition(current, target)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/unit/test_ingestion_states.py -q`
Expected: FAIL with `ModuleNotFoundError: backend.app.services.ingestion.states`.

- [ ] **Step 3: Create the states module**

```python
# backend/app/services/ingestion/states.py
from __future__ import annotations

from enum import Enum


class IngestionState(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    READY = "ready"
    SCORING = "scoring"
    COMPLETED = "completed"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    DELETED = "deleted"


PROCESSING_STATES: frozenset[IngestionState] = frozenset(
    {IngestionState.PARSING, IngestionState.EXTRACTING, IngestionState.SCORING}
)

TERMINAL_STATES: frozenset[IngestionState] = frozenset(
    {IngestionState.READY, IngestionState.COMPLETED, IngestionState.TERMINAL_FAILED, IngestionState.DELETED}
)

_ALLOWED: dict[IngestionState, frozenset[IngestionState]] = {
    IngestionState.QUEUED: frozenset({IngestionState.PARSING, IngestionState.DELETED}),
    IngestionState.PARSING: frozenset(
        {IngestionState.EXTRACTING, IngestionState.RETRYABLE_FAILED, IngestionState.TERMINAL_FAILED}
    ),
    IngestionState.EXTRACTING: frozenset(
        {IngestionState.READY, IngestionState.SCORING, IngestionState.RETRYABLE_FAILED, IngestionState.TERMINAL_FAILED}
    ),
    IngestionState.READY: frozenset({IngestionState.DELETED}),
    IngestionState.SCORING: frozenset(
        {IngestionState.COMPLETED, IngestionState.RETRYABLE_FAILED, IngestionState.TERMINAL_FAILED}
    ),
    IngestionState.COMPLETED: frozenset({IngestionState.DELETED}),
    IngestionState.RETRYABLE_FAILED: frozenset({IngestionState.QUEUED, IngestionState.TERMINAL_FAILED}),
    IngestionState.TERMINAL_FAILED: frozenset({IngestionState.DELETED}),
    IngestionState.DELETED: frozenset(),
}


class InvalidTransitionError(RuntimeError):
    def __init__(self, current: IngestionState, target: IngestionState) -> None:
        super().__init__(f"illegal ingestion transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def assert_transition(current: IngestionState, target: IngestionState) -> None:
    if target not in _ALLOWED[current]:
        raise InvalidTransitionError(current, target)
```

Also create `backend/app/services/ingestion/__init__.py`:

```python
# backend/app/services/ingestion/__init__.py
from backend.app.services.ingestion.states import (
    IngestionState,
    InvalidTransitionError,
    PROCESSING_STATES,
    TERMINAL_STATES,
    assert_transition,
)

__all__ = [
    "IngestionState",
    "InvalidTransitionError",
    "PROCESSING_STATES",
    "TERMINAL_STATES",
    "assert_transition",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/tests/unit/test_ingestion_states.py -q`
Expected: PASS.

- [ ] **Step 5: Add WP3 settings**

Add to `backend/app/config.py` after the "Resume upload boundary" block:

```python
    # Ingestion jobs (WP3)
    INGESTION_MAX_ATTEMPTS: int = 3
    INGESTION_LEASE_SECONDS: int = 900
    INGESTION_SWEEP_INTERVAL_SECONDS: int = 60
    INGESTION_BATCH_MAX_FILES: int = 50
```

- [ ] **Step 6: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/config.py backend/app/services/ingestion/ backend/tests/unit/test_ingestion_states.py
git commit -m "feat(wp3): add ingestion settings and state machine"
```

---

## Task 2: IngestionJob model and migration

**Files:**
- Create: `backend/app/models/ingestion_job.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/models/score.py`
- Create: `migrations/versions/<hash>_wp3_ingestion_jobs.py`
- Test: extend `backend/tests/unit/test_ingestion_states.py` is unit; the migration is verified by the integration `_apply_migrations` fixture and a dedicated round-trip test in Task 9.

**Interfaces:**
- Produces: `IngestionJob` model with columns from spec §5.1; `Score` gains `UniqueConstraint("candidate_id", "jd_id", "rule_version_id", name="uq_scores_candidate_jd_rule")`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_ingestion_job_model.py
from backend.app.models import IngestionJob, Score


def test_ingestion_job_columns_exist():
    cols = set(IngestionJob.__table__.columns.keys())
    assert {
        "id", "batch_id", "state", "source", "jd_code",
        "raw_file_key", "raw_file_sha256", "candidate_id", "score_id",
        "attempts", "last_error_code", "lease_expires_at", "trace_id", "actor",
    } <= cols


def test_scores_have_unique_business_constraint():
    names = {c.name for c in Score.__table__.constraints}
    assert "uq_scores_candidate_jd_rule" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/unit/test_ingestion_job_model.py -q`
Expected: FAIL with `ImportError: cannot import name 'IngestionJob'`.

- [ ] **Step 3: Create the model**

```python
# backend/app/models/ingestion_job.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_external_id: Mapped[str | None] = mapped_column(String(128))
    jd_code: Mapped[str | None] = mapped_column(String(64))

    raw_file_key: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_file_content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_file_original_name_cipher: Mapped[str] = mapped_column(Text, nullable=False)

    candidate_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("candidates.id"))
    score_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("scores.id"))

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
```

Register in `backend/app/models/__init__.py`: add the import `from backend.app.models.ingestion_job import IngestionJob` and add `"IngestionJob"` to `__all__`.

Add the constraint to `backend/app/models/score.py` — add `UniqueConstraint` to the imports and a `__table_args__`:

```python
from sqlalchemy import (
    BigInteger, Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint,
)
# ... in class Score, before the columns:
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "jd_id", "rule_version_id", name="uq_scores_candidate_jd_rule"
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/tests/unit/test_ingestion_job_model.py -q`
Expected: PASS.

- [ ] **Step 5: Create the migration**

Generate a stable revision id (use `python -c "import uuid; print(uuid.uuid4().hex[:12])"` once and hardcode it). File `migrations/versions/<hash>_wp3_ingestion_jobs.py`:

```python
"""WP3 ingestion jobs and score uniqueness.

Revision ID: <hash>
Revises: b57c2f9e1a6d
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "<hash>"
down_revision: str | Sequence[str] | None = "b57c2f9e1a6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_external_id", sa.String(length=128), nullable=True),
        sa.Column("jd_code", sa.String(length=64), nullable=True),
        sa.Column("raw_file_key", sa.String(length=256), nullable=False),
        sa.Column("raw_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("raw_file_content_type", sa.String(length=128), nullable=False),
        sa.Column("raw_file_original_name_cipher", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), sa.ForeignKey("candidates.id"), nullable=True),
        sa.Column("score_id", sa.BigInteger(), sa.ForeignKey("scores.id"), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ingestion_jobs_sha256", "ingestion_jobs", ["raw_file_sha256"])
    op.create_index("ix_ingestion_jobs_state_lease", "ingestion_jobs", ["state", "lease_expires_at"])
    op.create_index("ix_ingestion_jobs_batch", "ingestion_jobs", ["batch_id"])
    op.create_check_constraint(
        "ck_ingestion_jobs_attempts_nonnegative", "ingestion_jobs", "attempts >= 0"
    )
    # Fails loudly if legacy duplicate scores exist; reconcile before deploy (see README rollout).
    op.create_unique_constraint(
        "uq_scores_candidate_jd_rule", "scores", ["candidate_id", "jd_id", "rule_version_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_scores_candidate_jd_rule", "scores", type_="unique")
    op.drop_index("ix_ingestion_jobs_batch", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_state_lease", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_sha256", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
```

- [ ] **Step 6: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/models/ backend/tests/unit/test_ingestion_job_model.py migrations/versions/
git commit -m "feat(wp3): add ingestion_jobs table and score uniqueness"
```

---

## Task 3: IngestionJobService — create-or-reuse, claim, transition, backfill

**Files:**
- Create: `backend/app/services/ingestion/jobs.py`
- Test: `backend/tests/unit/test_ingestion_jobs.py`

**Interfaces:**
- Consumes: `IngestionJob`, `IngestionState`, `assert_transition`, `RawFileReference` (from `backend.app.tasks.ingest`).
- Produces:
  - `IngestionJobService(db: AsyncSession, clock: Callable[[], datetime] | None = None)`
  - `async create_or_reuse(*, raw_file, source, source_external_id, jd_code, actor, batch_id=None, trace_id=None) -> tuple[IngestionJob, bool]` — returns `(job, created)`; reuses an existing non-terminal job with the same `raw_file_sha256`.
  - `async claim(job_id: int, *, lease_seconds: int) -> IngestionJob | None` — atomically sets `parsing`, `lease_expires_at`, `attempts += 1` using `SELECT ... FOR UPDATE SKIP LOCKED`; returns None if already claimed/gone.
  - `async transition(job: IngestionJob, target: IngestionState, *, error_code: str | None = None) -> None`
  - `async batch_counts(batch_id: UUID) -> dict[str, int]`

This task uses a real async session, so its tests are integration-marked (need PostgreSQL for `FOR UPDATE SKIP LOCKED` and unique semantics). Unit-test the pure transition guard through Task 1; test service behavior here under `@pytest.mark.integration`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_ingestion_jobs.py
import pytest

from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.ingestion.states import IngestionState
from backend.app.tasks.ingest import RawFileReference

pytestmark = pytest.mark.integration


def _ref(sha: str) -> RawFileReference:
    return RawFileReference(
        object_key=f"resumes/2026/07/{sha}",
        sha256=sha,
        size_bytes=1234,
        content_type="application/pdf",
        original_name_cipher="cipher",
    )


async def test_create_then_reuse_by_sha(db_session):
    svc = IngestionJobService(db_session)
    job1, created1 = await svc.create_or_reuse(
        raw_file=_ref("a" * 64), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    await db_session.commit()
    job2, created2 = await svc.create_or_reuse(
        raw_file=_ref("a" * 64), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    assert created1 is True and created2 is False
    assert job2.id == job1.id


async def test_claim_sets_lease_and_increments_attempts(db_session):
    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=_ref("b" * 64), source="upload", source_external_id=None,
        jd_code="FOREIGN_TRADE", actor="user:1",
    )
    await db_session.commit()
    claimed = await svc.claim(job.id, lease_seconds=900)
    assert claimed is not None
    assert claimed.state == IngestionState.PARSING.value
    assert claimed.attempts == 1
    assert claimed.lease_expires_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/integration/test_ingestion_jobs.py -q`
Expected: FAIL with `ModuleNotFoundError: backend.app.services.ingestion.jobs`.

- [ ] **Step 3: Implement the service**

```python
# backend/app/services/ingestion/jobs.py
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob
from backend.app.services.ingestion.states import (
    IngestionState,
    TERMINAL_STATES,
    assert_transition,
)
from backend.app.tasks.ingest import RawFileReference

_TERMINAL_VALUES = tuple(s.value for s in TERMINAL_STATES)


class IngestionJobService:
    def __init__(self, db: AsyncSession, clock: Callable[[], datetime] | None = None) -> None:
        self.db = db
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def create_or_reuse(
        self,
        *,
        raw_file: RawFileReference,
        source: str,
        source_external_id: str | None,
        jd_code: str | None,
        actor: str,
        batch_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> tuple[IngestionJob, bool]:
        existing = (
            await self.db.execute(
                select(IngestionJob)
                .where(IngestionJob.raw_file_sha256 == raw_file.sha256)
                .where(IngestionJob.state.notin_(_TERMINAL_VALUES))
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        job = IngestionJob(
            batch_id=batch_id,
            state=IngestionState.QUEUED.value,
            source=source,
            source_external_id=source_external_id,
            jd_code=jd_code,
            raw_file_key=raw_file.object_key,
            raw_file_sha256=raw_file.sha256,
            raw_file_size_bytes=raw_file.size_bytes,
            raw_file_content_type=raw_file.content_type,
            raw_file_original_name_cipher=raw_file.original_name_cipher,
            attempts=0,
            actor=actor,
            trace_id=trace_id,
        )
        self.db.add(job)
        await self.db.flush()
        return job, True

    async def claim(self, job_id: int, *, lease_seconds: int) -> IngestionJob | None:
        job = (
            await self.db.execute(
                select(IngestionJob)
                .where(IngestionJob.id == job_id)
                .where(IngestionJob.state == IngestionState.QUEUED.value)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        assert_transition(IngestionState(job.state), IngestionState.PARSING)
        job.state = IngestionState.PARSING.value
        job.attempts += 1
        job.lease_expires_at = self.clock() + timedelta(seconds=lease_seconds)
        await self.db.flush()
        return job

    async def transition(
        self, job: IngestionJob, target: IngestionState, *, error_code: str | None = None
    ) -> None:
        assert_transition(IngestionState(job.state), target)
        job.state = target.value
        if error_code is not None:
            job.last_error_code = error_code
        if target not in {IngestionState.PARSING, IngestionState.EXTRACTING, IngestionState.SCORING}:
            job.lease_expires_at = None
        await self.db.flush()

    async def batch_counts(self, batch_id: UUID) -> dict[str, int]:
        rows = (
            await self.db.execute(
                select(IngestionJob.state, func.count())
                .where(IngestionJob.batch_id == batch_id)
                .group_by(IngestionJob.state)
            )
        ).all()
        return {state: count for state, count in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/tests/integration/test_ingestion_jobs.py -q`
Expected: PASS (requires PostgreSQL running; use `docker compose up -d`).

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/ingestion/jobs.py backend/tests/integration/test_ingestion_jobs.py
git commit -m "feat(wp3): add ingestion job service with sha256 idempotency and lease claim"
```

---

## Task 4: Worker orchestration over the job state machine

**Files:**
- Modify: `backend/app/tasks/ingest.py`
- Test: `backend/tests/integration/test_tasks_ingest.py` (async path)

**Interfaces:**
- Consumes: `IngestionJobService`, `IngestionState`, the existing `run_parse_and_score` internals, `ResumeStorageService`.
- Produces:
  - `async run_job(*, db: AsyncSession, job: IngestionJob, storage: ResumeStorageService) -> None` — drives the state machine, downloading from MinIO, parsing, extracting, inserting/reusing the candidate, upserting the score, committing each transition.
  - `parse_and_score_task(job_id: int)` — Celery task: opens a session, claims the job, calls `run_job`, and on retryable/terminal error transitions the job accordingly (never raises the provider body).
  - `RETRYABLE_ERRORS` / `TERMINAL_ERRORS` classification tuples mapping WP2 exceptions to error codes.

Key change: scoring uses an **upsert** on `(candidate_id, jd_id, rule_version_id)`. Add a helper in `ScoringPipeline` or `run_job` that, on unique conflict, selects the existing score row and returns its id (Task 5 formalizes the pipeline change; here call it).

- [ ] **Step 1: Write the failing integration test**

```python
# add to backend/tests/integration/test_tasks_ingest.py
import pytest

from backend.app.models import IngestionJob
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.ingestion.states import IngestionState
from backend.app.tasks.ingest import RawFileReference, run_job

pytestmark = pytest.mark.integration


async def test_run_job_completes_with_stub_parser(db_session, minio_storage, monkeypatch):
    monkeypatch.setenv("MINERU_MODE", "stub")
    # store an object so the worker can download it
    ...  # arrange RawFileReference pointing at a stored object (reuse existing helpers)
    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=ref, source="upload", source_external_id=None, jd_code=None, actor="user:1",
    )
    await db_session.commit()
    claimed = await svc.claim(job.id, lease_seconds=900)
    await db_session.commit()
    await run_job(db=db_session, job=claimed, storage=minio_storage_service)
    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.READY.value
    assert refreshed.candidate_id is not None
```

(Reuse the fixtures already used by the existing `test_tasks_ingest.py`; fill the arrange block from that file's existing object-store setup.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/integration/test_tasks_ingest.py::test_run_job_completes_with_stub_parser -q`
Expected: FAIL with `ImportError: cannot import name 'run_job'`.

- [ ] **Step 3: Implement `run_job` and rewrite the task**

Refactor `run_parse_and_score` into stage helpers so `run_job` can advance state between them, committing after each transition. Sketch (fill against current `run_parse_and_score` body — reuse candidate insert/dedup verbatim):

```python
# backend/app/tasks/ingest.py (new orchestration; keep existing helpers)
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.ingestion.states import IngestionState

RETRYABLE_ERRORS = {
    "MinerUUnavailableError": "resume_parser_unavailable",
    "LLMUnavailableError": "ai_service_unavailable",
}
TERMINAL_ERRORS = {
    "MinerUContractError": "resume_parser_contract_invalid",
    "MinerUTaskError": "resume_parser_failed",
    "LLMConfigurationError": "ai_service_configuration_invalid",
    "LLMInvalidResponseError": "ai_invalid_output",
    "LLMInvalidOutputError": "ai_invalid_output",
}


async def run_job(*, db, job, storage) -> None:
    svc = IngestionJobService(db)
    reference = _reference_from_job(job)
    local_path = _temp_path_for(reference.content_type)
    try:
        await storage.download_verified(reference.stored_resume, local_path)
        parsed = await MinerUClient().parse(local_path)          # state already PARSING
        await svc.transition(job, IngestionState.EXTRACTING); await db.commit()
        extracted = await ResumeExtractor().extract(parsed.markdown)
        candidate = await _insert_or_reuse_candidate(db, reference, parsed, extracted, storage)
        job.candidate_id = candidate.id
        if job.jd_code:
            jd = await _lookup_active_jd(db, job.jd_code)
            if jd is not None:
                await svc.transition(job, IngestionState.SCORING); await db.commit()
                score_id = await _score_upsert(db, candidate.id, jd.id)
                job.score_id = score_id
                await svc.transition(job, IngestionState.COMPLETED); await db.commit()
                return
        await svc.transition(job, IngestionState.READY); await db.commit()
    finally:
        local_path.unlink(missing_ok=True)


@celery_app.task(name="ingest.parse_and_score", bind=True)
def parse_and_score_task(self, job_id: int) -> None:
    async def _runner() -> None:
        settings = get_settings()
        storage = ResumeStorageService()
        try:
            async with AsyncSessionLocal() as db:
                svc = IngestionJobService(db)
                job = await svc.claim(job_id, lease_seconds=settings.INGESTION_LEASE_SECONDS)
                await db.commit()
                if job is None:
                    return
                try:
                    await run_job(db=db, job=job, storage=storage)
                except BaseException as exc:  # noqa: BLE001 — classified below, re-raised
                    await db.rollback()
                    await _fail_job(db, job_id, exc)
                    raise
        finally:
            await engine.dispose()

    asyncio.run(_runner())
```

`_fail_job` re-reads the job, classifies `type(exc).__name__` against `RETRYABLE_ERRORS`/`TERMINAL_ERRORS`, transitions to `retryable_failed` or `terminal_failed` with the mapped code, commits, and logs metadata only (no provider body). Unknown exceptions default to `retryable_failed` (the sweeper caps attempts).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/tests/integration/test_tasks_ingest.py -q`
Expected: PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/tasks/ingest.py backend/tests/integration/test_tasks_ingest.py
git commit -m "feat(wp3): drive ingestion through the durable job state machine"
```

---

## Task 5: Idempotent score upsert

**Files:**
- Modify: `backend/app/scoring/pipeline.py`
- Test: `backend/tests/integration/test_scoring_upsert.py`

**Interfaces:**
- Produces: `ScoringPipeline.run` becomes idempotent for `(candidate_id, jd_id, rule_version_id)`: on unique conflict it returns the existing `PipelineResult` instead of raising.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_scoring_upsert.py
import pytest

from backend.app.scoring.pipeline import ScoringPipeline

pytestmark = pytest.mark.integration


async def test_rescoring_same_rule_version_is_idempotent(db_session, seeded_candidate_and_jd):
    candidate_id, jd_id = seeded_candidate_and_jd
    first = await ScoringPipeline(db=db_session).run(candidate_id=candidate_id, jd_id=jd_id)
    await db_session.commit()
    second = await ScoringPipeline(db=db_session).run(candidate_id=candidate_id, jd_id=jd_id)
    await db_session.commit()
    assert second.score_id == first.score_id
```

(`seeded_candidate_and_jd` seeds a candidate with `parsed_markdown`/`extracted_json` and a JD with an active rule version whose judge dimensions are empty so the LLM judge is not called — reuse the existing P2 E2E seeding helpers.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/integration/test_scoring_upsert.py -q`
Expected: FAIL — the second run raises `IntegrityError` on `uq_scores_candidate_jd_rule`.

- [ ] **Step 3: Implement the upsert**

In `pipeline.py`, replace the direct `self.db.add(score_row); await self.db.flush()` for the scored path with a PostgreSQL upsert that returns the existing row on conflict:

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = (
    pg_insert(Score)
    .values(**score_values)
    .on_conflict_do_nothing(
        index_elements=["candidate_id", "jd_id", "rule_version_id"]
    )
    .returning(Score.id)
)
score_id = (await self.db.execute(stmt)).scalar_one_or_none()
if score_id is None:
    score_id = (
        await self.db.execute(
            select(Score.id).where(
                Score.candidate_id == candidate.id,
                Score.jd_id == jd.id,
                Score.rule_version_id == rv.id,
            )
        )
    ).scalar_one()
```

Apply the same pattern to the hard-filter-reject `Score` insert. Keep the `AuditLog` insert only when a new score is created (guard on `score_id is None` before the conflict check by using `RETURNING` semantics — a null return means the row already existed, so skip the duplicate audit row).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/tests/integration/test_scoring_upsert.py -q`
Expected: PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/scoring/pipeline.py backend/tests/integration/test_scoring_upsert.py
git commit -m "feat(wp3): make scoring an idempotent upsert"
```

---

## Task 6: Beat sweeper — reclaim, re-enqueue, terminate

**Files:**
- Create: `backend/app/services/ingestion/sweeper.py`, `backend/app/tasks/sweep.py`
- Modify: `backend/app/tasks/celery_app.py`
- Test: `backend/tests/integration/test_ingestion_sweeper.py`

**Interfaces:**
- Produces:
  - `async sweep(db: AsyncSession, *, now: datetime, max_attempts: int) -> SweepReport` where `SweepReport(reclaimed: int, requeued: list[int], terminated: int)`.
  - `sweep_task()` Celery Beat task that opens a session, runs `sweep`, commits, and enqueues each `requeued` job id.
  - Beat schedule entry `ingestion-sweep` every `INGESTION_SWEEP_INTERVAL_SECONDS`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_ingestion_sweeper.py
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import IngestionJob
from backend.app.services.ingestion.sweeper import sweep
from backend.app.services.ingestion.states import IngestionState

pytestmark = pytest.mark.integration


async def test_expired_lease_is_reclaimed_and_requeued(db_session, stored_job_factory):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = await stored_job_factory(state=IngestionState.PARSING, lease_expires_at=past, attempts=1)
    await db_session.commit()
    report = await sweep(db_session, now=datetime.now(timezone.utc), max_attempts=3)
    await db_session.commit()
    refreshed = await db_session.get(IngestionJob, job.id)
    assert job.id in report.requeued
    assert refreshed.state == IngestionState.QUEUED.value


async def test_retryable_at_cap_terminates(db_session, stored_job_factory):
    job = await stored_job_factory(state=IngestionState.RETRYABLE_FAILED, attempts=3)
    await db_session.commit()
    report = await sweep(db_session, now=datetime.now(timezone.utc), max_attempts=3)
    await db_session.commit()
    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.TERMINAL_FAILED.value
    assert report.terminated == 1
```

Add a `stored_job_factory` fixture to `backend/tests/integration/conftest.py` that inserts an `IngestionJob` with given `state`, `attempts`, and optional `lease_expires_at`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/integration/test_ingestion_sweeper.py -q`
Expected: FAIL with `ModuleNotFoundError: backend.app.services.ingestion.sweeper`.

- [ ] **Step 3: Implement the sweeper**

```python
# backend/app/services/ingestion/sweeper.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob
from backend.app.services.ingestion.states import IngestionState


@dataclass
class SweepReport:
    reclaimed: int = 0
    requeued: list[int] = field(default_factory=list)
    terminated: int = 0


_PROCESSING = tuple(s.value for s in (IngestionState.PARSING, IngestionState.EXTRACTING, IngestionState.SCORING))


async def sweep(db: AsyncSession, *, now: datetime, max_attempts: int) -> SweepReport:
    report = SweepReport()

    # 1. Reclaim lease-expired processing jobs.
    stuck = (
        await db.execute(
            select(IngestionJob)
            .where(IngestionJob.state.in_(_PROCESSING))
            .where(IngestionJob.lease_expires_at.is_not(None))
            .where(IngestionJob.lease_expires_at < now)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for job in stuck:
        job.state = IngestionState.RETRYABLE_FAILED.value
        job.lease_expires_at = None
        report.reclaimed += 1

    # 2. Re-enqueue or terminate retryable_failed jobs.
    retryable = (
        await db.execute(
            select(IngestionJob)
            .where(IngestionJob.state == IngestionState.RETRYABLE_FAILED.value)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for job in retryable:
        if job.attempts >= max_attempts:
            job.state = IngestionState.TERMINAL_FAILED.value
            report.terminated += 1
        else:
            job.state = IngestionState.QUEUED.value
            report.requeued.append(job.id)

    await db.flush()
    return report
```

Note: step 1's reclaimed jobs become `retryable_failed` and are re-evaluated by step 2 in the same sweep, so a stuck job at the cap terminates immediately.

```python
# backend/app/tasks/sweep.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal, engine
from backend.app.services.ingestion.sweeper import sweep
from backend.app.tasks.celery_app import celery_app


@celery_app.task(name="ingest.sweep")
def sweep_task() -> dict:
    async def _runner() -> dict:
        settings = get_settings()
        try:
            async with AsyncSessionLocal() as db:
                report = await sweep(
                    db, now=datetime.now(timezone.utc), max_attempts=settings.INGESTION_MAX_ATTEMPTS
                )
                await db.commit()
        finally:
            await engine.dispose()
        for job_id in report.requeued:
            celery_app.send_task("ingest.parse_and_score", args=[job_id])
        return {"reclaimed": report.reclaimed, "requeued": len(report.requeued), "terminated": report.terminated}

    return asyncio.run(_runner())
```

Wire Beat in `celery_app.py`: add `"backend.app.tasks.sweep"` to `include`, and:

```python
celery_app.conf.beat_schedule = {
    "ingestion-sweep": {
        "task": "ingest.sweep",
        "schedule": float(settings.INGESTION_SWEEP_INTERVAL_SECONDS),
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/tests/integration/test_ingestion_sweeper.py -q`
Expected: PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/ingestion/sweeper.py backend/app/tasks/sweep.py backend/app/tasks/celery_app.py backend/tests/integration/
git commit -m "feat(wp3): add beat sweeper for retry re-enqueue and lease reclaim"
```

---

## Task 7: Async upload, batch upload, and status endpoints

**Files:**
- Modify: `backend/app/routers/candidates.py`
- Test: `backend/tests/unit/test_candidates_upload_async.py`, extend `backend/tests/integration/test_candidates_api.py`

**Interfaces:**
- Produces:
  - `POST /api/v1/candidates/upload` → `202 {job_id, batch_id, state}` (enqueues; no inline parse).
  - `POST /api/v1/candidates/batch` → `202 {batch_id, jobs: [{job_id, state, error_code?}]}`.
  - `GET /api/v1/candidates/jobs/{job_id}` → `{state, attempts, last_error_code, candidate_id, score_id, batch_id}` or `404`.
  - `GET /api/v1/candidates/batches/{batch_id}` → `{total, by_state}` or `404`.

- [ ] **Step 1: Write the failing unit test**

```python
# backend/tests/unit/test_candidates_upload_async.py
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
# reuse existing helpers that override auth + storage + enqueue for unit scope


@pytest.mark.asyncio
async def test_upload_returns_202_and_enqueues(monkeypatch, fake_upload_env):
    enqueued = []
    monkeypatch.setattr(
        "backend.app.routers.candidates.enqueue_job", lambda job_id: enqueued.append(job_id)
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/candidates/upload",
            files={"file": ("r.pdf", fake_upload_env.pdf_bytes, "application/pdf")},
            headers=fake_upload_env.auth_headers,
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "queued" and body["job_id"] and enqueued == [body["job_id"]]
```

(`fake_upload_env` overrides `require_roles`, `UploadValidator`, `ResumeStorageService`, and the DB dependency with in-memory doubles so no real services are needed; model it on the existing candidate-API unit doubles.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/unit/test_candidates_upload_async.py -q`
Expected: FAIL — endpoint still returns `200` with `candidate_id`.

- [ ] **Step 3: Rewrite the upload endpoint**

Replace the body of `upload_resume` so that after `storage.store(...)` it creates a job and enqueues instead of calling `run_parse_and_score`:

```python
class UploadResponse(BaseModel):
    job_id: int
    batch_id: str | None = None
    state: str = "queued"


def enqueue_job(job_id: int) -> None:
    from backend.app.tasks.ingest import parse_and_score_task
    parse_and_score_task.delay(job_id)


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_resume(...):
    artifact = None
    try:
        settings = get_settings()
        artifact = await UploadValidator().validate(file)
        await get_malware_scanner(settings.MALWARE_SCAN_MODE).scan(artifact)
        original_name_cipher = encrypt_pii(artifact.original_filename)
        storage = ResumeStorageService()
        stored = await storage.store(artifact)
        raw_file = RawFileReference(
            object_key=stored.object_key, sha256=stored.sha256,
            size_bytes=stored.size_bytes, content_type=stored.content_type,
            original_name_cipher=original_name_cipher,
        )
        svc = IngestionJobService(db)
        job, created = await svc.create_or_reuse(
            raw_file=raw_file, source="upload", source_external_id=None,
            jd_code=jd_code, actor=f"user:{current_user.id}",
            trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
        )
        if not created:
            # idempotent resubmission: drop the redundant just-stored object
            await storage.delete(stored.object_key)
        await db.commit()
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except StorageError as exc:
        await db.rollback()
        raise _upload_error(503, "object_storage_unavailable", "Resume storage is unavailable") from exc
    except Exception:
        await db.rollback()
        if artifact is not None and "stored" in dir():
            ...  # compensating delete handled by storage.store on its own failure
        raise
    finally:
        if artifact is not None:
            artifact.cleanup()
        await file.close()
    if created:
        enqueue_job(job.id)
    return UploadResponse(job_id=job.id, batch_id=str(job.batch_id) if job.batch_id else None, state=job.state)
```

Note: compensating deletion for a failed job insert — if `create_or_reuse`/`commit` fails after the object was stored, delete `stored.object_key` in the `except Exception` block before re-raising. Add that explicitly.

- [ ] **Step 4: Add batch and status endpoints**

```python
class BatchJobResult(BaseModel):
    job_id: int | None = None
    state: str
    error_code: str | None = None


class BatchResponse(BaseModel):
    batch_id: str
    jobs: list[BatchJobResult]


@router.post("/batch", response_model=BatchResponse, status_code=202)
async def upload_batch(
    files: list[UploadFile] = File(...),
    jd_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
) -> BatchResponse:
    settings = get_settings()
    if len(files) > settings.INGESTION_BATCH_MAX_FILES:
        raise _upload_error(413, "batch_too_large", "Too many files in one batch")
    from uuid import uuid4
    batch_id = uuid4()
    svc = IngestionJobService(db)
    results: list[BatchJobResult] = []
    to_enqueue: list[int] = []
    for file in files:
        try:
            artifact = await UploadValidator().validate(file)
            await get_malware_scanner(settings.MALWARE_SCAN_MODE).scan(artifact)
            storage = ResumeStorageService()
            stored = await storage.store(artifact)
            raw_file = RawFileReference(
                object_key=stored.object_key, sha256=stored.sha256,
                size_bytes=stored.size_bytes, content_type=stored.content_type,
                original_name_cipher=encrypt_pii(artifact.original_filename),
            )
            job, created = await svc.create_or_reuse(
                raw_file=raw_file, source="upload", source_external_id=None,
                jd_code=jd_code, actor=f"user:{current_user.id}", batch_id=batch_id,
            )
            if not created:
                await storage.delete(stored.object_key)
            else:
                to_enqueue.append(job.id)
            results.append(BatchJobResult(job_id=job.id, state=job.state))
        except UploadValidationError as exc:
            results.append(BatchJobResult(state="terminal_failed", error_code=exc.detail.get("code")))
        finally:
            if "artifact" in dir() and artifact is not None:
                artifact.cleanup()
            await file.close()
    await db.commit()
    for job_id in to_enqueue:
        enqueue_job(job_id)
    return BatchResponse(batch_id=str(batch_id), jobs=results)


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db),
                  _u: User = Depends(require_roles(*WRITE_ROLES))) -> dict:
    job = await db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "state": job.state, "attempts": job.attempts, "last_error_code": job.last_error_code,
        "candidate_id": job.candidate_id, "score_id": job.score_id,
        "batch_id": str(job.batch_id) if job.batch_id else None,
    }


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str, db: AsyncSession = Depends(get_db),
                    _u: User = Depends(require_roles(*WRITE_ROLES))) -> dict:
    from uuid import UUID
    try:
        parsed = UUID(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="batch not found") from exc
    counts = await IngestionJobService(db).batch_counts(parsed)
    if not counts:
        raise HTTPException(status_code=404, detail="batch not found")
    return {"total": sum(counts.values()), "by_state": counts}
```

Update imports in `candidates.py`: add `IngestionJob`, `IngestionJobService`, `structlog`, and remove the now-unused `ScoringPipeline`/`run_parse_and_score` import if nothing else uses them (the re-score endpoint still uses `ScoringPipeline`).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest backend/tests/unit/test_candidates_upload_async.py -q`
Expected: PASS.

- [ ] **Step 6: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/routers/candidates.py backend/tests/unit/test_candidates_upload_async.py
git commit -m "feat(wp3): async upload, batch upload, and job/batch status endpoints"
```

---

## Task 8: End-to-end integration, crash recovery, docs, and verify gate

**Files:**
- Create: `backend/tests/integration/test_ingestion_async.py`
- Modify: `backend/tests/integration/test_candidates_api.py`, `scripts/verify.py`, `docker-compose.yml`, `.env.example`, `README.md`
- Modify docs: `docs/superpowers/plans/README.md`, `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`

- [ ] **Step 1: Write the end-to-end async test**

```python
# backend/tests/integration/test_ingestion_async.py
import pytest

pytestmark = pytest.mark.integration


async def test_upload_returns_202_then_worker_completes(client, auth_headers, valid_pdf_bytes, celery_worker, poll_job):
    headers = await auth_headers("hr")
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", valid_pdf_bytes, "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    final = await poll_job(job_id, headers, until={"ready", "completed"}, timeout=60)
    assert final["state"] in {"ready", "completed"}
    assert final["candidate_id"] is not None


async def test_crash_recovery_no_duplicate_candidate(client, auth_headers, valid_pdf_bytes, db_session, run_one_sweep, poll_job):
    # simulate a crashed worker: insert a job stuck in 'parsing' with an expired lease,
    # pointing at a stored object; run one sweep; a worker then completes it exactly once.
    ...
```

Add a `poll_job` fixture (polls `GET /candidates/jobs/{id}` until state in a set or timeout) and a `run_one_sweep` fixture (calls `ingest.sweep` synchronously) to `backend/tests/integration/conftest.py`.

- [ ] **Step 2: Run it (expect the async contract to hold)**

Run: `uv run pytest backend/tests/integration/test_ingestion_async.py -q`
Expected: PASS with a real worker + beat available.

- [ ] **Step 3: Update the existing candidate API integration test**

Change `test_candidates_api.py` expectations: upload now returns `202` with `job_id`; the candidate/score are observed by polling the job, not from the upload response.

- [ ] **Step 4: Add Beat to compose and verify.py**

In `docker-compose.yml`, add a `beat` service mirroring the worker command with `celery -A backend.app.tasks.celery_app beat -l info`. In `scripts/verify.py`, start (or single-shot run) the sweep during the integration gate and assert clean state (no orphaned objects/jobs/temp files) after.

- [ ] **Step 5: Update `.env.example` and README**

Add the four `INGESTION_*` settings with defaults and comments; document the async `202` upload contract, the batch endpoint, the job/batch status endpoints, the Beat process, and the duplicate-score reconciliation gate (SQL to count `(candidate_id, jd_id, rule_version_id)` duplicates before deploy).

- [ ] **Step 6: Full local matrix**

```bash
uv sync --extra dev --locked
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
uv run python scripts/verify.py
```

Expected: offline suite passes; `verify.py` passes with real PG/Redis/MinIO/worker/beat, migration round-trip, and clean-state assertions.

- [ ] **Step 7: Update roadmap and plan index**

Mark WP3 In progress → (after CI) Complete in `docs/superpowers/plans/README.md`; update the roadmap WP3 status and the "Batch upload and async status" / "Candidate upload API" traceability rows. Change WP4 to Ready for planning only after hosted CI passes.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/integration/ scripts/verify.py docker-compose.yml .env.example README.md docs/
git commit -m "test(wp3): end-to-end async ingestion, crash recovery, docs, and verify gate"
```

---

## Task 9: Push, hosted CI, and WP3 exit review

- [x] **Step 1: Push the branch and open a PR**

```bash
git push -u origin codex/wp3-durable-async-ingestion
gh pr create --title "WP3: durable asynchronous ingestion and batch processing" --body "<summary + exit evidence>"
```

- [x] **Step 2: Confirm hosted CI**

Watch the `verify` run for the PR head; require `unit-and-static (3.10)`, `unit-and-static (3.14)`, and `integration` to pass.

- [x] **Step 3: Record completion evidence**

In this plan and the roadmap: exact commit range, hosted run URL, offline/integration counts, and the duplicate-score reconciliation result on the configured deployment.

- [x] **Step 4: Mark WP3 Complete and WP4 Ready for planning**

Only after every offline and integration exit criterion and hosted CI pass.

### Completion evidence (2026-07-21)

- **Scoped commits:** WP3 range `5c57cab..4bd7130` (design spec, plan, and 18 implementation/fix commits) on branch `codex/wp3-durable-async-ingestion`, [PR #3](https://github.com/Forcome-Database/SmartScreenAgent/pull/3) into `main`.
- **Hosted CI:** [`verify` run 29795950194](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29795950194) passed `unit-and-static (3.10)`, `unit-and-static (3.14)`, and strict `integration` at commit `4bd7130`. The Linux `integration` job runs the full `scripts/verify.py` (managed compose stack, migration round trip, clean-state assertions) which cannot bind port 61000 on the Windows dev host.
- **Local evidence (Windows/Python 3.14):** 252 offline tests and 64 integration tests passed (real PostgreSQL/Redis/MinIO/worker, including end-to-end upload→worker→status and crash-recovery-produces-one-candidate); Ruff and mypy clean; Alembic upgrade/downgrade round trip to head `2f27938b430b`.
- **Migrations:** `0e57f449e555` (ingestion_jobs table + scores uniqueness) and `2f27938b430b` (sha256 active-job partial unique index). The `scores` unique constraint has a duplicate-row reconciliation gate documented in the README; the configured deployment must run it before applying.
- **Deferred non-blocking follow-ups:** remove the now-dead `run_parse_and_score`; close the narrow duplicate-branch crash window (object deleted before `candidate_id` commit); add a `lease_expired` marker on sweeper reclaim.

---

## Self-Review

**Spec coverage:**
- §5.1 `ingestion_jobs` → Task 2. §5.2 score uniqueness → Task 2 + Task 5. §6 state machine → Task 1. §7.1 upload path → Task 7. §7.2 worker path → Task 4. §7.3 sweeper → Task 6. §8 idempotency → Tasks 3/5/7. §9 HTTP + error mapping → Task 7. §10 batch → Task 7. §11 retention/`deleted` state → Task 1 (state) + compensating deletes in Tasks 4/7. §12 config → Task 1. §13 tests → Tasks 1–8. §14 rollout → Task 8 docs. §15 exit → Task 9.
- Gap check: the `deleted` state has no user-facing deletion endpoint in WP3 (retention deferred, spec §11); the transition exists and is exercised by unit tests only. Acceptable per non-goals.

**Placeholder scan:** the `...` markers in Task 4/8 test arrange blocks and the Task 7 compensating-delete note are deliberate "reuse existing fixture/helper" pointers, not logic placeholders; each names the exact fixture to reuse. The implementer must fill them from the referenced existing tests.

**Type consistency:** `IngestionState` values, `IngestionJobService` method names (`create_or_reuse`, `claim`, `transition`, `batch_counts`), `RawFileReference` fields, and `SweepReport` fields are used consistently across Tasks 1–8. `enqueue_job(job_id)` is defined in Task 7 and used by the batch endpoint in the same task.
