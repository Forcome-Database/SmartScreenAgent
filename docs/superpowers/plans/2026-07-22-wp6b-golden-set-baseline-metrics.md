# WP6b Golden Set and Baseline Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a curator import a CSV of `(candidate_id, jd_code, label)` ground-truth labels (one authoritative label per `(candidate, JD)`, upserted) and surface a live baseline metrics report (confusion matrix + precision/recall/F1/accuracy, overall and per-JD) comparing the AI's advance/reject decision to the golden label — over the WP4/WP5/WP6a surface.

**Architecture:** Backend golden-set router + service + schemas over the existing `GoldenSet` model (plus a small Alembic migration adding a `label` CHECK). CSV parse/validation and metrics aggregation live in the service; metrics are computed live (no snapshot). Frontend extends WP5 with a `/golden-set` import+list page (import control role-gated) and a `/reports/baseline` metrics page, over the existing BFF. No scoring change; the golden set never mutates a score.

**Tech Stack:** Python 3.10–3.14, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, PostgreSQL; Next.js 15, TanStack Query, zod, Vitest, Playwright.

## Global Constraints

- Backend default CI runs `pytest -m "not integration and not external_contract"`; offline & deterministic. Integration on this host uses the env prefix `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000" uv run pytest ...` (test stack: PG 25432, MinIO 9000, Redis 56379).
- Import requires role in `("hr_lead","admin")`; list & metrics require role in `("hr","hr_lead","admin")` via `require_roles(*roles)` (`backend/app/deps.py`, which RETURNS the `User`). Errors are `{code, message}` (FastAPI wraps as `{"detail": {code, message}}`); offset pagination is `{items, page, page_size, total}` reusing `backend/app/services/read/pagination.py` (`Page`, `page_params`, `PageMeta`).
- Golden label ∈ `('advance','reject','borderline')` (DB CHECK + API validation). AI prediction derives from `score.grade`: AI reject ⟺ `grade == "rejected"`, else AI advance (same convention as WP6a). Binary metrics treat `advance` as positive; `borderline` is excluded from the confusion matrix; a golden entry with no score is `uncovered` (excluded). When a `(candidate, JD)` has multiple scores, use the most recent (`created_at` desc, then `id` desc).
- One golden row per `(candidate_id, jd_id)` — the import upserts on the existing `uq_golden_set_cand_jd`. `GoldenSet` uses `Base` (NOT `TimestampMixin`): it has `imported_at` (set to `func.now()` on insert/update), no `updated_at`.
- No candidate PII, ciphertext, or object keys in any golden-set/metrics response — only `candidate_id`/`jd_code`/`label`/`imported_by_display_name` refs + aggregate numbers.
- Alembic head before WP6b is `1e9b39dbf340` (WP6a). Bump BOTH the expected head in `backend/tests/integration/test_db_migrations.py` AND `HEAD_REVISION` in `scripts/verify.py` to the new revision. Run `uv run ruff check backend` and `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports` before each backend commit. Frontend gates (`cd frontend`): `npm run lint && npm run typecheck && npm run test`.
- Base UI (not Radix) in the frontend: use `<Button render={<a/>}>` not `asChild`. `useSearchParams` needs a `<Suspense>` boundary. Exclude `.superpowers/`, `backend.zip`. End every commit message with a blank line then `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

**Backend — Create:**
- `migrations/versions/<rev>_wp6b_golden_set_label_check.py` — label CHECK.
- `backend/app/services/golden_set.py` — `parse_golden_csv`, `import_golden_set`, `list_golden_set`, `metric_stats`, `golden_metrics`.
- `backend/app/schemas/golden_set.py` — request/response models.
- `backend/app/routers/golden_set.py` — POST import + GET list + GET metrics.
- Tests: `backend/tests/unit/test_golden_set_service.py`, `backend/tests/unit/test_golden_metrics.py`, `backend/tests/integration/test_golden_set_api.py`.

**Backend — Modify:**
- `backend/app/models/golden_set.py` — add `CheckConstraint` to `__table_args__`.
- `backend/app/config.py` — add `GOLDEN_IMPORT_MAX_ROWS`.
- `backend/app/main.py` — register the golden-set router.
- `backend/tests/integration/test_db_migrations.py` + `scripts/verify.py` — bump head to `<rev>`.

**Frontend — Create (`frontend/`):**
- `src/app/(app)/golden-set/page.tsx` (server: reads role), `src/components/golden-set-view.tsx` (client), `src/app/api/golden-set/import/route.ts` (BFF multipart).
- `src/app/(app)/reports/baseline/page.tsx`.
- Tests: `src/components/golden-set-view.test.tsx`, `src/app/(app)/reports/baseline/report.test.tsx`, `e2e/golden-set.spec.ts`.

**Frontend — Modify:**
- `src/lib/schemas.ts` — golden-set + metrics zod schemas.
- `src/components/app-shell.tsx` — nav links.

---

## Task 1: GoldenSet label CHECK + migration + config + head bumps

**Files:**
- Modify: `backend/app/models/golden_set.py`, `backend/app/config.py`, `backend/tests/integration/test_db_migrations.py`, `scripts/verify.py`
- Create: `migrations/versions/<rev>_wp6b_golden_set_label_check.py`
- Test: the existing `backend/tests/integration/test_db_migrations.py` (upgrade-to-head).

**Interfaces:**
- Produces: `golden_set` constraint `ck_golden_set_label (label IN ('advance','reject','borderline'))`; new Alembic head `<rev>`; setting `GOLDEN_IMPORT_MAX_ROWS: int = 5000`.

- [ ] **Step 1: Add the CHECK to the model**

```python
# backend/app/models/golden_set.py  (replace imports + __table_args__)
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class GoldenSet(Base):
    __tablename__ = "golden_set"

    __table_args__ = (
        UniqueConstraint("candidate_id", "jd_id", name="uq_golden_set_cand_jd"),
        CheckConstraint(
            "label IN ('advance', 'reject', 'borderline')", name="ck_golden_set_label"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id"), nullable=False
    )
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
```

- [ ] **Step 2: Add the config setting**

In `backend/app/config.py`, after the `READ_PAGE_SIZE_MAX` line (in the "Read APIs (WP4)" block), add:

```python
    # Golden set (WP6b)
    GOLDEN_IMPORT_MAX_ROWS: int = 5000
```

- [ ] **Step 3: Generate the migration**

Run: `uv run alembic revision -m "wp6b golden set label check"` (NOT `--autogenerate`) — note the generated revision id `<rev>`. Confirm `down_revision = "1e9b39dbf340"`. Replace `upgrade`/`downgrade`:

```python
def upgrade() -> None:
    op.create_check_constraint(
        "ck_golden_set_label", "golden_set", "label IN ('advance', 'reject', 'borderline')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_golden_set_label", "golden_set", type_="check")
```

- [ ] **Step 4: Bump the expected head in the migration test and verify.py**

In `backend/tests/integration/test_db_migrations.py`, replace the expected-head literal `1e9b39dbf340` with `<rev>` (search for the literal). In `scripts/verify.py`, replace `HEAD_REVISION = "1e9b39dbf340"` with `HEAD_REVISION = "<rev>"`.

- [ ] **Step 5: Run the migration test (integration)**

Run: `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000" uv run pytest backend/tests/integration/test_db_migrations.py -q`
Expected: PASS (upgrades to `<rev>`, round-trips).

- [ ] **Step 6: Offline + ruff + mypy + commit**

```bash
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/models/golden_set.py backend/app/config.py migrations/versions/ backend/tests/integration/test_db_migrations.py scripts/verify.py
git commit -m "feat(wp6b): golden_set label CHECK migration + import row cap config"
```

---

## Task 2: Golden-set service — CSV parse, import upsert, list

**Files:**
- Create: `backend/app/services/golden_set.py`
- Test: `backend/tests/unit/test_golden_set_service.py`

**Interfaces:**
- Produces: `VALID_LABELS`; dataclasses `ParsedRow(row, candidate_id, jd_code, label)`, `RowError(row, candidate_id, jd_code, reason)`; exceptions `InvalidCSV`, `GoldenImportTooLarge`; `parse_golden_csv(content: bytes, *, max_rows: int) -> tuple[list[ParsedRow], list[RowError]]`; `async import_golden_set(db, *, parsed, importer_id) -> tuple[int, int, list[RowError]]` (created, updated, db-errors); `async list_golden_set(db, *, jd_code, page) -> tuple[list[tuple[GoldenSet, str, str]], int]` (rows of `(golden, jd_code, imported_by_display_name)` + total).

- [ ] **Step 1: Write the failing unit test (parsing)**

```python
# backend/tests/unit/test_golden_set_service.py
from backend.app.services.golden_set import (
    GoldenImportTooLarge,
    InvalidCSV,
    parse_golden_csv,
)


def _csv(*rows: str) -> bytes:
    return ("candidate_id,jd_code,label\n" + "\n".join(rows) + "\n").encode("utf-8")


def test_parse_valid_and_row_errors():
    parsed, errors = parse_golden_csv(
        _csv("1,FT,advance", "2,FT,reject", "x,FT,advance", "3,FT,bogus", "4,,advance"),
        max_rows=100,
    )
    assert [(p.candidate_id, p.jd_code, p.label) for p in parsed] == [
        (1, "FT", "advance"),
        (2, "FT", "reject"),
    ]
    reasons = {(e.row, e.reason) for e in errors}
    assert reasons == {(3, "invalid_candidate_id"), (4, "invalid_label"), (5, "missing_jd_code")}


def test_parse_missing_header_raises():
    import pytest

    with pytest.raises(InvalidCSV):
        parse_golden_csv(b"a,b,c\n1,2,3\n", max_rows=100)


def test_parse_row_cap():
    import pytest

    rows = [f"{i},FT,advance" for i in range(3)]
    with pytest.raises(GoldenImportTooLarge):
        parse_golden_csv(_csv(*rows), max_rows=2)
```

- [ ] **Step 2: Run it (fails)** — `uv run pytest backend/tests/unit/test_golden_set_service.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement the service**

```python
# backend/app/services/golden_set.py
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, Candidate, GoldenSet, User
from backend.app.services.read.pagination import Page

VALID_LABELS = ("advance", "reject", "borderline")
_REQUIRED_COLUMNS = {"candidate_id", "jd_code", "label"}


class InvalidCSV(Exception):
    """Raised when the upload is not CSV or lacks the required header."""


class GoldenImportTooLarge(Exception):
    """Raised when a single import exceeds the configured row cap."""


@dataclass(frozen=True)
class ParsedRow:
    row: int
    candidate_id: int
    jd_code: str
    label: str


@dataclass(frozen=True)
class RowError:
    row: int
    candidate_id: int | None
    jd_code: str | None
    reason: str


def parse_golden_csv(content: bytes, *, max_rows: int) -> tuple[list[ParsedRow], list[RowError]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    header = {(name or "").strip() for name in (reader.fieldnames or [])}
    if not _REQUIRED_COLUMNS.issubset(header):
        raise InvalidCSV()
    parsed: list[ParsedRow] = []
    errors: list[RowError] = []
    for i, raw in enumerate(reader, start=1):
        if i > max_rows:
            raise GoldenImportTooLarge()
        cid_text = (raw.get("candidate_id") or "").strip()
        jd_code = (raw.get("jd_code") or "").strip()
        label = (raw.get("label") or "").strip()
        try:
            cid = int(cid_text)
        except ValueError:
            errors.append(RowError(i, None, jd_code or None, "invalid_candidate_id"))
            continue
        if label not in VALID_LABELS:
            errors.append(RowError(i, cid, jd_code or None, "invalid_label"))
            continue
        if not jd_code:
            errors.append(RowError(i, cid, None, "missing_jd_code"))
            continue
        parsed.append(ParsedRow(i, cid, jd_code, label))
    return parsed, errors


async def import_golden_set(
    db: AsyncSession, *, parsed: list[ParsedRow], importer_id: int
) -> tuple[int, int, list[RowError]]:
    if not parsed:
        return 0, 0, []
    jd_codes = {r.jd_code for r in parsed}
    jd_map = dict(
        (await db.execute(select(JD.code, JD.id).where(JD.code.in_(jd_codes)))).all()
    )
    cand_ids = {r.candidate_id for r in parsed}
    known_cands = set(
        (await db.execute(select(Candidate.id).where(Candidate.id.in_(cand_ids)))).scalars().all()
    )
    # keys that already exist, so we can count created vs updated
    resolved_keys = [
        (r.candidate_id, jd_map[r.jd_code])
        for r in parsed
        if r.jd_code in jd_map and r.candidate_id in known_cands
    ]
    existing: set[tuple[int, int]] = set()
    if resolved_keys:
        existing = set(
            (
                await db.execute(
                    select(GoldenSet.candidate_id, GoldenSet.jd_id).where(
                        tuple_(GoldenSet.candidate_id, GoldenSet.jd_id).in_(resolved_keys)
                    )
                )
            ).all()
        )
    created = updated = 0
    errors: list[RowError] = []
    seen: set[tuple[int, int]] = set()
    for r in parsed:
        jd_id = jd_map.get(r.jd_code)
        if jd_id is None:
            errors.append(RowError(r.row, r.candidate_id, r.jd_code, "unknown_jd_code"))
            continue
        if r.candidate_id not in known_cands:
            errors.append(RowError(r.row, r.candidate_id, r.jd_code, "unknown_candidate"))
            continue
        key = (r.candidate_id, jd_id)
        if key in existing or key in seen:
            updated += 1
        else:
            created += 1
        seen.add(key)
        await db.execute(
            pg_insert(GoldenSet)
            .values(
                candidate_id=r.candidate_id,
                jd_id=jd_id,
                label=r.label,
                imported_at=func.now(),
                imported_by_user_id=importer_id,
            )
            .on_conflict_do_update(
                constraint="uq_golden_set_cand_jd",
                set_={
                    "label": r.label,
                    "imported_at": func.now(),
                    "imported_by_user_id": importer_id,
                },
            )
        )
    await db.commit()
    return created, updated, errors


async def list_golden_set(
    db: AsyncSession, *, jd_code: str | None, page: Page
) -> tuple[list[tuple[GoldenSet, str, str]], int]:
    base = (
        select(GoldenSet, JD.code, User.display_name)
        .join(JD, JD.id == GoldenSet.jd_id)
        .join(User, User.id == GoldenSet.imported_by_user_id)
    )
    if jd_code is not None:
        base = base.where(JD.code == jd_code)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(GoldenSet.imported_at.desc(), GoldenSet.id.desc())
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).all()
    return [(g, code, name) for g, code, name in rows], total
```

- [ ] **Step 4: Run tests (pass)** — `uv run pytest backend/tests/unit/test_golden_set_service.py -q` → PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/golden_set.py backend/tests/unit/test_golden_set_service.py
git commit -m "feat(wp6b): golden-set service (csv parse, import upsert, list)"
```

---

## Task 3: Golden-set schemas + router (import + list)

**Files:**
- Create: `backend/app/schemas/golden_set.py`, `backend/app/routers/golden_set.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_golden_set_api.py`

**Interfaces:**
- Consumes: Task 2 service, `require_roles`, `get_db`, `get_settings`.
- Produces routes: `POST /api/v1/golden-set/import`, `GET /api/v1/golden-set`. Schemas `GoldenImportError`, `GoldenImportResult`, `GoldenSetItem`, `GoldenSetList`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_golden_set_api.py
from datetime import datetime, timezone

import pytest

from backend.app.models import JD, Candidate
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration


async def _seed_candidate(db, *, name="张三", pii_hash="h1"):
    cand = Candidate(
        source="upload", name_cipher=encrypt_pii(name), pii_hash=pii_hash, extracted_json={}
    )
    db.add(cand)
    await db.flush()
    return cand


async def _seed_jd(db, *, code="FT"):
    jd = JD(code=code, name="Foreign Trade", description="", status="active")
    db.add(jd)
    await db.flush()
    return jd


def _csv_bytes(*rows: str) -> bytes:
    return ("candidate_id,jd_code,label\n" + "\n".join(rows) + "\n").encode("utf-8")


async def test_import_creates_then_updates_one_row(client, db_session, auth_headers):
    cand = await _seed_candidate(db_session)
    await _seed_jd(db_session)
    await db_session.commit()
    headers = await auth_headers("hr_lead")
    r1 = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
        headers=headers,
    )
    assert r1.status_code == 200 and r1.json()["created"] == 1 and r1.json()["updated"] == 0
    r2 = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,reject"), "text/csv")},
        headers=headers,
    )
    assert r2.status_code == 200 and r2.json()["created"] == 0 and r2.json()["updated"] == 1


async def test_import_row_errors_and_auth(client, db_session, auth_headers):
    cand = await _seed_candidate(db_session)
    await _seed_jd(db_session)
    await db_session.commit()
    body = _csv_bytes(f"{cand.id},NOPE,advance", "999999,FT,advance", f"{cand.id},FT,advance")
    r = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", body, "text/csv")},
        headers=await auth_headers("admin"),
    )
    assert r.status_code == 200
    reasons = {(e["row"], e["reason"]) for e in r.json()["errors"]}
    assert reasons == {(1, "unknown_jd_code"), (2, "unknown_candidate")}
    assert r.json()["created"] == 1 and r.json()["total"] == 3
    # plain hr may not import
    forbidden = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
        headers=await auth_headers("hr"),
    )
    assert forbidden.status_code == 403
    noauth = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
    )
    assert noauth.status_code == 401


async def test_invalid_csv_returns_422(client, db_session, auth_headers):
    r = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", b"a,b,c\n1,2,3\n", "text/csv")},
        headers=await auth_headers("admin"),
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_csv"


async def test_list_golden_set(client, db_session, auth_headers):
    cand = await _seed_candidate(db_session)
    await _seed_jd(db_session)
    await db_session.commit()
    await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", _csv_bytes(f"{cand.id},FT,advance"), "text/csv")},
        headers=await auth_headers("hr_lead"),
    )
    lst = await client.get("/api/v1/golden-set", headers=await auth_headers("hr"))
    assert lst.status_code == 200
    body = lst.json()
    assert body["total"] == 1 and body["items"][0]["label"] == "advance"
    assert body["items"][0]["jd_code"] == "FT" and "name_cipher" not in lst.text
```

- [ ] **Step 2: Run it (fails)** — routes missing.

- [ ] **Step 3: Implement schemas**

```python
# backend/app/schemas/golden_set.py
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.app.services.read.pagination import PageMeta


class GoldenImportError(BaseModel):
    row: int
    candidate_id: int | None
    jd_code: str | None
    reason: str


class GoldenImportResult(BaseModel):
    total: int
    created: int
    updated: int
    errors: list[GoldenImportError]


class GoldenSetItem(BaseModel):
    id: int
    candidate_id: int
    jd_code: str
    label: str
    imported_at: datetime
    imported_by_display_name: str


class GoldenSetList(PageMeta):
    items: list[GoldenSetItem]
```

- [ ] **Step 4: Implement the router**

```python
# backend/app/routers/golden_set.py
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import User
from backend.app.schemas.golden_set import (
    GoldenImportError,
    GoldenImportResult,
    GoldenSetItem,
    GoldenSetList,
)
from backend.app.services.golden_set import (
    GoldenImportTooLarge,
    InvalidCSV,
    RowError,
    import_golden_set,
    list_golden_set,
    parse_golden_csv,
)
from backend.app.services.read.pagination import Page, page_params

router = APIRouter(prefix="/api/v1", tags=["golden-set"])
IMPORT_ROLES = ("hr_lead", "admin")
READ_ROLES = ("hr", "hr_lead", "admin")


def _err(e: RowError) -> GoldenImportError:
    return GoldenImportError(row=e.row, candidate_id=e.candidate_id, jd_code=e.jd_code, reason=e.reason)


@router.post("/golden-set/import", response_model=GoldenImportResult)
async def import_golden(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*IMPORT_ROLES)),
) -> GoldenImportResult:
    settings = get_settings()
    content = await file.read()
    try:
        parsed, fmt_errors = parse_golden_csv(content, max_rows=settings.GOLDEN_IMPORT_MAX_ROWS)
    except InvalidCSV as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_csv", "message": "无法解析 CSV 或缺少必需表头"},
        ) from exc
    except GoldenImportTooLarge as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "golden_import_too_large",
                "message": f"单次导入不超过 {settings.GOLDEN_IMPORT_MAX_ROWS} 行",
            },
        ) from exc
    created, updated, db_errors = await import_golden_set(db, parsed=parsed, importer_id=user.id)
    all_errors = sorted([*fmt_errors, *db_errors], key=lambda e: e.row)
    return GoldenImportResult(
        total=len(parsed) + len(fmt_errors),
        created=created,
        updated=updated,
        errors=[_err(e) for e in all_errors],
    )


@router.get("/golden-set", response_model=GoldenSetList)
async def list_entries(
    jd_code: str | None = None,
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> GoldenSetList:
    rows, total = await list_golden_set(db, jd_code=jd_code, page=page)
    items = [
        GoldenSetItem(
            id=g.id,
            candidate_id=g.candidate_id,
            jd_code=code,
            label=g.label,
            imported_at=g.imported_at,
            imported_by_display_name=name,
        )
        for g, code, name in rows
    ]
    return GoldenSetList(items=items, page=page.page, page_size=page.page_size, total=total)
```

Register in `backend/app/main.py`: add `from backend.app.routers import golden_set as golden_set_router` (alphabetically among the router imports) and `app.include_router(golden_set_router.router)` (alphabetically among the `include_router` calls).

- [ ] **Step 5: Run tests (pass)** — with the integration env prefix → PASS.

- [ ] **Step 6: Offline + ruff + mypy + commit**

```bash
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/schemas/golden_set.py backend/app/routers/golden_set.py backend/app/main.py backend/tests/integration/test_golden_set_api.py
git commit -m "feat(wp6b): golden-set import + list endpoints"
```

---

## Task 4: Baseline metrics service + endpoint

**Files:**
- Modify: `backend/app/services/golden_set.py`, `backend/app/routers/golden_set.py`, `backend/app/schemas/golden_set.py`
- Test: `backend/tests/unit/test_golden_metrics.py`, extend `backend/tests/integration/test_golden_set_api.py`

**Interfaces:**
- Produces: `metric_stats(tp, fp, tn, fn) -> dict`; `async golden_metrics(db, jd_code) -> GoldenMetricsReport`; route `GET /api/v1/golden-set/metrics?jd_code=`. Models `Confusion`, `MetricStats`, `JDMetrics`, `GoldenMetricsReport`.

- [ ] **Step 1: Write the failing unit test (metrics math)**

```python
# backend/tests/unit/test_golden_metrics.py
from backend.app.services.golden_set import metric_stats


def test_metric_stats_and_zero_denominator():
    s = metric_stats(3, 1, 4, 2)  # tp, fp, tn, fn
    assert s["confusion"] == {"tp": 3, "fp": 1, "tn": 4, "fn": 2}
    assert s["precision"] == 0.75  # 3/(3+1)
    assert s["recall"] == 0.6  # 3/(3+2)
    assert s["f1"] == 2 * 3 / (2 * 3 + 1 + 2)  # 0.666...
    assert s["accuracy"] == (3 + 4) / (3 + 1 + 4 + 2)
    empty = metric_stats(0, 0, 0, 0)
    assert empty["precision"] is None and empty["recall"] is None
    assert empty["f1"] is None and empty["accuracy"] is None
```

- [ ] **Step 2: Run it (fails)** — FAIL.

- [ ] **Step 3: Add report models to schemas**

Append to `backend/app/schemas/golden_set.py`:

```python
class Confusion(BaseModel):
    tp: int
    fp: int
    tn: int
    fn: int


class MetricStats(BaseModel):
    labeled_total: int
    scored: int
    uncovered: int
    borderline_excluded: int
    confusion: Confusion
    precision: float | None
    recall: float | None
    f1: float | None
    accuracy: float | None


class JDMetrics(MetricStats):
    jd_code: str


class GoldenMetricsReport(BaseModel):
    overall: MetricStats
    by_jd: list[JDMetrics]
```

- [ ] **Step 4: Add `metric_stats` + `golden_metrics` to the service**

In `backend/app/services/golden_set.py`, update the existing imports (do NOT add duplicate import lines): change `from sqlalchemy import func, select, tuple_` to `from sqlalchemy import and_, func, select, tuple_`; change `from backend.app.models import JD, Candidate, GoldenSet, User` to `from backend.app.models import JD, Candidate, GoldenSet, Score, User`; and add a new line `from backend.app.schemas.golden_set import GoldenMetricsReport, JDMetrics, MetricStats`. Then append the functions:

```python
def metric_stats(tp: int, fp: int, tn: int, fn: int) -> dict:
    def ratio(num: int, den: int) -> float | None:
        return (num / den) if den else None

    return {
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
        "accuracy": ratio(tp + tn, tp + tn + fp + fn),
    }


def _build_stats(counts: dict[str, int]) -> dict:
    tp, fp, tn, fn = counts["tp"], counts["fp"], counts["tn"], counts["fn"]
    scored = tp + fp + tn + fn + counts["borderline_excluded"]
    return {
        "labeled_total": counts["labeled_total"],
        "scored": scored,
        "uncovered": counts["labeled_total"] - scored,
        "borderline_excluded": counts["borderline_excluded"],
        **metric_stats(tp, fp, tn, fn),
    }


def _empty_counts() -> dict[str, int]:
    return {"labeled_total": 0, "borderline_excluded": 0, "tp": 0, "fp": 0, "tn": 0, "fn": 0}


async def golden_metrics(db: AsyncSession, jd_code: str | None) -> GoldenMetricsReport:
    latest = (
        select(
            Score.candidate_id,
            Score.jd_id,
            Score.grade,
            func.row_number()
            .over(
                partition_by=(Score.candidate_id, Score.jd_id),
                order_by=(Score.created_at.desc(), Score.id.desc()),
            )
            .label("rn"),
        )
        .subquery()
    )
    q = (
        select(JD.code, GoldenSet.label, latest.c.grade)
        .select_from(GoldenSet)
        .join(JD, JD.id == GoldenSet.jd_id)
        .outerjoin(
            latest,
            and_(
                latest.c.candidate_id == GoldenSet.candidate_id,
                latest.c.jd_id == GoldenSet.jd_id,
                latest.c.rn == 1,
            ),
        )
    )
    if jd_code is not None:
        q = q.where(JD.code == jd_code)
    rows = (await db.execute(q)).all()

    per_jd: dict[str, dict[str, int]] = {}
    overall = _empty_counts()
    for code, label, grade in rows:
        c = per_jd.setdefault(code, _empty_counts())
        for bucket in (c, overall):
            bucket["labeled_total"] += 1
        if grade is None:
            continue  # uncovered
        if label == "borderline":
            for bucket in (c, overall):
                bucket["borderline_excluded"] += 1
            continue
        ai_advance = grade != "rejected"
        if label == "advance":
            cell = "tp" if ai_advance else "fn"
        else:  # label == "reject"
            cell = "fp" if ai_advance else "tn"
        for bucket in (c, overall):
            bucket[cell] += 1

    by_jd = [JDMetrics(jd_code=code, **_build_stats(counts)) for code, counts in sorted(per_jd.items())]
    return GoldenMetricsReport(overall=MetricStats(**_build_stats(overall)), by_jd=by_jd)
```

- [ ] **Step 5: Add the metrics route**

Add to `backend/app/routers/golden_set.py`:

```python
from backend.app.schemas.golden_set import GoldenMetricsReport
from backend.app.services.golden_set import golden_metrics


@router.get("/golden-set/metrics", response_model=GoldenMetricsReport)
async def metrics(
    jd_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> GoldenMetricsReport:
    return await golden_metrics(db, jd_code)
```

- [ ] **Step 6: Extend the integration test**

Add to `backend/tests/integration/test_golden_set_api.py` (import `RuleVersion`, `Score` at the top: `from backend.app.models import JD, Candidate, RuleVersion, Score` alongside the existing imports):

```python
async def _seed_score(db, cand, jd, *, grade):
    # unique version per candidate avoids any (jd_id, version) collision when a
    # test seeds several scores against one JD.
    rv = RuleVersion(
        jd_id=jd.id, version=f"v{cand.id}", schema_json={}, published_at=datetime.now(timezone.utc)
    )
    db.add(rv)
    await db.flush()
    score = Score(
        candidate_id=cand.id, jd_id=jd.id, rule_version_id=rv.id, total_score=80,
        grade=grade, hard_filter_result={}, rule_dimensions={}, is_suspicious=False,
    )
    db.add(score)
    await db.flush()
    return score


async def test_metrics_confusion_and_exclusions(client, db_session, auth_headers):
    jd = await _seed_jd(db_session)
    # golden advance + AI advance (grade L4) -> TP
    tp = await _seed_candidate(db_session, pii_hash="c1")
    await _seed_score(db_session, tp, jd, grade="L4")
    # golden reject + AI advance (grade L4) -> FP
    fp = await _seed_candidate(db_session, pii_hash="c2")
    await _seed_score(db_session, fp, jd, grade="L4")
    # borderline -> excluded
    bd = await _seed_candidate(db_session, pii_hash="c3")
    await _seed_score(db_session, bd, jd, grade="rejected")
    # uncovered: golden label but no score
    unc = await _seed_candidate(db_session, pii_hash="c4")
    await db_session.commit()
    body = (
        f"candidate_id,jd_code,label\n{tp.id},FT,advance\n{fp.id},FT,reject\n"
        f"{bd.id},FT,borderline\n{unc.id},FT,advance\n"
    ).encode("utf-8")
    imp = await client.post(
        "/api/v1/golden-set/import",
        files={"file": ("g.csv", body, "text/csv")},
        headers=await auth_headers("admin"),
    )
    assert imp.status_code == 200 and imp.json()["created"] == 4
    rep = await client.get("/api/v1/golden-set/metrics", headers=await auth_headers("hr"))
    assert rep.status_code == 200
    overall = rep.json()["overall"]
    assert overall["confusion"] == {"tp": 1, "fp": 1, "tn": 0, "fn": 0}
    assert overall["labeled_total"] == 4 and overall["scored"] == 3
    assert overall["uncovered"] == 1 and overall["borderline_excluded"] == 1
    assert overall["precision"] == 0.5  # 1/(1+1)
    assert "name_cipher" not in rep.text and "张三" not in rep.text
```

- [ ] **Step 7: Run tests, ruff, mypy, commit**

```bash
uv run pytest backend/tests/unit/test_golden_metrics.py -q
# integration with env prefix
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/golden_set.py backend/app/routers/golden_set.py backend/app/schemas/golden_set.py backend/tests/unit/test_golden_metrics.py backend/tests/integration/test_golden_set_api.py
git commit -m "feat(wp6b): baseline metrics endpoint (confusion matrix, precision/recall/f1)"
```

---

## Task 5: Frontend — golden-set import + list page

**Files:**
- Modify: `frontend/src/lib/schemas.ts`, `frontend/src/components/app-shell.tsx`
- Create: `frontend/src/app/(app)/golden-set/page.tsx`, `frontend/src/components/golden-set-view.tsx`, `frontend/src/app/api/golden-set/import/route.ts`
- Test: `frontend/src/components/golden-set-view.test.tsx`

**Interfaces:**
- Produces zod: `GoldenImportResult`, `GoldenSetList`; component `<GoldenSetView canImport={boolean} />`; BFF `POST /api/golden-set/import`.

- [ ] **Step 1: Add zod schemas**

Append to `frontend/src/lib/schemas.ts`:

```ts
export const GoldenImportResult = z.object({
  total: z.number(),
  created: z.number(),
  updated: z.number(),
  errors: z.array(
    z.object({
      row: z.number(),
      candidate_id: z.number().nullable(),
      jd_code: z.string().nullable(),
      reason: z.string(),
    }),
  ),
});
export const GoldenSetList = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      candidate_id: z.number(),
      jd_code: z.string(),
      label: z.string(),
      imported_at: z.string(),
      imported_by_display_name: z.string(),
    }),
  ),
  page: z.number(),
  page_size: z.number(),
  total: z.number(),
});
```

- [ ] **Step 2: Add the BFF import route**

```ts
// frontend/src/app/api/golden-set/import/route.ts
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { proxyJson } from "@/lib/server/api";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { sessionExpiredResponse } from "@/lib/server/proxy-response";

export async function POST(req: Request): Promise<NextResponse> {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session)
    return NextResponse.json({ code: "unauthorized", message: "会话已失效" }, { status: 401 });
  const form = await req.formData();
  const res = await proxyJson("/api/v1/golden-set/import", {
    method: "POST",
    token: session.token,
    body: form,
  });
  if (res.status === 401) return sessionExpiredResponse();
  return NextResponse.json(res.body, { status: res.status });
}
```

- [ ] **Step 3: Write the failing component test**

```tsx
// frontend/src/components/golden-set-view.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GoldenSetView } from "@/components/golden-set-view";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("GoldenSetView", () => {
  it("hides the import control when canImport is false", async () => {
    global.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
          status: 200,
        }),
    ) as unknown as typeof fetch;
    render(wrap(<GoldenSetView canImport={false} />));
    expect(await screen.findByText(/黄金集/)).toBeInTheDocument();
    expect(screen.queryByLabelText("导入 CSV")).not.toBeInTheDocument();
  });

  it("shows the import control when canImport is true", async () => {
    global.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
          status: 200,
        }),
    ) as unknown as typeof fetch;
    render(wrap(<GoldenSetView canImport={true} />));
    expect(await screen.findByLabelText("导入 CSV")).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run it (fails)** — FAIL.

- [ ] **Step 5: Implement `<GoldenSetView>`**

```tsx
// frontend/src/components/golden-set-view.tsx
"use client";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { z } from "zod";
import { apiGet, ApiError } from "@/lib/api-client";
import { GoldenImportResult, GoldenSetList } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { DataState } from "@/components/data-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

async function importCsv(file: File): Promise<z.infer<typeof GoldenImportResult>> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/golden-set/import", { method: "POST", body: form });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = body as { code?: string; message?: string };
    throw new ApiError(e.code ?? `http_${res.status}`, e.message ?? "导入失败", res.status);
  }
  return GoldenImportResult.parse(body);
}

export function GoldenSetView({ canImport }: { canImport: boolean }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<z.infer<typeof GoldenImportResult> | null>(null);
  const list = useQuery({
    queryKey: ["golden-set"],
    queryFn: () => apiGet("/api/v1/golden-set", {}, GoldenSetList),
  });

  const mutation = useMutation({
    mutationFn: importCsv,
    onSuccess: (r) => {
      setResult(r);
      toast.success(`导入完成：新增 ${r.created} · 更新 ${r.updated} · 错误 ${r.errors.length}`);
      void qc.invalidateQueries({ queryKey: ["golden-set"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "导入失败"),
  });

  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">黄金集</h1>
      {canImport ? (
        <div className="space-y-2 rounded-md border p-4">
          <label htmlFor="golden-csv" className="text-sm font-medium">
            导入 CSV
          </label>
          <p className="text-muted-foreground text-sm">列：candidate_id, jd_code, label</p>
          <input
            id="golden-csv"
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            aria-label="导入 CSV"
            className="block text-sm"
          />
          <Button
            size="sm"
            disabled={mutation.isPending}
            onClick={() => {
              const f = fileRef.current?.files?.[0];
              if (!f) {
                toast.error("请先选择 CSV 文件");
                return;
              }
              mutation.mutate(f);
            }}
          >
            {mutation.isPending ? "导入中…" : "导入"}
          </Button>
          {result ? (
            <p className="text-sm">
              新增 {result.created} · 更新 {result.updated} · 错误 {result.errors.length}
              {result.errors.length > 0
                ? `（第 ${result.errors.map((e) => e.row).join("、")} 行）`
                : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      <DataState
        isLoading={list.isLoading}
        error={list.error ? { message: (list.error as Error).message } : null}
        isEmpty={list.data?.items.length === 0}
        emptyText="暂无黄金集条目"
        onRetry={() => list.refetch()}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>候选人</TableHead>
              <TableHead>JD</TableHead>
              <TableHead>标签</TableHead>
              <TableHead>导入人</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.items.map((g) => (
              <TableRow key={g.id}>
                <TableCell>{g.candidate_id}</TableCell>
                <TableCell>{g.jd_code}</TableCell>
                <TableCell>{g.label}</TableCell>
                <TableCell>{g.imported_by_display_name}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataState>
    </section>
  );
}
```

- [ ] **Step 6: Implement the server page (reads role)**

```tsx
// frontend/src/app/(app)/golden-set/page.tsx
import { cookies } from "next/headers";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { GoldenSetView } from "@/components/golden-set-view";

export const dynamic = "force-dynamic";

export default async function GoldenSetPage() {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  const canImport = session?.role === "hr_lead" || session?.role === "admin";
  return <GoldenSetView canImport={canImport} />;
}
```

- [ ] **Step 7: Add the nav link**

In `frontend/src/components/app-shell.tsx`, add inside the `<nav>` (after the 复核报告 link):

```tsx
<Link href="/golden-set" className="text-muted-foreground hover:text-foreground">
  黄金集
</Link>
```

- [ ] **Step 8: Run tests + gates + commit**

```bash
cd frontend && npm run test && npm run typecheck && npm run lint
git add frontend/src
git commit -m "feat(wp6b): golden-set import + list page (role-gated import)"
```

---

## Task 6: Frontend — baseline metrics report page + nav

**Files:**
- Create: `frontend/src/app/(app)/reports/baseline/page.tsx`
- Modify: `frontend/src/lib/schemas.ts`, `frontend/src/components/app-shell.tsx`
- Test: `frontend/src/app/(app)/reports/baseline/report.test.tsx`

**Interfaces:**
- Produces zod: `GoldenMetricsReport`; the report page at `/reports/baseline`.

- [ ] **Step 1: Add the zod schema**

Append to `frontend/src/lib/schemas.ts`:

```ts
const MetricStats = z.object({
  labeled_total: z.number(),
  scored: z.number(),
  uncovered: z.number(),
  borderline_excluded: z.number(),
  confusion: z.object({ tp: z.number(), fp: z.number(), tn: z.number(), fn: z.number() }),
  precision: z.number().nullable(),
  recall: z.number().nullable(),
  f1: z.number().nullable(),
  accuracy: z.number().nullable(),
});
export const GoldenMetricsReport = z.object({
  overall: MetricStats,
  by_jd: z.array(MetricStats.extend({ jd_code: z.string() })),
});
```

- [ ] **Step 2: Write the failing report-page test**

```tsx
// frontend/src/app/(app)/reports/baseline/report.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReportPage from "./page";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

const STATS = {
  labeled_total: 4,
  scored: 3,
  uncovered: 1,
  borderline_excluded: 1,
  confusion: { tp: 1, fp: 1, tn: 0, fn: 0 },
  precision: 0.5,
  recall: 1,
  f1: 0.6667,
  accuracy: 0.5,
};

describe("Baseline metrics report page", () => {
  it("renders the overall precision", async () => {
    global.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ overall: STATS, by_jd: [{ ...STATS, jd_code: "FT" }] }),
          { status: 200 },
        ),
    ) as unknown as typeof fetch;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ReportPage />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("50%", { selector: "span" })).toBeInTheDocument());
  });
});
```

- [ ] **Step 3: Run it (fails)** — FAIL.

- [ ] **Step 4: Implement the report page**

```tsx
// frontend/src/app/(app)/reports/baseline/page.tsx
"use client";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { GoldenMetricsReport } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

export default function BaselineReportPage() {
  const query = useQuery({
    queryKey: ["baseline-metrics"],
    queryFn: () => apiGet("/api/v1/golden-set/metrics", {}, GoldenMetricsReport),
  });
  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">基线指标</h1>
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        onRetry={() => query.refetch()}
      >
        {query.data ? (
          <div className="space-y-6">
            <div className="rounded-md border p-4">
              <p>
                精确率 <span className="text-2xl font-semibold">{pct(query.data.overall.precision)}</span>
                {"　"}召回率 <span className="text-2xl font-semibold">{pct(query.data.overall.recall)}</span>
                {"　"}F1 <span className="text-2xl font-semibold">{pct(query.data.overall.f1)}</span>
              </p>
              <p className="text-muted-foreground text-sm">
                标注 {query.data.overall.labeled_total} · 已评分 {query.data.overall.scored} · 未覆盖{" "}
                {query.data.overall.uncovered} · borderline 排除 {query.data.overall.borderline_excluded} ·
                混淆 TP{query.data.overall.confusion.tp}/FP{query.data.overall.confusion.fp}/TN
                {query.data.overall.confusion.tn}/FN{query.data.overall.confusion.fn}
              </p>
            </div>
            <div>
              <h2 className="mb-2 font-medium">按 JD</h2>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>JD</TableHead>
                    <TableHead>精确率</TableHead>
                    <TableHead>召回率</TableHead>
                    <TableHead>F1</TableHead>
                    <TableHead>TP/FP/TN/FN</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data.by_jd.map((j) => (
                    <TableRow key={j.jd_code}>
                      <TableCell>{j.jd_code}</TableCell>
                      <TableCell>{pct(j.precision)}</TableCell>
                      <TableCell>{pct(j.recall)}</TableCell>
                      <TableCell>{pct(j.f1)}</TableCell>
                      <TableCell>
                        {j.confusion.tp}/{j.confusion.fp}/{j.confusion.tn}/{j.confusion.fn}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        ) : null}
      </DataState>
    </section>
  );
}
```

- [ ] **Step 5: Add the nav link**

In `frontend/src/components/app-shell.tsx`, add inside the `<nav>` (after the 黄金集 link):

```tsx
<Link href="/reports/baseline" className="text-muted-foreground hover:text-foreground">
  基线指标
</Link>
```

- [ ] **Step 6: Run tests + gates + commit**

```bash
cd frontend && npm run test && npm run typecheck && npm run lint && npm run build
git add frontend/src
git commit -m "feat(wp6b): baseline metrics report page and nav"
```

---

## Task 7: E2E, docs, full gate, push, CI, and WP6b exit review

**Files:**
- Create: `frontend/e2e/golden-set.spec.ts`
- Modify: `README.md`, roadmap + plan index.

- [ ] **Step 1: Write the e2e (stubbed BFF)**

```ts
// frontend/e2e/golden-set.spec.ts
import { test, expect } from "@playwright/test";
import { mintSession } from "./helpers/session";

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试Lead", role: "hr_lead" }),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.route("**/api/proxy/api/v1/golden-set/metrics**", (r) =>
    r.fulfill({
      status: 200,
      json: {
        overall: {
          labeled_total: 4,
          scored: 3,
          uncovered: 1,
          borderline_excluded: 1,
          confusion: { tp: 1, fp: 1, tn: 0, fn: 0 },
          precision: 0.5,
          recall: 1,
          f1: 0.6667,
          accuracy: 0.5,
        },
        by_jd: [
          {
            jd_code: "FT",
            labeled_total: 4,
            scored: 3,
            uncovered: 1,
            borderline_excluded: 1,
            confusion: { tp: 1, fp: 1, tn: 0, fn: 0 },
            precision: 0.5,
            recall: 1,
            f1: 0.6667,
            accuracy: 0.5,
          },
        ],
      },
    }),
  );
});

test("baseline report shows the overall precision", async ({ page }) => {
  await page.goto("/reports/baseline");
  await expect(page.getByRole("heading", { name: "基线指标" })).toBeVisible();
  await expect(page.locator("span.text-2xl").first()).toHaveText("50%");
});
```

- [ ] **Step 2: Run the full frontend gate**

```bash
cd frontend
npm run lint && npm run typecheck && npm run test && npm run e2e && npm run build
```

- [ ] **Step 3: Run the full backend gate**

```bash
uv run pytest -m "not integration and not external_contract" -q
DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000" uv run pytest -m integration -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

- [ ] **Step 4: README + roadmap**

Document golden-set import + baseline metrics in `README.md`. In the roadmap authority (`docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md` §WP6) and the plan index (`docs/superpowers/plans/README.md` row 6), mark WP6b **In progress** (do NOT mark Complete or WP6c Ready until hosted CI passes).

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e README.md docs/
git commit -m "test(wp6b): golden-set e2e and docs"
```

- [ ] **Step 6: Push + PR + hosted CI**

```bash
git push -u origin codex/wp6b-golden-set
gh pr create --base main --title "WP6b: golden set and baseline metrics" --body "<summary + exit evidence>"
```

Require hosted `verify.yml` (backend `unit-and-static` 3.10/3.14 + `integration`) green — WP6b adds a migration + backend routes, so the backend CI genuinely exercises the change.

- [ ] **Step 7: Record evidence + mark WP6b Complete / WP6c Ready** only after every backend gate + hosted CI + the frontend local gate pass.

---

## Self-Review

**Spec coverage:** §5 data model + migration → Task 1. §6 golden vocabulary + AI mapping + latest-score → Tasks 1 (CHECK) / 2 (parse/import) / 4 (metrics). §7.1 import → Task 3. §7.2 list → Task 3. §7.3 metrics → Task 4. §8 architecture (service/router/schemas; frontend pages + BFF) → Tasks 2–6. §9 auth/leak (import hr_lead/admin, read hr+, no-PII assertions) → Tasks 3/4 (integration `name_cipher` absence + 403/401 matrix). §10 config `GOLDEN_IMPORT_MAX_ROWS` → Task 1. §11 tests → Tasks 1–7 (unit parse + metrics math, integration import/upsert/errors/auth/metrics/Alembic, frontend component + report + e2e). §12 rollout / §13 exit → Task 7.

**Placeholder scan:** the only placeholder is the migration revision id `<rev>` (generated by `alembic revision` in Task 1 Step 3, referenced in Step 4) — intrinsic to Alembic, resolved in-task. All other steps contain real code.

**Type consistency:** service names (`parse_golden_csv`, `import_golden_set`, `list_golden_set`, `metric_stats`, `golden_metrics`) and dataclasses (`ParsedRow`, `RowError`) are defined once (Task 2/4) and consumed consistently by the router (Task 3/4). Response models (`GoldenImportResult`, `GoldenSetItem`, `GoldenSetList`, `MetricStats`, `JDMetrics`, `GoldenMetricsReport`, `Confusion`) are defined in `schemas/golden_set.py` and used by both service (metrics builders) and router. Frontend zod (`GoldenImportResult`, `GoldenSetList`, `GoldenMetricsReport`) match the backend field names/nullability. `_build_stats` returns exactly the `MetricStats` field set (labeled_total, scored, uncovered, borderline_excluded, confusion, precision, recall, f1, accuracy). The metrics route path `/golden-set/metrics` is registered after `/golden-set` — FastAPI matches the more specific static path regardless of order, and both are distinct paths so there is no shadowing.
