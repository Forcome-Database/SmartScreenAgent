# WP6a Feedback Capture and Minimal Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an HR reviewer record a verdict (`advance`/`reject`/`hold`) + reason on each score — attributable per reviewer, with a server-derived AI-agreement flag — and surface a minimal aggregate report (overall/per-JD agreement rate + PII-free disagreement list), over the WP4/WP5 surface.

**Architecture:** Backend feedback router + service + schemas over the existing `Feedback` model (plus a small Alembic migration adding a per-reviewer uniqueness constraint and a decision CHECK). Frontend extends the WP5 scorecard with a `FeedbackPanel` and adds a report page, hitting the existing `/api/proxy` BFF (feedback routes are `/api/v1/*`, already allowlisted). No scoring change; feedback never mutates a score.

**Tech Stack:** Python 3.10–3.14, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, PostgreSQL; Next.js 15, TanStack Query, zod, Vitest, Playwright.

## Global Constraints

- Backend default CI runs `pytest -m "not integration and not external_contract"`; offline & deterministic. Integration on this host uses the env prefix `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000" uv run pytest ...` (test stack: PG 25432, MinIO 9000, Redis 56379).
- All feedback routes require Bearer JWT with role in `("hr","hr_lead","admin")` via `require_roles(*roles)` (`backend/app/deps.py`). Errors are `{code, message}`; pagination is offset `{items, page, page_size, total}` reusing `backend/app/services/read/pagination.py` (`Page`, `resolve_page`, `page_params`, `PageMeta`).
- `ai_agreed` is derived SERVER-SIDE from `score.grade` (never trusted from the client): AI reject ⟺ `grade == "rejected"`. `hold → ai_agreed = null`. When `ai_agreed` is `false`, `reason` is required (blank/missing → `422 feedback_reason_required`).
- One feedback row per `(score_id, reviewer_user_id)` (upsert). Multiple reviewers per score allowed. Feedback never mutates the score.
- No candidate PII, ciphertext, or object keys in any feedback/report response — only `candidate_id`/`jd_code`/`score_id`/reviewer refs + HR-authored reason.
- Alembic head before WP6a is `2f27938b430b`. Run `uv run ruff check backend` and `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports` before each backend commit. Frontend gates (`cd frontend`): `npm run lint && npm run typecheck && npm run test`.
- Base UI (not Radix) in the frontend: use `<Button render={<a/>}>` not `asChild`. `useSearchParams` needs a `<Suspense>` boundary. Exclude `.superpowers/`, `backend.zip`.

---

## File Structure

**Backend — Create:**
- `migrations/versions/<rev>_wp6a_feedback_constraints.py` — uq + decision CHECK.
- `backend/app/services/feedback.py` — `derive_ai_agreed`, `upsert_feedback`, `list_feedback`, `feedback_report`.
- `backend/app/schemas/feedback.py` — request/response models.
- `backend/app/routers/feedback.py` — PUT/GET feedback + GET report.
- Tests: `backend/tests/unit/test_feedback_service.py`, `backend/tests/integration/test_feedback_api.py`.

**Backend — Modify:**
- `backend/app/models/feedback.py` — add `__table_args__` (uq + check).
- `backend/app/main.py` — register the feedback router.
- `backend/tests/integration/test_db_migrations.py` — bump expected head to `<rev>`.

**Frontend — Create (`frontend/`):**
- `src/components/feedback-panel.tsx`, `src/app/(app)/reports/feedback/page.tsx`.
- Tests: `src/components/feedback-panel.test.tsx`, `e2e/feedback.spec.ts`.

**Frontend — Modify:**
- `src/lib/schemas.ts` — feedback + report zod schemas.
- `src/app/(app)/candidates/[id]/scores/[sid]/page.tsx` — mount `<FeedbackPanel>`.
- `src/components/app-shell.tsx` — nav link to the report page.

---

## Task 1: Feedback model constraints + Alembic migration

**Files:**
- Modify: `backend/app/models/feedback.py`, `backend/tests/integration/test_db_migrations.py`
- Create: `migrations/versions/<rev>_wp6a_feedback_constraints.py`
- Test: the existing `backend/tests/integration/test_db_migrations.py` (upgrade-to-head).

**Interfaces:**
- Produces: `feedback` table constraints `uq_feedback_score_reviewer(score_id, reviewer_user_id)` and `ck_feedback_decision (decision IN ('advance','reject','hold'))`; new Alembic head `<rev>`.

- [ ] **Step 1: Add constraints to the model**

```python
# backend/app/models/feedback.py  (replace the imports + class header)
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"

    __table_args__ = (
        UniqueConstraint("score_id", "reviewer_user_id", name="uq_feedback_score_reviewer"),
        CheckConstraint("decision IN ('advance', 'reject', 'hold')", name="ck_feedback_decision"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scores.id"), nullable=False, index=True
    )
    reviewer_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    ai_agreed: Mapped[bool | None] = mapped_column(Boolean)
```

- [ ] **Step 2: Generate the migration file**

Run: `uv run alembic revision -m "wp6a feedback constraints"` — note the generated revision id `<rev>`. Replace its `upgrade`/`downgrade` with:

```python
def upgrade() -> None:
    op.create_unique_constraint(
        "uq_feedback_score_reviewer", "feedback", ["score_id", "reviewer_user_id"]
    )
    op.create_check_constraint(
        "ck_feedback_decision", "feedback", "decision IN ('advance', 'reject', 'hold')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_feedback_decision", "feedback", type_="check")
    op.drop_constraint("uq_feedback_score_reviewer", "feedback", type_="unique")
```

Confirm `down_revision = "2f27938b430b"` (the current head) in the generated file.

- [ ] **Step 3: Bump the expected head in the migration test**

In `backend/tests/integration/test_db_migrations.py`, update the expected head assertion from `2f27938b430b` to `<rev>` (search for the literal `2f27938b430b`). Keep the upgrade/downgrade round-trip assertions.

- [ ] **Step 4: Run the migration test (integration)**

Run: `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000" uv run pytest backend/tests/integration/test_db_migrations.py -q`
Expected: PASS (upgrades to `<rev>`, round-trips).

- [ ] **Step 5: Offline suite + ruff + mypy + commit**

```bash
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/models/feedback.py migrations/versions/ backend/tests/integration/test_db_migrations.py
git commit -m "feat(wp6a): feedback per-reviewer uniqueness + decision CHECK migration"
```

---

## Task 2: Feedback service — verdict derivation, upsert, list

**Files:**
- Create: `backend/app/services/feedback.py`
- Test: `backend/tests/unit/test_feedback_service.py`

**Interfaces:**
- Produces: `derive_ai_agreed(grade: str, decision: str) -> bool | None`; `async upsert_feedback(db, *, score, reviewer_id, decision, reason) -> Feedback` (raises `FeedbackReasonRequired` when disagreement lacks a reason); `async list_feedback(db, score_id) -> list[tuple[Feedback, str]]` (feedback + reviewer display_name). Exception `FeedbackReasonRequired(Exception)`.

- [ ] **Step 1: Write the failing unit test**

```python
# backend/tests/unit/test_feedback_service.py
import pytest

from backend.app.services.feedback import FeedbackReasonRequired, derive_ai_agreed


def test_derive_ai_agreed_quadrants():
    # AI advance = grade != "rejected"
    assert derive_ai_agreed("L4", "advance") is True
    assert derive_ai_agreed("L4", "reject") is False
    assert derive_ai_agreed("rejected", "reject") is True
    assert derive_ai_agreed("rejected", "advance") is False


def test_hold_is_none():
    assert derive_ai_agreed("L4", "hold") is None
    assert derive_ai_agreed("rejected", "hold") is None


def test_reason_required_symbol_exists():
    assert issubclass(FeedbackReasonRequired, Exception)
```

- [ ] **Step 2: Run it (fails)** — `uv run pytest backend/tests/unit/test_feedback_service.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement the service**

```python
# backend/app/services/feedback.py
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Feedback, Score, User


class FeedbackReasonRequired(Exception):
    """Raised when a disagreement (ai_agreed is False) is submitted without a reason."""


def derive_ai_agreed(grade: str, decision: str) -> bool | None:
    """AI reject == grade "rejected"; hold yields None (excluded from agreement)."""
    if decision == "hold":
        return None
    ai_reject = grade == "rejected"
    hr_reject = decision == "reject"
    return ai_reject == hr_reject


async def upsert_feedback(
    db: AsyncSession, *, score: Score, reviewer_id: int, decision: str, reason: str | None
) -> Feedback:
    ai_agreed = derive_ai_agreed(score.grade, decision)
    normalized_reason = (reason or "").strip() or None
    if ai_agreed is False and not normalized_reason:
        raise FeedbackReasonRequired()
    stmt = (
        pg_insert(Feedback)
        .values(
            score_id=score.id,
            reviewer_user_id=reviewer_id,
            decision=decision,
            reason=normalized_reason,
            ai_agreed=ai_agreed,
        )
        .on_conflict_do_update(
            constraint="uq_feedback_score_reviewer",
            set_={
                "decision": decision,
                "reason": normalized_reason,
                "ai_agreed": ai_agreed,
                "updated_at": func.now(),
            },
        )
        .returning(Feedback.id)
    )
    feedback_id = (await db.execute(stmt)).scalar_one()
    await db.commit()
    return (
        await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    ).scalar_one()


async def list_feedback(db: AsyncSession, score_id: int) -> list[tuple[Feedback, str]]:
    rows = (
        await db.execute(
            select(Feedback, User.display_name)
            .join(User, User.id == Feedback.reviewer_user_id)
            .where(Feedback.score_id == score_id)
            .order_by(Feedback.updated_at.desc().nullslast(), Feedback.id.desc())
        )
    ).all()
    return [(fb, name) for fb, name in rows]
```

- [ ] **Step 4: Run tests (pass)** — `uv run pytest backend/tests/unit/test_feedback_service.py -q` → PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/feedback.py backend/tests/unit/test_feedback_service.py
git commit -m "feat(wp6a): feedback service (ai_agreed derivation, upsert, list)"
```

---

## Task 3: Feedback schemas + router (upsert + list)

**Files:**
- Create: `backend/app/schemas/feedback.py`, `backend/app/routers/feedback.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_feedback_api.py`

**Interfaces:**
- Consumes: Task 2 service, `require_roles`, `get_db`, `get_current_user`.
- Produces routes: `PUT /api/v1/candidates/{candidate_id}/scores/{score_id}/feedback`, `GET /api/v1/candidates/{candidate_id}/scores/{score_id}/feedback`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_feedback_api.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.models import JD, Candidate, Feedback, RuleVersion, Score
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration


async def _seed(db, grade="L4"):
    jd = JD(code="FT", name="Foreign Trade", description="", status="active")
    db.add(jd); await db.flush()
    rv = RuleVersion(jd_id=jd.id, version="v1", schema_json={}, published_at=datetime.now(timezone.utc))
    db.add(rv); await db.flush()
    cand = Candidate(source="upload", name_cipher=encrypt_pii("张三"), pii_hash="h1", extracted_json={})
    db.add(cand); await db.flush()
    score = Score(candidate_id=cand.id, jd_id=jd.id, rule_version_id=rv.id, total_score=80,
                  grade=grade, hard_filter_result={}, rule_dimensions={}, is_suspicious=False)
    db.add(score); await db.commit()
    return cand, score


async def test_upsert_creates_then_updates_one_row(client, db_session, auth_headers):
    cand, score = await _seed(db_session, grade="L4")
    base = f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback"
    r1 = await client.put(base, json={"decision": "advance"}, headers=await auth_headers("hr"))
    assert r1.status_code == 200 and r1.json()["ai_agreed"] is True
    r2 = await client.put(base, json={"decision": "reject", "reason": "经验不符"}, headers=await auth_headers("hr"))
    assert r2.status_code == 200 and r2.json()["ai_agreed"] is False
    count = len((await db_session.execute(select(Feedback).where(Feedback.score_id == score.id))).all())
    assert count == 1  # same reviewer upserts


async def test_disagreement_requires_reason(client, db_session, auth_headers):
    cand, score = await _seed(db_session, grade="L4")  # AI advance
    r = await client.put(f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback",
                         json={"decision": "reject"}, headers=await auth_headers("hr"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "feedback_reason_required"


async def test_list_and_auth(client, db_session, auth_headers):
    cand, score = await _seed(db_session)
    await client.put(f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback",
                    json={"decision": "hold"}, headers=await auth_headers("hr"))
    lst = await client.get(f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback",
                          headers=await auth_headers("hr"))
    assert lst.status_code == 200 and lst.json()[0]["decision"] == "hold"
    assert lst.json()[0]["ai_agreed"] is None
    noauth = await client.put(f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback", json={"decision": "hold"})
    assert noauth.status_code == 401
```

- [ ] **Step 2: Run it (fails)** — routes missing (404/401 mismatch).

- [ ] **Step 3: Implement schemas**

```python
# backend/app/schemas/feedback.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Decision = Literal["advance", "reject", "hold"]


class FeedbackUpsertRequest(BaseModel):
    decision: Decision
    reason: str | None = None


class FeedbackItem(BaseModel):
    id: int
    score_id: int
    reviewer_user_id: int
    reviewer_display_name: str
    decision: str
    reason: str | None
    ai_agreed: bool | None
    created_at: datetime
    updated_at: datetime | None
```

- [ ] **Step 4: Implement the router**

```python
# backend/app/routers/feedback.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import get_current_user, require_roles
from backend.app.models import Candidate, Score, User
from backend.app.schemas.feedback import FeedbackItem, FeedbackUpsertRequest
from backend.app.services.feedback import (
    FeedbackReasonRequired,
    list_feedback,
    upsert_feedback,
)

router = APIRouter(prefix="/api/v1", tags=["feedback"])
ROLES = ("hr", "hr_lead", "admin")


async def _load_score(db: AsyncSession, candidate_id: int, score_id: int) -> Score:
    score = (
        await db.execute(
            select(Score).where(Score.id == score_id, Score.candidate_id == candidate_id)
        )
    ).scalar_one_or_none()
    if score is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "score not found"})
    return score


def _serialize(fb, display_name: str) -> FeedbackItem:
    return FeedbackItem(
        id=fb.id, score_id=fb.score_id, reviewer_user_id=fb.reviewer_user_id,
        reviewer_display_name=display_name, decision=fb.decision, reason=fb.reason,
        ai_agreed=fb.ai_agreed, created_at=fb.created_at, updated_at=fb.updated_at,
    )


@router.put("/candidates/{candidate_id}/scores/{score_id}/feedback", response_model=FeedbackItem)
async def upsert(
    candidate_id: int, score_id: int, payload: FeedbackUpsertRequest,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ROLES)),
) -> FeedbackItem:
    score = await _load_score(db, candidate_id, score_id)
    try:
        fb = await upsert_feedback(
            db, score=score, reviewer_id=user.id, decision=payload.decision, reason=payload.reason
        )
    except FeedbackReasonRequired as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "feedback_reason_required", "message": "与 AI 不一致时必须填写理由"},
        ) from exc
    return _serialize(fb, user.display_name)


@router.get("/candidates/{candidate_id}/scores/{score_id}/feedback", response_model=list[FeedbackItem])
async def list_for_score(
    candidate_id: int, score_id: int,
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*ROLES)),
) -> list[FeedbackItem]:
    await _load_score(db, candidate_id, score_id)
    return [_serialize(fb, name) for fb, name in await list_feedback(db, score_id)]
```

Register in `backend/app/main.py`: add `from backend.app.routers import feedback as feedback_router` and `app.include_router(feedback_router.router)`.

- [ ] **Step 5: Run tests (pass)** — with the integration env prefix → PASS.

- [ ] **Step 6: Offline + ruff + mypy + commit**

```bash
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/schemas/feedback.py backend/app/routers/feedback.py backend/app/main.py backend/tests/integration/test_feedback_api.py
git commit -m "feat(wp6a): feedback upsert + list endpoints"
```

---

## Task 4: Feedback report service + endpoint

**Files:**
- Modify: `backend/app/services/feedback.py`, `backend/app/routers/feedback.py`, `backend/app/schemas/feedback.py`
- Test: `backend/tests/unit/test_feedback_report.py`, extend `backend/tests/integration/test_feedback_api.py`

**Interfaces:**
- Produces: `async feedback_report(db, jd_code, page) -> FeedbackReport`; route `GET /api/v1/feedback/report?jd_code=&page=&page_size=`. Report models `AgreementStats`, `JDAgreement`, `DisagreementItem`, `FeedbackReport`.

- [ ] **Step 1: Write the failing unit test (aggregation math)**

```python
# backend/tests/unit/test_feedback_report.py
from backend.app.services.feedback import agreement_stats


def test_agreement_rate_excludes_hold_and_handles_zero():
    # (agreed, disagreed, hold)
    assert agreement_stats(3, 1, 2) == {"total": 6, "agreed": 3, "disagreed": 1, "hold": 2, "agreement_rate": 0.75}
    assert agreement_stats(0, 0, 0)["agreement_rate"] is None
    assert agreement_stats(0, 0, 5)["agreement_rate"] is None
```

- [ ] **Step 2: Run it (fails)** — FAIL.

- [ ] **Step 3: Add report models + aggregation to schemas and service**

Append to `backend/app/schemas/feedback.py`:

```python
from backend.app.services.read.pagination import PageMeta


class AgreementStats(BaseModel):
    total: int
    agreed: int
    disagreed: int
    hold: int
    agreement_rate: float | None


class JDAgreement(AgreementStats):
    jd_code: str


class DisagreementItem(BaseModel):
    feedback_id: int
    score_id: int
    candidate_id: int
    jd_code: str
    decision: str
    reason: str | None
    reviewer_display_name: str
    updated_at: datetime | None


class DisagreementPage(PageMeta):
    items: list[DisagreementItem]


class FeedbackReport(BaseModel):
    overall: AgreementStats
    by_jd: list[JDAgreement]
    disagreements: DisagreementPage
```

Append to `backend/app/services/feedback.py`:

```python
from backend.app.models import JD  # add to existing imports
from backend.app.schemas.feedback import (
    AgreementStats,
    DisagreementItem,
    DisagreementPage,
    FeedbackReport,
    JDAgreement,
)
from backend.app.services.read.pagination import Page


def agreement_stats(agreed: int, disagreed: int, hold: int) -> dict:
    decided = agreed + disagreed
    rate = (agreed / decided) if decided else None
    return {
        "total": agreed + disagreed + hold,
        "agreed": agreed,
        "disagreed": disagreed,
        "hold": hold,
        "agreement_rate": rate,
    }


async def feedback_report(db: AsyncSession, jd_code: str | None, page: Page) -> FeedbackReport:
    # counts grouped by (jd_code, ai_agreed) — ai_agreed True/False/None
    grouped = (
        select(JD.code, Feedback.ai_agreed, func.count().label("n"))
        .select_from(Feedback)
        .join(Score, Score.id == Feedback.score_id)
        .join(JD, JD.id == Score.jd_id)
    )
    if jd_code is not None:
        grouped = grouped.where(JD.code == jd_code)
    grouped = grouped.group_by(JD.code, Feedback.ai_agreed)
    rows = (await db.execute(grouped)).all()

    per_jd: dict[str, list[int]] = {}
    tot_agreed = tot_disagreed = tot_hold = 0
    for code, ai_agreed, n in rows:
        bucket = per_jd.setdefault(code, [0, 0, 0])  # agreed, disagreed, hold
        if ai_agreed is True:
            bucket[0] += n; tot_agreed += n
        elif ai_agreed is False:
            bucket[1] += n; tot_disagreed += n
        else:
            bucket[2] += n; tot_hold += n

    by_jd = [
        JDAgreement(jd_code=code, **agreement_stats(a, d, h))
        for code, (a, d, h) in sorted(per_jd.items())
    ]
    overall = AgreementStats(**agreement_stats(tot_agreed, tot_disagreed, tot_hold))

    # disagreements list (ai_agreed is False), paginated
    dis_base = (
        select(
            Feedback.id, Feedback.score_id, Score.candidate_id, JD.code,
            Feedback.decision, Feedback.reason, User.display_name, Feedback.updated_at,
        )
        .select_from(Feedback)
        .join(Score, Score.id == Feedback.score_id)
        .join(JD, JD.id == Score.jd_id)
        .join(User, User.id == Feedback.reviewer_user_id)
        .where(Feedback.ai_agreed.is_(False))
    )
    if jd_code is not None:
        dis_base = dis_base.where(JD.code == jd_code)
    total = (await db.execute(select(func.count()).select_from(dis_base.subquery()))).scalar_one()
    dis_rows = (
        await db.execute(
            dis_base.order_by(Feedback.updated_at.desc().nullslast(), Feedback.id.desc())
            .offset(page.offset).limit(page.page_size)
        )
    ).all()
    items = [
        DisagreementItem(
            feedback_id=fid, score_id=sid, candidate_id=cid, jd_code=code,
            decision=dec, reason=reason, reviewer_display_name=name, updated_at=updated,
        )
        for fid, sid, cid, code, dec, reason, name, updated in dis_rows
    ]
    return FeedbackReport(
        overall=overall,
        by_jd=by_jd,
        disagreements=DisagreementPage(
            items=items, page=page.page, page_size=page.page_size, total=total
        ),
    )
```

- [ ] **Step 4: Add the report route**

Add to `backend/app/routers/feedback.py`:

```python
from backend.app.schemas.feedback import FeedbackReport
from backend.app.services.feedback import feedback_report
from backend.app.services.read.pagination import Page, page_params


@router.get("/feedback/report", response_model=FeedbackReport)
async def report(
    jd_code: str | None = None, page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*ROLES)),
) -> FeedbackReport:
    return await feedback_report(db, jd_code, page)
```

- [ ] **Step 5: Extend the integration test**

Add to `backend/tests/integration/test_feedback_api.py`:

```python
async def test_report_aggregates(client, db_session, auth_headers):
    cand, score = await _seed(db_session, grade="L4")  # AI advance
    # HR rejects (disagreement, reason required)
    await client.put(f"/api/v1/candidates/{cand.id}/scores/{score.id}/feedback",
                    json={"decision": "reject", "reason": "x"}, headers=await auth_headers("hr"))
    rep = await client.get("/api/v1/feedback/report", headers=await auth_headers("hr"))
    assert rep.status_code == 200
    body = rep.json()
    assert body["overall"]["disagreed"] == 1
    assert body["overall"]["agreement_rate"] == 0.0
    assert body["disagreements"]["total"] == 1
    assert body["disagreements"]["items"][0]["jd_code"] == "FT"
    assert "name_cipher" not in rep.text and "张三" not in rep.text  # no PII
```

- [ ] **Step 6: Run tests, ruff, mypy, commit**

```bash
uv run pytest backend/tests/unit/test_feedback_report.py -q
# integration with env prefix
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/feedback.py backend/app/routers/feedback.py backend/app/schemas/feedback.py backend/tests/unit/test_feedback_report.py backend/tests/integration/test_feedback_api.py
git commit -m "feat(wp6a): feedback aggregate report endpoint"
```

---

## Task 5: Frontend — feedback schemas + FeedbackPanel on the scorecard

**Files:**
- Modify: `frontend/src/lib/schemas.ts`, `frontend/src/app/(app)/candidates/[id]/scores/[sid]/page.tsx`
- Create: `frontend/src/components/feedback-panel.tsx`
- Test: `frontend/src/components/feedback-panel.test.tsx`

**Interfaces:**
- Produces zod: `FeedbackItem`, `FeedbackList`; component `<FeedbackPanel candidateId scoreId />`.

- [ ] **Step 1: Add zod schemas**

Append to `frontend/src/lib/schemas.ts`:

```ts
export const FeedbackItem = z.object({
  id: z.number(),
  score_id: z.number(),
  reviewer_user_id: z.number(),
  reviewer_display_name: z.string(),
  decision: z.string(),
  reason: z.string().nullable(),
  ai_agreed: z.boolean().nullable(),
  created_at: z.string(),
  updated_at: z.string().nullable(),
});
export const FeedbackList = z.array(FeedbackItem);
```

- [ ] **Step 2: Write the failing component test**

```tsx
// frontend/src/components/feedback-panel.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FeedbackPanel } from "@/components/feedback-panel";

const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("FeedbackPanel", () => {
  it("requires a reason before submitting a disagreement-capable verdict", async () => {
    global.fetch = vi.fn(async (url: string) => {
      if (url.includes("/feedback")) return new Response(JSON.stringify([]), { status: 200 });
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    render(wrap(<FeedbackPanel candidateId={7} scoreId={9} aiRejected={false} />));
    // choose reject (disagrees with AI advance) then submit without reason → blocked client-side
    await userEvent.click(await screen.findByRole("button", { name: "淘汰" }));
    await userEvent.click(screen.getByRole("button", { name: /提交/ }));
    expect(screen.getByText(/请填写理由/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run it (fails)** — FAIL.

- [ ] **Step 4: Implement `<FeedbackPanel>`**

```tsx
// frontend/src/components/feedback-panel.tsx
"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { z } from "zod";
import { apiGet, apiPut, ApiError } from "@/lib/api-client";
import { FeedbackItem, FeedbackList } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { DataState } from "@/components/data-state";

type Decision = "advance" | "reject" | "hold";
const LABELS: Record<Decision, string> = { advance: "推进", reject: "淘汰", hold: "待定" };

export function FeedbackPanel({
  candidateId, scoreId, aiRejected,
}: { candidateId: number; scoreId: number; aiRejected: boolean }) {
  const qc = useQueryClient();
  const [decision, setDecision] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const path = `/api/v1/candidates/${candidateId}/scores/${scoreId}/feedback`;

  const list = useQuery({ queryKey: ["feedback", candidateId, scoreId], queryFn: () => apiGet(path, {}, FeedbackList) });

  const disagrees = (d: Decision) => (d === "reject") !== aiRejected && d !== "hold";

  const mutation = useMutation({
    mutationFn: () => apiPut(path, { decision, reason: reason.trim() || undefined }, FeedbackItem),
    onSuccess: () => { toast.success("已保存反馈"); setReason(""); void qc.invalidateQueries({ queryKey: ["feedback", candidateId, scoreId] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "保存失败"),
  });

  function submit() {
    setLocalError(null);
    if (!decision) { setLocalError("请选择裁决"); return; }
    if (disagrees(decision) && !reason.trim()) { setLocalError("与 AI 不一致时请填写理由"); return; }
    mutation.mutate();
  }

  return (
    <div className="space-y-3 rounded-md border p-4">
      <h3 className="font-medium">我的复核</h3>
      <div className="flex gap-2">
        {(["advance", "reject", "hold"] as Decision[]).map((d) => (
          <Button key={d} variant={decision === d ? "default" : "outline"} size="sm" onClick={() => setDecision(d)}>
            {LABELS[d]}
          </Button>
        ))}
      </div>
      <textarea
        className="w-full rounded-md border p-2 text-sm"
        placeholder="理由（与 AI 不一致时必填）"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {localError ? <p className="text-destructive text-sm">{localError}</p> : null}
      <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "提交中…" : "提交反馈"}</Button>

      <div className="pt-2">
        <h4 className="text-muted-foreground text-sm">复核记录</h4>
        <DataState isLoading={list.isLoading} error={list.error ? { message: (list.error as Error).message } : null} isEmpty={list.data?.length === 0} emptyText="暂无复核">
          <ul className="mt-1 space-y-1 text-sm">
            {list.data?.map((f: z.infer<typeof FeedbackItem>) => (
              <li key={f.id} className="flex items-center justify-between">
                <span>{f.reviewer_display_name}：{LABELS[f.decision as Decision] ?? f.decision}{f.reason ? `（${f.reason}）` : ""}</span>
                <span className={f.ai_agreed === false ? "text-destructive" : "text-muted-foreground"}>
                  {f.ai_agreed === true ? "与AI一致" : f.ai_agreed === false ? "与AI不一致" : "待定"}
                </span>
              </li>
            ))}
          </ul>
        </DataState>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Add `apiPut` to the client**

Append to `frontend/src/lib/api-client.ts`:

```ts
export async function apiPut<T>(upstreamPath: string, body: unknown, schema: z.ZodType<T>): Promise<T> {
  const res = await fetch(`/api/proxy${upstreamPath}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return parseOrThrow(res, schema);
}
```

Note: the generic proxy route currently exports `GET` and `POST`. Add a `PUT` export mirroring `POST`:

```ts
// frontend/src/app/api/proxy/[...path]/route.ts — add alongside GET/POST
export async function PUT(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(req, (await ctx.params).path, "PUT");
}
```

- [ ] **Step 6: Mount on the scorecard page**

In `frontend/src/app/(app)/candidates/[id]/scores/[sid]/page.tsx`, after the `<Scorecard>` render, add (inside the `DataState` success block, where `query.data` is available):

```tsx
{query.data ? (
  <FeedbackPanel candidateId={Number(id)} scoreId={Number(sid)} aiRejected={query.data.grade === "rejected"} />
) : null}
```

Import `FeedbackPanel`.

- [ ] **Step 7: Run tests + gates + commit**

```bash
cd frontend && npm run test && npm run typecheck && npm run lint
git add frontend/src
git commit -m "feat(wp6a): FeedbackPanel on scorecard with reviewer attribution"
```

---

## Task 6: Frontend — feedback report page + nav

**Files:**
- Create: `frontend/src/app/(app)/reports/feedback/page.tsx`
- Modify: `frontend/src/lib/schemas.ts`, `frontend/src/components/app-shell.tsx`
- Test: `frontend/src/app/(app)/reports/feedback/report.test.tsx`

**Interfaces:**
- Produces zod: `FeedbackReport`; the report page at `/reports/feedback`.

- [ ] **Step 1: Add the report zod schema**

Append to `frontend/src/lib/schemas.ts`:

```ts
const AgreementStats = z.object({
  total: z.number(), agreed: z.number(), disagreed: z.number(), hold: z.number(),
  agreement_rate: z.number().nullable(),
});
export const FeedbackReport = z.object({
  overall: AgreementStats,
  by_jd: z.array(AgreementStats.extend({ jd_code: z.string() })),
  disagreements: z.object({
    items: z.array(z.object({
      feedback_id: z.number(), score_id: z.number(), candidate_id: z.number(),
      jd_code: z.string(), decision: z.string(), reason: z.string().nullable(),
      reviewer_display_name: z.string(), updated_at: z.string().nullable(),
    })),
    page: z.number(), page_size: z.number(), total: z.number(),
  }),
});
```

- [ ] **Step 2: Write the failing report-page test**

```tsx
// frontend/src/app/(app)/reports/feedback/report.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReportPage from "./page";

const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });

describe("Feedback report page", () => {
  it("renders the overall agreement rate", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify({
      overall: { total: 4, agreed: 3, disagreed: 1, hold: 0, agreement_rate: 0.75 },
      by_jd: [{ jd_code: "FT", total: 4, agreed: 3, disagreed: 1, hold: 0, agreement_rate: 0.75 }],
      disagreements: { items: [], page: 1, page_size: 20, total: 0 },
    }), { status: 200 })) as unknown as typeof fetch;
    render(<QueryClientProvider client={new QueryClient()}><ReportPage /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByText(/75%/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 3: Run it (fails)** — FAIL.

- [ ] **Step 4: Implement the report page**

```tsx
// frontend/src/app/(app)/reports/feedback/page.tsx
"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { FeedbackReport } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function pct(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

export default function FeedbackReportPage() {
  const query = useQuery({ queryKey: ["feedback-report"], queryFn: () => apiGet("/api/v1/feedback/report", {}, FeedbackReport) });
  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">复核报告</h1>
      <DataState isLoading={query.isLoading} error={query.error ? { message: (query.error as Error).message } : null} onRetry={() => query.refetch()}>
        {query.data ? (
          <div className="space-y-6">
            <div className="rounded-md border p-4">
              <p>总体一致率：<span className="text-2xl font-semibold">{pct(query.data.overall.agreement_rate)}</span></p>
              <p className="text-muted-foreground text-sm">
                共 {query.data.overall.total} 条 · 一致 {query.data.overall.agreed} · 不一致 {query.data.overall.disagreed} · 待定 {query.data.overall.hold}
              </p>
            </div>
            <div>
              <h2 className="mb-2 font-medium">按 JD</h2>
              <Table>
                <TableHeader><TableRow><TableHead>JD</TableHead><TableHead>一致率</TableHead><TableHead>一致/不一致/待定</TableHead></TableRow></TableHeader>
                <TableBody>
                  {query.data.by_jd.map((j) => (
                    <TableRow key={j.jd_code}>
                      <TableCell>{j.jd_code}</TableCell>
                      <TableCell>{pct(j.agreement_rate)}</TableCell>
                      <TableCell>{j.agreed}/{j.disagreed}/{j.hold}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div>
              <h2 className="mb-2 font-medium">不一致明细（{query.data.disagreements.total}）</h2>
              <Table>
                <TableHeader><TableRow><TableHead>候选人</TableHead><TableHead>JD</TableHead><TableHead>裁决</TableHead><TableHead>理由</TableHead><TableHead>审阅人</TableHead></TableRow></TableHeader>
                <TableBody>
                  {query.data.disagreements.items.map((d) => (
                    <TableRow key={d.feedback_id}>
                      <TableCell><Link className="underline" href={`/candidates/${d.candidate_id}/scores/${d.score_id}`}>{d.candidate_id}</Link></TableCell>
                      <TableCell>{d.jd_code}</TableCell>
                      <TableCell>{d.decision}</TableCell>
                      <TableCell>{d.reason ?? "—"}</TableCell>
                      <TableCell>{d.reviewer_display_name}</TableCell>
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

In `frontend/src/components/app-shell.tsx`, add to the `<nav>`:

```tsx
<Link href="/reports/feedback" className="text-muted-foreground hover:text-foreground">复核报告</Link>
```

- [ ] **Step 6: Run tests + gates + commit**

```bash
cd frontend && npm run test && npm run typecheck && npm run lint && npm run build
git add frontend/src
git commit -m "feat(wp6a): feedback report page and nav"
```

---

## Task 7: E2E, docs, full gate, push, CI, and WP6a exit review

**Files:**
- Create: `frontend/e2e/feedback.spec.ts`
- Modify: `README.md`, roadmap + plan index.

- [ ] **Step 1: Write the e2e (stubbed BFF)**

```ts
// frontend/e2e/feedback.spec.ts
import { test, expect } from "@playwright/test";
import { mintSession } from "./helpers/session";

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([{ name: "ssa_session", value: mintSession({ token: "e2e", displayName: "测试HR", role: "hr" }), url: "http://127.0.0.1:4173" }]);
  await page.route("**/api/proxy/api/v1/feedback/report**", (r) =>
    r.fulfill({ status: 200, json: { overall: { total: 2, agreed: 1, disagreed: 1, hold: 0, agreement_rate: 0.5 }, by_jd: [{ jd_code: "FT", total: 2, agreed: 1, disagreed: 1, hold: 0, agreement_rate: 0.5 }], disagreements: { items: [], page: 1, page_size: 20, total: 0 } } }),
  );
});

test("feedback report shows the agreement rate", async ({ page }) => {
  await page.goto("/reports/feedback");
  await expect(page.getByText("复核报告")).toBeVisible();
  await expect(page.getByText("50%")).toBeVisible();
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

Document the feedback capture + report in `README.md`. In the roadmap and plan index, mark WP6a **In progress** (do NOT mark Complete or WP6b Ready until hosted CI passes).

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e README.md docs/
git commit -m "test(wp6a): feedback e2e and docs"
```

- [ ] **Step 6: Push + PR + hosted CI**

```bash
git push -u origin codex/wp6a-feedback-capture
gh pr create --title "WP6a: feedback capture and minimal reporting" --body "<summary + exit evidence>"
```

Require hosted `verify.yml` (backend `unit-and-static` 3.10/3.14 + `integration`) green — WP6a adds a migration + backend routes, so the backend CI now genuinely exercises the change.

- [ ] **Step 7: Record evidence + mark WP6a Complete / WP6b Ready** only after every backend gate + hosted CI + the frontend local gate pass.

---

## Self-Review

**Spec coverage:** §5 data model + migration → Task 1. §6 ai_agreed derivation → Task 2 (+ enforced in Task 3 router). §7.1 upsert / §7.2 list → Task 3. §7.3 report → Task 4. §8 frontend FeedbackPanel → Task 5; report page → Task 6. §9 auth/leak → Tasks 3/4 (require_roles, no-PII assertion in the report test). §11 tests → Tasks 1–7 (unit derivation + report math, integration upsert/uq/404/reason/report, frontend component + e2e, Alembic round-trip). §12 rollout/§13 exit → Task 7.

**Placeholder scan:** the only placeholder is the migration revision id `<rev>` (generated by `alembic revision` in Task 1 Step 2, then referenced in Task 1 Step 3) — this is intrinsic to Alembic and is explicitly resolved in-task. All other steps contain real code.

**Type consistency:** service functions (`derive_ai_agreed`, `upsert_feedback`, `list_feedback`, `agreement_stats`, `feedback_report`) and response models (`FeedbackItem`, `FeedbackReport`, `AgreementStats`, `JDAgreement`, `DisagreementItem`, `DisagreementPage`) are defined once and consumed consistently across Tasks 2–4; frontend `apiPut` (Task 5) + the proxy `PUT` export are added together; `FeedbackItem`/`FeedbackList`/`FeedbackReport` zod schemas (Tasks 5/6) match the backend response field names. The report reuses `PageMeta`/`Page`/`page_params` from `backend/app/services/read/pagination.py`.
