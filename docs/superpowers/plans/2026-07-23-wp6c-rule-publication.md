# WP6c Rule Publication, What-If, and Regression Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a curator create a draft rule version (POST a validated `schema_json`), run a deterministic What-If that re-scores the JD's golden set with the draft's rules and compares it to the active version's baseline, and publish the draft under a gate that blocks publication until regression metrics are recorded — on publish the draft becomes the JD's active version and the predecessor is archived.

**Architecture:** Backend rule-publication router + service over the existing `RuleVersion` model (plus a migration adding a `status` column + CHECK, a `(jd_id, version)` unique constraint, and making `published_at` nullable). The What-If re-score composes the existing deterministic scoring functions (`run_hard_filters`, `score_dimensions`, `_grade_from`) and reuses each candidate's stored LLM-judge subtotal (`stored total − stored rule subtotal`); metrics reuse WP6b's `metric_stats`. Frontend extends WP5 with a per-JD rule-management page. No scoring-algorithm change; a JD keeps its active version until a publish moves it.

**Tech Stack:** Python 3.10–3.14, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, PostgreSQL; Next.js 15, TanStack Query, zod, Vitest, Playwright.

## Global Constraints

- Backend default CI runs `pytest -m "not integration and not external_contract"`; offline & deterministic. Integration on this host uses the env prefix `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000" uv run pytest ...` (test stack: PG 25432, MinIO 9000, Redis 56379).
- Write routes (create draft, evaluate, publish) require role in `("hr_lead","admin")`; reads require `("hr","hr_lead","admin")` via `require_roles(*roles)` (`backend/app/deps.py`, which RETURNS the `User`). Errors are `{code, message}` (FastAPI wraps as `{"detail": {code, message}}`); offset pagination is `{items, page, page_size, total}`.
- A `RuleVersion.status` is `draft` | `published` | `archived` (DB CHECK). A `draft` has `published_at = NULL` and `golden_set_metrics = NULL` until evaluated; scoring uses `jd.active_rule_version_id` (a `published` version). Version strings are unique per JD (`uq_rule_versions_jd_version`).
- What-If is deterministic and offline — NO LLM. For a golden candidate it re-runs `run_hard_filters` + `score_dimensions` over the candidate's stored `extracted_json` and reuses the judge subtotal as `stored total_score − stored rule_dimensions["subtotal"]`. A candidate whose stored score was hard-filter-rejected (`judge_dimensions is None`) and whom the draft does NOT hard-reject is `indeterminate` (excluded). If the draft's `judge_dimensions` differ from the active version's, the response carries `judge_dimensions_changed = true`.
- AI prediction from a grade: AI reject ⟺ `grade == "rejected"`, else advance (same as WP6a/WP6b). `borderline` golden labels and golden entries with no score are excluded from the confusion matrix.
- Publish is gated: it requires the draft's `golden_set_metrics` to be recorded (else `409 regression_not_recorded`). No auto-threshold — a human judges the comparison.
- No candidate PII, ciphertext, or object keys in any evaluate/metrics response — only aggregate numbers (and, at most, `candidate_id`/`jd_code` references).
- Alembic head before WP6c is `f412481450cf` (WP6b). Bump BOTH the expected head in `backend/tests/integration/test_db_migrations.py` AND `HEAD_REVISION` in `scripts/verify.py` to the new revision. Run `uv run ruff check backend` and `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports` before each backend commit. Frontend gates (`cd frontend`): `npm run lint && npm run typecheck && npm run test`.
- Base UI (not Radix) in the frontend: use `<Button render={<a/>}>` not `asChild`. `useSearchParams` needs a `<Suspense>` boundary. Exclude `.superpowers/`, `backend.zip`. End every commit message with a blank line then `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

**Backend — Create:**
- `migrations/versions/<rev>_wp6c_rule_version_status.py` — status column + CHECK + uq + published_at nullable.
- `backend/app/services/rule_publication.py` — `whatif_grade`, `evaluate_draft`, `create_draft`, `publish_draft`.
- `backend/app/schemas/rule_publication.py` — request/response models.
- `backend/app/routers/rule_publication.py` — POST create / evaluate / publish.
- Tests: `backend/tests/unit/test_whatif.py`, `backend/tests/integration/test_rule_publication.py`.

**Backend — Modify:**
- `backend/app/models/rule_version.py` — add `status`, make `published_at` nullable, add `__table_args__`.
- `backend/app/schemas/read.py` — `RuleVersionItem`: add `status`, make `published_at` optional.
- `backend/app/services/read/jds.py` — `list_rule_versions` populates `status`.
- `backend/app/main.py` — register the rule-publication router.
- `backend/tests/integration/test_db_migrations.py` + `scripts/verify.py` — bump head to `<rev>`.

**Frontend — Create (`frontend/`):**
- `src/app/(app)/jds/[code]/rules/page.tsx` (server: reads role), `src/components/rule-management-view.tsx` (client).
- Tests: `src/components/rule-management-view.test.tsx`, `e2e/rule-publication.spec.ts`.

**Frontend — Modify:**
- `src/lib/schemas.ts` — rule-version + evaluate zod schemas.
- `src/components/app-shell.tsx` — nav link (a rules entry point).

---

## Task 1: RuleVersion status + uq + nullable published_at migration

**Files:**
- Modify: `backend/app/models/rule_version.py`, `backend/tests/integration/test_db_migrations.py`, `scripts/verify.py`
- Create: `migrations/versions/<rev>_wp6c_rule_version_status.py`
- Test: the existing `backend/tests/integration/test_db_migrations.py` (upgrade-to-head).

**Interfaces:**
- Produces: `rule_versions.status` (draft/published/archived, CHECK `ck_rule_versions_status`), `uq_rule_versions_jd_version (jd_id, version)`, nullable `published_at`; new Alembic head `<rev>`.

- [ ] **Step 1: Update the model**

```python
# backend/app/models/rule_version.py  (full file)
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    __table_args__ = (
        UniqueConstraint("jd_id", "version", name="uq_rule_versions_jd_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_rule_versions_status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="published")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    golden_set_metrics: Mapped[dict | None] = mapped_column(JSONB)
```

- [ ] **Step 2: Generate the migration**

Run: `uv run alembic revision -m "wp6c rule version status"` (NOT `--autogenerate`) — note the generated revision id `<rev>`. Confirm `down_revision = "f412481450cf"`. Replace `upgrade`/`downgrade`:

```python
def upgrade() -> None:
    op.add_column(
        "rule_versions",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="published"),
    )
    op.create_check_constraint(
        "ck_rule_versions_status", "rule_versions", "status IN ('draft', 'published', 'archived')"
    )
    op.create_unique_constraint(
        "uq_rule_versions_jd_version", "rule_versions", ["jd_id", "version"]
    )
    op.alter_column(
        "rule_versions", "published_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )


def downgrade() -> None:
    # Draft rows have no meaningful published_at; drop them before restoring NOT NULL.
    op.execute("DELETE FROM rule_versions WHERE status = 'draft'")
    op.alter_column(
        "rule_versions", "published_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.drop_constraint("uq_rule_versions_jd_version", "rule_versions", type_="unique")
    op.drop_constraint("ck_rule_versions_status", "rule_versions", type_="check")
    op.drop_column("rule_versions", "status")
```

(`import sqlalchemy as sa` IS used here — keep it.)

- [ ] **Step 3: Bump the expected head in the migration test and verify.py**

In `backend/tests/integration/test_db_migrations.py`, replace the expected-head literal `f412481450cf` with `<rev>`. In `scripts/verify.py`, replace `HEAD_REVISION = "f412481450cf"` with `HEAD_REVISION = "<rev>"`.

- [ ] **Step 4: Run the migration test (integration)**

Run: `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000" uv run pytest backend/tests/integration/test_db_migrations.py -q`
Expected: PASS (upgrades to `<rev>`, round-trips).

- [ ] **Step 5: Offline + ruff + mypy + commit**

```bash
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/models/rule_version.py migrations/versions/ backend/tests/integration/test_db_migrations.py scripts/verify.py
git commit -m "feat(wp6c): rule_versions status + version uniqueness + nullable published_at"
```

---

## Task 2: What-If re-score service (offline core)

**Files:**
- Create: `backend/app/services/rule_publication.py`
- Test: `backend/tests/unit/test_whatif.py`

**Interfaces:**
- Produces: `whatif_grade(schema: RuleSchema, extracted: dict, *, stored_rule_subtotal: float, stored_total: float, stored_hard_rejected: bool) -> str | None`; `bucket(label: str, grade: str) -> str` (returns `"tp"|"fp"|"tn"|"fn"`). Consumed by Task 4's `evaluate_draft`.

- [ ] **Step 1: Write the failing unit test**

```python
# backend/tests/unit/test_whatif.py
from backend.app.rules.schema import RuleSchema
from backend.app.services.rule_publication import bucket, whatif_grade

_SCHEMA = {
    "version": "v2",
    "jd_code": "FT",
    "total_score": 10.0,
    "passing_threshold": 6.0,
    "hard_filters": [
        {"id": "age", "rule": "age <= 40", "action": "reject", "audit_tag": "age"}
    ],
    "rule_dimensions": [
        {
            "id": "exp",
            "name": "experience",
            "weight": 4.0,
            "method": "experience_years",
            "tiers": [
                {"label": "high", "score": 4.0, "min_years": 3.0},
                {"label": "low", "score": 1.0, "min_years": 0.0},
            ],
        }
    ],
    "judge_dimensions": [
        {
            "id": "fit",
            "name": "fit",
            "weight": 6.0,
            "prompt_hint": "fit",
            "tiers": [{"label": "high", "score": 6.0}],
        }
    ],
    "grade_thresholds": [
        {"grade": "L1", "min": 6.0, "label": "pass"},
    ],
}


def _schema() -> RuleSchema:
    return RuleSchema.model_validate(_SCHEMA)


def test_hard_reject_grade():
    # age 50 > 40 -> hard reject regardless of the stored subtotal
    assert whatif_grade(
        _schema(), {"age": 50, "years_experience": 5}, stored_rule_subtotal=0, stored_total=0,
        stored_hard_rejected=False,
    ) == "rejected"


def test_deterministic_rescore_reuses_judge_subtotal():
    # stored total 9, stored rule subtotal 3 -> reused judge subtotal = 6.
    # draft rule engine gives exp high (4.0); 4.0 + 6.0 = 10 >= 6 -> "L1".
    grade = whatif_grade(
        _schema(), {"age": 30, "years_experience": 5}, stored_rule_subtotal=3.0, stored_total=9.0,
        stored_hard_rejected=False,
    )
    assert grade == "L1"


def test_indeterminate_when_stored_hard_rejected_and_draft_does_not_reject():
    assert whatif_grade(
        _schema(), {"age": 30, "years_experience": 5}, stored_rule_subtotal=0, stored_total=0,
        stored_hard_rejected=True,
    ) is None


def test_bucket_quadrants():
    assert bucket("advance", "L1") == "tp"
    assert bucket("advance", "rejected") == "fn"
    assert bucket("reject", "L1") == "fp"
    assert bucket("reject", "rejected") == "tn"
```

- [ ] **Step 2: Run it (fails)** — `uv run pytest backend/tests/unit/test_whatif.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement the What-If core**

```python
# backend/app/services/rule_publication.py
from __future__ import annotations

from backend.app.rules.schema import RuleSchema
from backend.app.scoring.hard_filter import run_hard_filters
from backend.app.scoring.pipeline import _grade_from
from backend.app.scoring.rule_engine import score_dimensions


def whatif_grade(
    schema: RuleSchema,
    extracted: dict,
    *,
    stored_rule_subtotal: float,
    stored_total: float,
    stored_hard_rejected: bool,
) -> str | None:
    """Re-score one golden candidate with a draft schema, reusing the stored
    LLM-judge subtotal. Returns the hypothetical grade, or None (indeterminate)
    when the stored score was hard-filter-rejected (no judge subtotal exists to
    reuse) and the draft does not hard-reject."""
    hf = run_hard_filters(candidate=extracted, filters=schema.hard_filters)
    if hf.rejected:
        return "rejected"
    if stored_hard_rejected:
        return None
    rule_results = score_dimensions(extracted, schema.rule_dimensions)
    rule_total = sum((r.get("score") or 0) for r in rule_results)
    judge_total = stored_total - stored_rule_subtotal
    return _grade_from(rule_total + judge_total, schema)


def bucket(label: str, grade: str) -> str:
    """Confusion-matrix cell for one golden entry (advance = positive)."""
    ai_advance = grade != "rejected"
    if label == "advance":
        return "tp" if ai_advance else "fn"
    return "fp" if ai_advance else "tn"
```

- [ ] **Step 4: Run tests (pass)** — `uv run pytest backend/tests/unit/test_whatif.py -q` → PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/rule_publication.py backend/tests/unit/test_whatif.py
git commit -m "feat(wp6c): what-if re-score core (deterministic grade + confusion bucket)"
```

---

## Task 3: Schemas + create/publish service + routes + list status

**Files:**
- Create: `backend/app/schemas/rule_publication.py`
- Modify: `backend/app/services/rule_publication.py`, `backend/app/routers/rule_publication.py` (create), `backend/app/main.py`, `backend/app/schemas/read.py`, `backend/app/services/read/jds.py`
- Test: `backend/tests/integration/test_rule_publication.py`

**Interfaces:**
- Consumes: Task 2 service, `require_roles`, `get_db`.
- Produces: `async create_draft(db, *, jd, schema_json, notes) -> RuleVersion` (raises `VersionExists`, `InvalidRuleSchema`); `async publish_draft(db, *, jd, draft) -> RuleVersion` (raises `RegressionNotRecorded`, `NotADraft`); routes `POST /api/v1/jds/{code}/rule-versions`, `POST /api/v1/jds/{code}/rule-versions/{version}/publish`. Schemas `CreateDraftRequest`, `RuleVersionRef`. `RuleVersionItem` gains `status`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_rule_publication.py
from datetime import datetime, timezone

import pytest

from backend.app.models import JD, RuleVersion

pytestmark = pytest.mark.integration

# A minimal valid rule schema (weights sum to total_score).
def _schema(version: str) -> dict:
    return {
        "version": version,
        "jd_code": "FT",
        "total_score": 10.0,
        "passing_threshold": 6.0,
        "hard_filters": [],
        "rule_dimensions": [
            {"id": "exp", "name": "experience", "weight": 4.0, "method": "experience_years",
             "tiers": [{"label": "high", "score": 4.0, "min_years": 0.0}]}
        ],
        "judge_dimensions": [
            {"id": "fit", "name": "fit", "weight": 6.0, "prompt_hint": "fit",
             "tiers": [{"label": "high", "score": 6.0}]}
        ],
        "grade_thresholds": [{"grade": "L1", "min": 6.0, "label": "pass"}],
    }


async def _seed_jd_with_active(db) -> JD:
    jd = JD(code="FT", name="Foreign Trade", description="", status="active")
    db.add(jd)
    await db.flush()
    rv = RuleVersion(jd_id=jd.id, version="v1", schema_json=_schema("v1"), status="published",
                     published_at=datetime.now(timezone.utc))
    db.add(rv)
    await db.flush()
    jd.active_rule_version_id = rv.id
    await db.commit()
    return jd


async def test_create_draft_validates_and_dedupes(client, db_session, auth_headers):
    await _seed_jd_with_active(db_session)
    base = "/api/v1/jds/FT/rule-versions"
    ok = await client.post(base, json={"schema_json": _schema("v2")}, headers=await auth_headers("hr_lead"))
    assert ok.status_code == 200 and ok.json()["status"] == "draft"
    dup = await client.post(base, json={"schema_json": _schema("v2")}, headers=await auth_headers("admin"))
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "version_exists"
    bad = await client.post(base, json={"schema_json": {"version": "v3"}}, headers=await auth_headers("admin"))
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_rule_schema"
    # plain hr may not create; no token -> 401
    forbidden = await client.post(base, json={"schema_json": _schema("v4")}, headers=await auth_headers("hr"))
    assert forbidden.status_code == 403
    noauth = await client.post(base, json={"schema_json": _schema("v5")})
    assert noauth.status_code == 401


async def test_publish_requires_recorded_metrics(client, db_session, auth_headers):
    jd = await _seed_jd_with_active(db_session)
    await client.post("/api/v1/jds/FT/rule-versions", json={"schema_json": _schema("v2")},
                     headers=await auth_headers("hr_lead"))
    pub = "/api/v1/jds/FT/rule-versions/v2/publish"
    early = await client.post(pub, headers=await auth_headers("hr_lead"))
    assert early.status_code == 409 and early.json()["detail"]["code"] == "regression_not_recorded"


async def test_list_includes_status(client, db_session, auth_headers):
    await _seed_jd_with_active(db_session)
    await client.post("/api/v1/jds/FT/rule-versions", json={"schema_json": _schema("v2")},
                     headers=await auth_headers("hr_lead"))
    lst = await client.get("/api/v1/jds/FT/rule-versions", headers=await auth_headers("hr"))
    assert lst.status_code == 200
    statuses = {i["version"]: i["status"] for i in lst.json()["items"]}
    assert statuses == {"v1": "published", "v2": "draft"}
```

- [ ] **Step 2: Run it (fails)** — routes missing.

- [ ] **Step 3: Add `status` to the read schema + service**

In `backend/app/schemas/read.py`, change `RuleVersionItem` to make `published_at` optional and add `status`:

```python
class RuleVersionItem(BaseModel):
    id: int
    version: str
    status: str
    published_at: datetime | None
    published_by_user_id: int | None
    notes: str | None
    golden_set_metrics: dict | None
    is_active: bool
```

In `backend/app/services/read/jds.py`, add `status=v.status,` to the `RuleVersionItem(...)` construction in `list_rule_versions`.

- [ ] **Step 4: Implement schemas**

```python
# backend/app/schemas/rule_publication.py
from __future__ import annotations

from pydantic import BaseModel


class CreateDraftRequest(BaseModel):
    schema_json: dict
    notes: str | None = None


class RuleVersionRef(BaseModel):
    id: int
    version: str
    status: str
    notes: str | None
```

- [ ] **Step 5: Add create/publish to the service**

Append to `backend/app/services/rule_publication.py` (add the imports shown at the top of the file):

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from backend.app.models import JD, RuleVersion


class InvalidRuleSchema(Exception):
    pass


class VersionExists(Exception):
    pass


class NotADraft(Exception):
    pass


class RegressionNotRecorded(Exception):
    pass


async def create_draft(
    db: AsyncSession, *, jd: JD, schema_json: dict, notes: str | None
) -> RuleVersion:
    try:
        schema = RuleSchema.model_validate(schema_json)
    except ValidationError as exc:
        raise InvalidRuleSchema() from exc
    existing = (
        await db.execute(
            select(RuleVersion.id).where(
                RuleVersion.jd_id == jd.id, RuleVersion.version == schema.version
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise VersionExists()
    draft = RuleVersion(
        jd_id=jd.id, version=schema.version, schema_json=schema_json, status="draft",
        published_at=None, notes=notes,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def publish_draft(db: AsyncSession, *, jd: JD, draft: RuleVersion) -> RuleVersion:
    if draft.status != "draft":
        raise NotADraft()
    if draft.golden_set_metrics is None:
        raise RegressionNotRecorded()
    from datetime import datetime, timezone

    if jd.active_rule_version_id is not None:
        prev = (
            await db.execute(
                select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id)
            )
        ).scalar_one_or_none()
        if prev is not None:
            prev.status = "archived"
    draft.status = "published"
    draft.published_at = datetime.now(timezone.utc)
    jd.active_rule_version_id = draft.id
    await db.commit()
    await db.refresh(draft)
    return draft
```

(`publish_draft` does not set `published_by_user_id`; the router passes it — adjust the signature to accept `publisher_id` and set `draft.published_by_user_id = publisher_id` before commit.)

- [ ] **Step 6: Implement the router (create + publish)**

```python
# backend/app/routers/rule_publication.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import JD, RuleVersion, User
from backend.app.schemas.rule_publication import CreateDraftRequest, RuleVersionRef
from backend.app.services.rule_publication import (
    InvalidRuleSchema,
    NotADraft,
    RegressionNotRecorded,
    VersionExists,
    create_draft,
    publish_draft,
)

router = APIRouter(prefix="/api/v1/jds", tags=["rule-publication"])
WRITE_ROLES = ("hr_lead", "admin")


async def _load_jd(db: AsyncSession, code: str) -> JD:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "JD not found"})
    return jd


async def _load_version(db: AsyncSession, jd: JD, version: str) -> RuleVersion:
    rv = (
        await db.execute(
            select(RuleVersion).where(RuleVersion.jd_id == jd.id, RuleVersion.version == version)
        )
    ).scalar_one_or_none()
    if rv is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "rule version not found"}
        )
    return rv


@router.post("/{code}/rule-versions", response_model=RuleVersionRef)
async def create(
    code: str, payload: CreateDraftRequest,
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*WRITE_ROLES)),
) -> RuleVersionRef:
    jd = await _load_jd(db, code)
    try:
        draft = await create_draft(db, jd=jd, schema_json=payload.schema_json, notes=payload.notes)
    except InvalidRuleSchema as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_rule_schema", "message": "规则 schema 不合法"}
        ) from exc
    except VersionExists as exc:
        raise HTTPException(
            status_code=409, detail={"code": "version_exists", "message": "该版本号已存在"}
        ) from exc
    return RuleVersionRef(id=draft.id, version=draft.version, status=draft.status, notes=draft.notes)


@router.post("/{code}/rule-versions/{version}/publish", response_model=RuleVersionRef)
async def publish(
    code: str, version: str,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITE_ROLES)),
) -> RuleVersionRef:
    jd = await _load_jd(db, code)
    draft = await _load_version(db, jd, version)
    try:
        published = await publish_draft(db, jd=jd, draft=draft, publisher_id=user.id)
    except NotADraft as exc:
        raise HTTPException(
            status_code=409, detail={"code": "not_a_draft", "message": "只能发布草稿版本"}
        ) from exc
    except RegressionNotRecorded as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "regression_not_recorded", "message": "发布前必须先运行 What-If 评估"},
        ) from exc
    return RuleVersionRef(
        id=published.id, version=published.version, status=published.status, notes=published.notes
    )
```

Update `publish_draft`'s signature to `async def publish_draft(db, *, jd, draft, publisher_id)` and set `draft.published_by_user_id = publisher_id` before commit. Register in `backend/app/main.py`: `from backend.app.routers import rule_publication as rule_publication_router` and `app.include_router(rule_publication_router.router)` (append after the existing include_router calls).

- [ ] **Step 7: Run tests (pass)** — with the integration env prefix → PASS.

- [ ] **Step 8: Offline + ruff + mypy + commit**

```bash
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/schemas/rule_publication.py backend/app/routers/rule_publication.py backend/app/services/rule_publication.py backend/app/main.py backend/app/schemas/read.py backend/app/services/read/jds.py backend/tests/integration/test_rule_publication.py
git commit -m "feat(wp6c): create-draft + gated publish endpoints + rule-version status"
```

---

## Task 4: Evaluate (What-If / regression) endpoint

**Files:**
- Modify: `backend/app/services/rule_publication.py`, `backend/app/routers/rule_publication.py`, `backend/app/schemas/rule_publication.py`
- Test: extend `backend/tests/integration/test_rule_publication.py`

**Interfaces:**
- Produces: `async evaluate_draft(db, *, jd, draft) -> tuple[dict, bool]` (metrics dict, judge_dimensions_changed); route `POST /api/v1/jds/{code}/rule-versions/{version}/evaluate`. Schemas `RuleMetrics`, `EvaluateResponse`.

- [ ] **Step 1: Add response schemas**

Append to `backend/app/schemas/rule_publication.py`:

```python
from backend.app.schemas.golden_set import Confusion


class RuleMetrics(BaseModel):
    confusion: Confusion
    precision: float | None
    recall: float | None
    f1: float | None
    accuracy: float | None
    evaluated: int
    indeterminate: int
    borderline_excluded: int
    uncovered: int


class EvaluateResponse(BaseModel):
    draft: RuleMetrics
    baseline: RuleMetrics | None
    judge_dimensions_changed: bool
```

- [ ] **Step 2: Add `evaluate_draft` to the service**

Append to `backend/app/services/rule_publication.py` (add `and_`, `func` to the `from sqlalchemy import ...` line, and add the model + golden-set imports shown):

```python
from sqlalchemy import and_, func

from backend.app.models import Candidate, GoldenSet, Score
from backend.app.services.golden_set import golden_metrics, metric_stats


def _metrics_dict(tp: int, fp: int, tn: int, fn: int, *, indeterminate: int,
                  borderline_excluded: int, uncovered: int) -> dict:
    stats = metric_stats(tp, fp, tn, fn)  # {confusion, precision, recall, f1, accuracy}
    return {
        **stats,
        "evaluated": tp + fp + tn + fn,
        "indeterminate": indeterminate,
        "borderline_excluded": borderline_excluded,
        "uncovered": uncovered,
    }


async def evaluate_draft(db: AsyncSession, *, jd: JD, draft: RuleVersion) -> tuple[dict, bool]:
    if draft.status != "draft":
        raise NotADraft()
    schema = RuleSchema.model_validate(draft.schema_json)

    label_counts = dict(
        (
            await db.execute(
                select(GoldenSet.label, func.count())
                .where(GoldenSet.jd_id == jd.id)
                .group_by(GoldenSet.label)
            )
        ).all()
    )
    advance_reject_total = label_counts.get("advance", 0) + label_counts.get("reject", 0)
    borderline_excluded = label_counts.get("borderline", 0)

    latest = (
        select(
            Score.candidate_id,
            Score.total_score,
            Score.rule_dimensions,
            Score.judge_dimensions,
            func.row_number()
            .over(
                partition_by=(Score.candidate_id, Score.jd_id),
                order_by=(Score.created_at.desc(), Score.id.desc()),
            )
            .label("rn"),
        )
        .where(Score.jd_id == jd.id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(GoldenSet.label, Candidate.extracted_json, latest.c.total_score,
                   latest.c.rule_dimensions, latest.c.judge_dimensions)
            .select_from(GoldenSet)
            .join(Candidate, Candidate.id == GoldenSet.candidate_id)
            .join(latest, and_(latest.c.candidate_id == GoldenSet.candidate_id, latest.c.rn == 1))
            .where(GoldenSet.jd_id == jd.id, GoldenSet.label.in_(("advance", "reject")))
        )
    ).all()

    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    indeterminate = 0
    for label, extracted, total_score, rule_dims, judge_dims in rows:
        grade = whatif_grade(
            schema, extracted or {},
            stored_rule_subtotal=float((rule_dims or {}).get("subtotal", 0) or 0),
            stored_total=float(total_score),
            stored_hard_rejected=judge_dims is None,
        )
        if grade is None:
            indeterminate += 1
            continue
        counts[bucket(label, grade)] += 1

    uncovered = advance_reject_total - len(rows)
    metrics = _metrics_dict(
        counts["tp"], counts["fp"], counts["tn"], counts["fn"],
        indeterminate=indeterminate, borderline_excluded=borderline_excluded, uncovered=uncovered,
    )
    draft.golden_set_metrics = metrics
    await db.commit()

    # judge_dimensions_changed vs the active version
    changed = False
    if jd.active_rule_version_id is not None:
        active = (
            await db.execute(
                select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id)
            )
        ).scalar_one_or_none()
        if active is not None:
            changed = draft.schema_json.get("judge_dimensions") != active.schema_json.get(
                "judge_dimensions"
            )
    return metrics, changed


async def active_baseline(db: AsyncSession, *, jd: JD) -> dict | None:
    """The active version's metrics against the golden set (WP6b), shaped like RuleMetrics."""
    if jd.active_rule_version_id is None:
        return None
    report = await golden_metrics(db, jd.code)
    o = report.overall
    return {
        "confusion": o.confusion.model_dump(),
        "precision": o.precision, "recall": o.recall, "f1": o.f1, "accuracy": o.accuracy,
        "evaluated": o.confusion.tp + o.confusion.fp + o.confusion.tn + o.confusion.fn,
        "indeterminate": 0,
        "borderline_excluded": o.borderline_excluded,
        "uncovered": o.uncovered,
    }
```

- [ ] **Step 3: Add the evaluate route**

Add to `backend/app/routers/rule_publication.py` (import `EvaluateResponse`, `RuleMetrics`, `evaluate_draft`, `active_baseline`):

```python
from backend.app.schemas.rule_publication import EvaluateResponse, RuleMetrics
from backend.app.services.rule_publication import active_baseline, evaluate_draft


@router.post("/{code}/rule-versions/{version}/evaluate", response_model=EvaluateResponse)
async def evaluate(
    code: str, version: str,
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*WRITE_ROLES)),
) -> EvaluateResponse:
    jd = await _load_jd(db, code)
    draft = await _load_version(db, jd, version)
    try:
        metrics, changed = await evaluate_draft(db, jd=jd, draft=draft)
    except NotADraft as exc:
        raise HTTPException(
            status_code=409, detail={"code": "not_a_draft", "message": "只能评估草稿版本"}
        ) from exc
    baseline = await active_baseline(db, jd=jd)
    return EvaluateResponse(
        draft=RuleMetrics(**metrics),
        baseline=RuleMetrics(**baseline) if baseline is not None else None,
        judge_dimensions_changed=changed,
    )
```

- [ ] **Step 4: Extend the integration test**

Add to `backend/tests/integration/test_rule_publication.py` (import `Candidate`, `Score`, and `encrypt_pii` at the top: `from backend.app.models import JD, Candidate, RuleVersion, Score` and `from backend.app.security.crypto import encrypt_pii`):

```python
async def _seed_scored_golden(db, jd, *, label, extracted, total, rule_subtotal, judge_dims, pii_hash):
    from backend.app.models import GoldenSet

    cand = Candidate(source="upload", name_cipher=encrypt_pii("张三"), pii_hash=pii_hash,
                     extracted_json=extracted)
    db.add(cand)
    await db.flush()
    rv_id = jd.active_rule_version_id
    score = Score(candidate_id=cand.id, jd_id=jd.id, rule_version_id=rv_id, total_score=total,
                  grade=("rejected" if judge_dims is None else "L1"), hard_filter_result={},
                  rule_dimensions={"subtotal": rule_subtotal}, judge_dimensions=judge_dims,
                  is_suspicious=False)
    db.add(score)
    await db.flush()
    db.add(GoldenSet(candidate_id=cand.id, jd_id=jd.id, label=label,
                     imported_at=datetime.now(timezone.utc), imported_by_user_id=1))
    await db.flush()
    return cand


async def test_evaluate_then_publish_switches_active(client, db_session, auth_headers):
    jd = await _seed_jd_with_active(db_session)
    # golden advance, stored total 10 (rule subtotal 4, judge 6). Draft gives exp high 4 + 6 = 10 -> L1 -> advance -> TP.
    await _seed_scored_golden(db_session, jd, label="advance",
                              extracted={"years_experience": 5}, total=10, rule_subtotal=4,
                              judge_dims={"dimensions": []}, pii_hash="c1")
    await db_session.commit()
    await client.post("/api/v1/jds/FT/rule-versions", json={"schema_json": _schema("v2")},
                     headers=await auth_headers("hr_lead"))
    ev = await client.post("/api/v1/jds/FT/rule-versions/v2/evaluate", headers=await auth_headers("hr_lead"))
    assert ev.status_code == 200
    body = ev.json()
    assert body["draft"]["confusion"]["tp"] == 1
    assert "name_cipher" not in ev.text and "张三" not in ev.text
    # now publish succeeds and moves the active version
    pub = await client.post("/api/v1/jds/FT/rule-versions/v2/publish", headers=await auth_headers("admin"))
    assert pub.status_code == 200 and pub.json()["status"] == "published"
    jd_row = (await db_session.execute(select(JD).where(JD.code == "FT"))).scalar_one()
    v2 = (await db_session.execute(
        select(RuleVersion).where(RuleVersion.jd_id == jd_row.id, RuleVersion.version == "v2")
    )).scalar_one()
    assert jd_row.active_rule_version_id == v2.id
    v1 = (await db_session.execute(
        select(RuleVersion).where(RuleVersion.jd_id == jd_row.id, RuleVersion.version == "v1")
    )).scalar_one()
    assert v1.status == "archived"
```

Add `from sqlalchemy import select` at the top of the test file if not present.

- [ ] **Step 5: Run tests, ruff, mypy, commit**

```bash
# integration with env prefix
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/rule_publication.py backend/app/routers/rule_publication.py backend/app/schemas/rule_publication.py backend/tests/integration/test_rule_publication.py
git commit -m "feat(wp6c): what-if evaluate endpoint (draft vs baseline regression)"
```

---

## Task 5: Frontend — rule-management page (versions + create draft)

**Files:**
- Modify: `frontend/src/lib/schemas.ts`, `frontend/src/components/app-shell.tsx`
- Create: `frontend/src/app/(app)/jds/[code]/rules/page.tsx`, `frontend/src/components/rule-management-view.tsx`
- Test: `frontend/src/components/rule-management-view.test.tsx`

**Interfaces:**
- Produces zod: `RuleVersionList`, `EvaluateResponse`; component `<RuleManagementView code canManage />`.

- [ ] **Step 1: Add zod schemas**

Append to `frontend/src/lib/schemas.ts`:

```ts
const RuleMetrics = z.object({
  confusion: z.object({ tp: z.number(), fp: z.number(), tn: z.number(), fn: z.number() }),
  precision: z.number().nullable(),
  recall: z.number().nullable(),
  f1: z.number().nullable(),
  accuracy: z.number().nullable(),
  evaluated: z.number(),
  indeterminate: z.number(),
  borderline_excluded: z.number(),
  uncovered: z.number(),
});
export const EvaluateResponse = z.object({
  draft: RuleMetrics,
  baseline: RuleMetrics.nullable(),
  judge_dimensions_changed: z.boolean(),
});
export const RuleVersionList = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      version: z.string(),
      status: z.string(),
      published_at: z.string().nullable(),
      published_by_user_id: z.number().nullable(),
      notes: z.string().nullable(),
      golden_set_metrics: z.record(z.string(), z.unknown()).nullable(),
      is_active: z.boolean(),
    }),
  ),
  page: z.number(),
  page_size: z.number(),
  total: z.number(),
});
export const RuleVersionRef = z.object({
  id: z.number(),
  version: z.string(),
  status: z.string(),
  notes: z.string().nullable(),
});
```

- [ ] **Step 2: Write the failing component test**

```tsx
// frontend/src/components/rule-management-view.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RuleManagementView } from "@/components/rule-management-view";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

const LIST = {
  items: [
    { id: 1, version: "v1", status: "published", published_at: "2026-07-23T00:00:00Z",
      published_by_user_id: null, notes: null, golden_set_metrics: null, is_active: true },
    { id: 2, version: "v2", status: "draft", published_at: null,
      published_by_user_id: null, notes: null, golden_set_metrics: null, is_active: false },
  ],
  page: 1, page_size: 20, total: 2,
};

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("RuleManagementView", () => {
  it("hides the create-draft form when canManage is false", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify(LIST), { status: 200 })) as unknown as typeof fetch;
    render(wrap(<RuleManagementView code="FT" canManage={false} />));
    expect(await screen.findByText("v2")).toBeInTheDocument();
    expect(screen.queryByLabelText("规则 schema JSON")).not.toBeInTheDocument();
  });

  it("shows the create-draft form when canManage is true", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify(LIST), { status: 200 })) as unknown as typeof fetch;
    render(wrap(<RuleManagementView code="FT" canManage={true} />));
    expect(await screen.findByLabelText("规则 schema JSON")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run it (fails)** — FAIL.

- [ ] **Step 4: Implement `<RuleManagementView>`**

```tsx
// frontend/src/components/rule-management-view.tsx
"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiGet, apiPost, ApiError } from "@/lib/api-client";
import { RuleVersionList, RuleVersionRef } from "@/lib/schemas";
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

export function RuleManagementView({ code, canManage }: { code: string; canManage: boolean }) {
  const qc = useQueryClient();
  const [schemaText, setSchemaText] = useState("");
  const [notes, setNotes] = useState("");
  const path = `/api/v1/jds/${code}/rule-versions`;
  const list = useQuery({ queryKey: ["rule-versions", code], queryFn: () => apiGet(path, {}, RuleVersionList) });

  const create = useMutation({
    mutationFn: () => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(schemaText);
      } catch {
        throw new ApiError("invalid_json", "schema JSON 无法解析", 400);
      }
      return apiPost(path, { schema_json: parsed, notes: notes || null }, RuleVersionRef);
    },
    onSuccess: () => { toast.success("草稿已创建"); setSchemaText(""); setNotes(""); void qc.invalidateQueries({ queryKey: ["rule-versions", code] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "创建失败"),
  });

  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">规则版本 · {code}</h1>
      {canManage ? (
        <div className="space-y-2 rounded-md border p-4">
          <label htmlFor="schema-json" className="text-sm font-medium">规则 schema JSON</label>
          <textarea id="schema-json" aria-label="规则 schema JSON" className="h-40 w-full rounded-md border p-2 font-mono text-xs"
            value={schemaText} onChange={(e) => setSchemaText(e.target.value)} placeholder='{"version":"v2", ...}' />
          <input aria-label="备注" className="w-full rounded-md border p-2 text-sm" placeholder="备注（可选）"
            value={notes} onChange={(e) => setNotes(e.target.value)} />
          <Button size="sm" disabled={create.isPending || !schemaText.trim()} onClick={() => create.mutate()}>
            {create.isPending ? "创建中…" : "创建草稿"}
          </Button>
        </div>
      ) : null}

      <DataState isLoading={list.isLoading} error={list.error ? { message: (list.error as Error).message } : null}
        isEmpty={list.data?.items.length === 0} emptyText="暂无规则版本" onRetry={() => list.refetch()}>
        <Table>
          <TableHeader>
            <TableRow><TableHead>版本</TableHead><TableHead>状态</TableHead><TableHead>已评估</TableHead></TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.items.map((v) => (
              <TableRow key={v.id}>
                <TableCell>{v.version}{v.is_active ? "（生效）" : ""}</TableCell>
                <TableCell>{v.status}</TableCell>
                <TableCell>{v.golden_set_metrics ? "是" : "否"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataState>
    </section>
  );
}
```

The create response is a `RuleVersionRef` (`{id, version, status, notes}`), validated by the `RuleVersionRef` zod schema added in Step 1.

- [ ] **Step 5: Implement the server page (reads role)**

```tsx
// frontend/src/app/(app)/jds/[code]/rules/page.tsx
import { cookies } from "next/headers";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { RuleManagementView } from "@/components/rule-management-view";

export const dynamic = "force-dynamic";

export default async function RulesPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  const canManage = session?.role === "hr_lead" || session?.role === "admin";
  return <RuleManagementView code={code} canManage={canManage} />;
}
```

- [ ] **Step 6: Add a nav entry**

In `frontend/src/components/app-shell.tsx`, add inside the `<nav>` (after 基线指标):

```tsx
<Link href="/jds" className="text-muted-foreground hover:text-foreground">
  职位
</Link>
```

(The JDs list page — if `/jds` does not already exist as a page, link instead to the existing JD list route used by WP5; confirm the real route and point the nav there. The rule-management page is reached from a JD's detail/rules link.)

- [ ] **Step 7: Run tests + gates + commit**

```bash
cd frontend && npm run test && npm run typecheck && npm run lint
git add frontend/src
git commit -m "feat(wp6c): rule-management page with versions list and draft creation"
```

---

## Task 6: Frontend — evaluate comparison + gated publish

**Files:**
- Modify: `frontend/src/components/rule-management-view.tsx`
- Test: extend `frontend/src/components/rule-management-view.test.tsx`

**Interfaces:**
- Produces: per-draft evaluate + publish actions and the draft-vs-baseline comparison in `<RuleManagementView>`.

- [ ] **Step 1: Write the failing test (evaluate comparison)**

```tsx
// add to frontend/src/components/rule-management-view.test.tsx
import userEvent from "@testing-library/user-event";

it("shows the draft-vs-baseline comparison after evaluate", async () => {
  const EVAL = {
    draft: { confusion: { tp: 2, fp: 0, tn: 1, fn: 1 }, precision: 1, recall: 0.6667, f1: 0.8, accuracy: 0.75,
             evaluated: 4, indeterminate: 0, borderline_excluded: 0, uncovered: 0 },
    baseline: { confusion: { tp: 1, fp: 1, tn: 1, fn: 1 }, precision: 0.5, recall: 0.5, f1: 0.5, accuracy: 0.5,
                evaluated: 4, indeterminate: 0, borderline_excluded: 0, uncovered: 0 },
    judge_dimensions_changed: false,
  };
  global.fetch = vi.fn(async (url: string) => {
    if (url.includes("/evaluate")) return new Response(JSON.stringify(EVAL), { status: 200 });
    return new Response(JSON.stringify(LIST), { status: 200 });
  }) as unknown as typeof fetch;
  render(wrap(<RuleManagementView code="FT" canManage={true} />));
  await userEvent.click(await screen.findByRole("button", { name: "评估 v2" }));
  expect(await screen.findByText(/草稿 F1/)).toBeInTheDocument();
  expect(screen.getByText("80%")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it (fails)** — FAIL.

- [ ] **Step 3: Add evaluate + publish to the component**

In `frontend/src/components/rule-management-view.tsx`, add per-draft actions and the comparison. Add a `pct` helper and, inside the component, an evaluate mutation keyed by version, a publish mutation, and render an "评估 {version}" button + a "发布" button (enabled only when `v.golden_set_metrics` is set) for each `draft` row, plus a comparison block when an evaluation result is present:

```tsx
import { EvaluateResponse } from "@/lib/schemas";

function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

// inside the component:
const [evalResult, setEvalResult] = useState<{ version: string; data: z.infer<typeof EvaluateResponse> } | null>(null);
const evaluate = useMutation({
  mutationFn: (version: string) => apiPost(`${path}/${version}/evaluate`, {}, EvaluateResponse).then((d) => ({ version, data: d })),
  onSuccess: (r) => { setEvalResult(r); void qc.invalidateQueries({ queryKey: ["rule-versions", code] }); },
  onError: (e) => toast.error(e instanceof ApiError ? e.message : "评估失败"),
});
const publish = useMutation({
  mutationFn: (version: string) => apiPost(`${path}/${version}/publish`, {}, RuleVersionRef),
  onSuccess: () => { toast.success("已发布"); setEvalResult(null); void qc.invalidateQueries({ queryKey: ["rule-versions", code] }); },
  onError: (e) => toast.error(e instanceof ApiError ? e.message : "发布失败"),
});
```

Import `z` from `zod` and `RuleVersionRef` from `@/lib/schemas`. In the versions table, add an actions cell for each `draft` row (only when `canManage`):

```tsx
<TableCell>
  {canManage && v.status === "draft" ? (
    <div className="flex gap-2">
      <Button size="sm" variant="outline" disabled={evaluate.isPending} onClick={() => evaluate.mutate(v.version)}>
        评估 {v.version}
      </Button>
      <Button size="sm" disabled={!v.golden_set_metrics || publish.isPending} onClick={() => publish.mutate(v.version)}>
        发布
      </Button>
    </div>
  ) : null}
</TableCell>
```

Add a comparison block after the table:

```tsx
{evalResult ? (
  <div className="rounded-md border p-4">
    <h2 className="mb-2 font-medium">What-If 对比 · 草稿 {evalResult.version}</h2>
    {evalResult.data.judge_dimensions_changed ? (
      <p className="text-destructive text-sm">⚠ 草稿改动了 judge 维度，重算复用了旧 judge 分，结果为近似值。</p>
    ) : null}
    <p>草稿 F1 <span className="font-semibold">{pct(evalResult.data.draft.f1)}</span>
      {"　"}基线 F1 <span className="font-semibold">{evalResult.data.baseline ? pct(evalResult.data.baseline.f1) : "—"}</span></p>
    <p className="text-muted-foreground text-sm">
      草稿混淆 TP{evalResult.data.draft.confusion.tp}/FP{evalResult.data.draft.confusion.fp}/TN{evalResult.data.draft.confusion.tn}/FN{evalResult.data.draft.confusion.fn}
      · 已评估 {evalResult.data.draft.evaluated} · indeterminate {evalResult.data.draft.indeterminate}
    </p>
  </div>
) : null}
```

- [ ] **Step 4: Run tests + gates + commit**

```bash
cd frontend && npm run test && npm run typecheck && npm run lint && npm run build
git add frontend/src
git commit -m "feat(wp6c): what-if comparison and gated publish in rule-management"
```

---

## Task 7: E2E, docs, full gate, push, CI, and WP6c exit review

**Files:**
- Create: `frontend/e2e/rule-publication.spec.ts`
- Modify: `README.md`, roadmap + plan index.

- [ ] **Step 1: Write the e2e (stubbed BFF)**

```ts
// frontend/e2e/rule-publication.spec.ts
import { test, expect } from "@playwright/test";
import { mintSession } from "./helpers/session";

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([
    { name: "ssa_session", value: mintSession({ token: "e2e", displayName: "测试Lead", role: "hr_lead" }),
      url: "http://127.0.0.1:4173" },
  ]);
  await page.route("**/api/proxy/api/v1/jds/FT/rule-versions**", (r) =>
    r.fulfill({ status: 200, json: {
      items: [
        { id: 1, version: "v1", status: "published", published_at: "2026-07-23T00:00:00Z",
          published_by_user_id: null, notes: null, golden_set_metrics: null, is_active: true },
        { id: 2, version: "v2", status: "draft", published_at: null,
          published_by_user_id: null, notes: null, golden_set_metrics: null, is_active: false },
      ], page: 1, page_size: 20, total: 2,
    } }),
  );
});

test("rule-management page lists versions with status", async ({ page }) => {
  await page.goto("/jds/FT/rules");
  await expect(page.getByRole("heading", { name: /规则版本/ })).toBeVisible();
  await expect(page.getByText("v2")).toBeVisible();
  await expect(page.getByText("draft")).toBeVisible();
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

Document the rule publication workflow + What-If + regression gate in `README.md`. In the roadmap authority (`docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`, the WP6 section) and the plan index (`docs/superpowers/plans/README.md` row 6), mark WP6c **In progress** (do NOT mark WP6c Complete, WP6 Complete, or WP7 Ready until hosted CI passes).

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e README.md docs/
git commit -m "test(wp6c): rule-publication e2e and docs"
```

- [ ] **Step 6: Push + PR + hosted CI**

```bash
git push -u origin codex/wp6c-rule-publication
gh pr create --base main --title "WP6c: rule publication, what-if, and regression gates" --body "<summary + exit evidence>"
```

Require hosted `verify.yml` (backend `unit-and-static` 3.10/3.14 + `integration`) green — WP6c adds a migration + backend routes, so the backend CI genuinely exercises the change.

- [ ] **Step 7: Record evidence + mark WP6c Complete / WP6 Complete / WP7 Ready** only after every backend gate + hosted CI + the frontend local gate pass.

---

## Self-Review

**Spec coverage:** §5 data model + migration → Task 1. §6 draft lifecycle + schema validation → Task 3 (create_draft). §7 What-If computation → Task 2 (core) + Task 4 (`evaluate_draft` aggregation + baseline). §8.1 create / §8.3 publish → Task 3; §8.2 evaluate → Task 4; §8.4 list status → Task 3. §9 architecture (service/router/schemas; frontend page + role gate) → Tasks 2–6. §10 auth/leak (write hr_lead/admin, read hr+, publish gate, no-PII assertion) → Tasks 3/4. §11 config (none) → n/a. §12 tests → Tasks 1–7 (unit whatif_grade/bucket, integration create/publish-gate/evaluate/active-switch/archive/Alembic, frontend component + e2e). §13 rollout / §14 exit → Task 7.

**Placeholder scan:** the only placeholder is the migration revision id `<rev>` (generated by `alembic revision` in Task 1 Step 2, referenced in Step 3) — intrinsic to Alembic, resolved in-task. The create mutation validates its response with the `RuleVersionRef` zod schema defined in Task 5 Step 1 (no placeholder). All other steps contain real code.

**Type consistency:** service names (`whatif_grade`, `bucket`, `create_draft`, `publish_draft`, `evaluate_draft`, `active_baseline`) are defined once (Tasks 2–4) and consumed by the router (Tasks 3/4). `publish_draft` gains a `publisher_id` param (noted in Task 3 Step 5/6). Response models (`CreateDraftRequest`, `RuleVersionRef`, `RuleMetrics`, `EvaluateResponse`) live in `schemas/rule_publication.py`; `RuleMetrics.confusion` reuses `Confusion` from `schemas/golden_set.py`. `RuleVersionItem` gains `status` and nullable `published_at` (Task 3) — the frontend `RuleVersionList` zod matches (status + nullable published_at). Frontend `EvaluateResponse`/`RuleVersionRef` zod match the backend field names. The What-If reuses `run_hard_filters`/`score_dimensions`/`_grade_from` (scoring) + `metric_stats`/`golden_metrics`/`Confusion` (WP6b) — the deliberate `_grade_from` import keeps the What-If grade identical to live scoring (avoiding a duplicated, drift-prone threshold function).
