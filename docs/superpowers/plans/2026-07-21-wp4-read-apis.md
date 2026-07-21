# WP4 Read APIs and Rule Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the read-only HTTP surface (candidate lists, candidate/score detail, raw-file download, JD/rule-version list and diff) an HR API client needs to complete upload → monitor → list → inspect → re-score without direct database access.

**Architecture:** Thin FastAPI read routers (`candidates_read.py`, `jds.py`) translate HTTP to a new `backend/app/services/read/` layer that owns pagination, filtering, ordering, and the decrypt-plus-audit orchestration. PII is decrypted only in candidate detail and raw-file download, each writing one audit row; list endpoints never decrypt. No schema change, no migration.

**Tech Stack:** Python 3.10–3.14, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, MinIO presigned URLs, pytest, Ruff, mypy.

## Global Constraints

- Default CI runs `pytest -m "not integration and not external_contract"`; unit tests stay offline and deterministic.
- All read routes require Bearer JWT with role in `("hr", "hr_lead", "admin")` via the existing `require_roles` dependency (`backend/app/deps.py::require_roles(*roles)`).
- List endpoints never decrypt PII and never write an audit row. Candidate detail and raw-file download decrypt PII and write exactly one audit row each.
- Never leak into responses or logs: ciphertext, PII beyond the authorized detail body, object keys, presigned URLs, or provider bodies.
- Pagination is offset-based: `page` (1-based, default 1), `page_size` (default `READ_PAGE_SIZE_DEFAULT`=20, max `READ_PAGE_SIZE_MAX`=100); list responses use `{items, page, page_size, total}`.
- No schema change and no Alembic migration in WP4.
- SQLAlchemy 2.0 async (`select`, `AsyncSession`); scoped conventional commits; exclude `.superpowers/`, `backend.zip`, `.firecrawl/`.
- Run `uv run ruff check backend` and `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports` before each commit.
- Integration tests on this Windows host: the test stack is `docker-compose.test.yml`; run MinIO-touching integration tests with `MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest ...` (test MinIO is remapped to 9000; port 61000 is reserved on this host).

---

## File Structure

**Create:**
- `backend/app/services/read/__init__.py`
- `backend/app/services/read/pagination.py` — `Page` params, `page_params` FastAPI dependency, `PageMeta`.
- `backend/app/services/read/rule_diff.py` — pure `diff_schemas(from_schema, to_schema) -> list[dict]`.
- `backend/app/services/read/candidates.py` — candidate/score read services.
- `backend/app/services/read/jds.py` — JD/rule-version read services.
- `backend/app/routers/candidates_read.py` — candidate list/detail/score/raw-file routes.
- `backend/app/routers/jds.py` — JD and rule-version routes.
- `backend/app/schemas/read.py` — Pydantic response models for every read route.
- Unit tests: `backend/tests/unit/test_pagination.py`, `test_rule_diff.py`, `test_read_serializers.py`.
- Integration tests: `backend/tests/integration/test_candidates_read_api.py`, `test_jds_api.py`, `test_openapi_contract.py`.

**Modify:**
- `backend/app/config.py` — add three read settings.
- `backend/app/main.py` — register the two new routers.
- `backend/tests/test_bootstrap.py` — add the three new settings to `TEST_ENV_DEFAULTS`.
- `README.md`, `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`, `docs/superpowers/plans/README.md` — WP4 status and docs.

---

## Task 1: Read settings, pagination helper, and read package

**Files:**
- Modify: `backend/app/config.py`, `backend/tests/test_bootstrap.py`
- Create: `backend/app/services/read/__init__.py`, `backend/app/services/read/pagination.py`
- Test: `backend/tests/unit/test_pagination.py`

**Interfaces:**
- Produces: `Settings.RAW_FILE_PRESIGN_TTL_SECONDS: int`, `READ_PAGE_SIZE_DEFAULT: int`, `READ_PAGE_SIZE_MAX: int`; `Page(page: int, page_size: int)` with `.offset -> int`; `resolve_page(page: int | None, page_size: int | None) -> Page`; `page_params` FastAPI dependency returning `Page`; `PageMeta(page, page_size, total)` helper for building the response envelope.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_pagination.py
import pytest

from backend.app.services.read.pagination import Page, resolve_page


def test_offset_computes_from_page_and_size():
    assert Page(page=1, page_size=20).offset == 0
    assert Page(page=3, page_size=20).offset == 40


def test_resolve_clamps_size_and_defaults():
    # default when page_size is None
    assert resolve_page(None, None).page_size == 20
    # clamp above max (100)
    assert resolve_page(1, 500).page_size == 100
    # floor at 1
    assert resolve_page(1, 0).page_size == 1
    # page floors at 1
    assert resolve_page(0, 20).page == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/unit/test_pagination.py -q`
Expected: FAIL with `ModuleNotFoundError: backend.app.services.read.pagination`.

- [ ] **Step 3: Add settings**

Add to `backend/app/config.py` after the WP3 "Ingestion jobs" block:

```python
    # Read APIs (WP4)
    RAW_FILE_PRESIGN_TTL_SECONDS: int = 300
    READ_PAGE_SIZE_DEFAULT: int = 20
    READ_PAGE_SIZE_MAX: int = 100
```

Add the same three keys (as strings) to `TEST_ENV_DEFAULTS` in `backend/tests/test_bootstrap.py`:

```python
    "RAW_FILE_PRESIGN_TTL_SECONDS": "300",
    "READ_PAGE_SIZE_DEFAULT": "20",
    "READ_PAGE_SIZE_MAX": "100",
```

- [ ] **Step 4: Create the pagination module**

```python
# backend/app/services/read/pagination.py
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel

from backend.app.config import get_settings


@dataclass(frozen=True)
class Page:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def resolve_page(page: int | None, page_size: int | None) -> Page:
    settings = get_settings()
    resolved_page = max(1, page or 1)
    size = page_size if page_size is not None else settings.READ_PAGE_SIZE_DEFAULT
    resolved_size = max(1, min(size, settings.READ_PAGE_SIZE_MAX))
    return Page(page=resolved_page, page_size=resolved_size)


def page_params(
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1),
) -> Page:
    return resolve_page(page, page_size)


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
```

Create `backend/app/services/read/__init__.py` (empty module marker).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest backend/tests/unit/test_pagination.py -q`
Expected: PASS.

- [ ] **Step 6: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/config.py backend/tests/test_bootstrap.py backend/app/services/read/ backend/tests/unit/test_pagination.py
git commit -m "feat(wp4): add read settings and offset pagination helper"
```

---

## Task 2: Rule-version diff (pure function)

**Files:**
- Create: `backend/app/services/read/rule_diff.py`
- Test: `backend/tests/unit/test_rule_diff.py`

**Interfaces:**
- Produces: `diff_schemas(from_schema: dict, to_schema: dict) -> list[dict]` returning a list of `{path, kind, before, after}` where `kind` in `added|removed|changed`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_rule_diff.py
from backend.app.services.read.rule_diff import diff_schemas


def _schema(**over):
    base = {
        "passing_threshold": 40,
        "total_score": 100,
        "hard_filters": [{"id": "age_max", "rule": "age <= 45", "action": "reject"}],
        "rule_dimensions": [{"id": "trade", "name": "Trade", "weight": 25}],
        "judge_dimensions": [{"id": "independence", "name": "Independence", "weight": 5}],
        "grade_thresholds": [{"grade": "L5", "min": 90}, {"grade": "L1", "min": 40}],
    }
    base.update(over)
    return base


def test_no_change_returns_empty():
    assert diff_schemas(_schema(), _schema()) == []


def test_reorder_is_no_change():
    a = _schema()
    b = _schema(grade_thresholds=[{"grade": "L1", "min": 40}, {"grade": "L5", "min": 90}])
    assert diff_schemas(a, b) == []


def test_scalar_change():
    changes = diff_schemas(_schema(), _schema(passing_threshold=50))
    assert {"path": "passing_threshold", "kind": "changed", "before": 40, "after": 50} in changes


def test_dimension_added_removed_changed():
    a = _schema()
    b = _schema(rule_dimensions=[{"id": "trade", "name": "Trade", "weight": 30}, {"id": "edu", "name": "Edu", "weight": 12}])
    changes = diff_schemas(a, b)
    paths = {(c["path"], c["kind"]) for c in changes}
    assert ("rule_dimensions[trade]", "changed") in paths
    assert ("rule_dimensions[edu]", "added") in paths


def test_grade_removed():
    a = _schema()
    b = _schema(grade_thresholds=[{"grade": "L5", "min": 90}])
    changes = diff_schemas(a, b)
    assert ("grade_thresholds[L1]", "removed") in {(c["path"], c["kind"]) for c in changes}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/unit/test_rule_diff.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the diff**

```python
# backend/app/services/read/rule_diff.py
from __future__ import annotations

from typing import Any

_SCALARS = ("passing_threshold", "total_score")
_KEYED = (
    ("hard_filters", "id"),
    ("rule_dimensions", "id"),
    ("judge_dimensions", "id"),
    ("grade_thresholds", "grade"),
)


def _index(items: list[dict] | None, key: str) -> dict[Any, dict]:
    return {item[key]: item for item in (items or []) if key in item}


def diff_schemas(from_schema: dict, to_schema: dict) -> list[dict]:
    """Structural diff of two rule schema_json objects. Pure, deterministic;
    collections are matched by id/grade so reordering alone yields no change."""
    changes: list[dict] = []

    for path in _SCALARS:
        before, after = from_schema.get(path), to_schema.get(path)
        if before != after:
            changes.append({"path": path, "kind": "changed", "before": before, "after": after})

    for collection, key in _KEYED:
        before_index = _index(from_schema.get(collection), key)
        after_index = _index(to_schema.get(collection), key)
        for missing in sorted(set(before_index) - set(after_index), key=str):
            changes.append(
                {"path": f"{collection}[{missing}]", "kind": "removed",
                 "before": before_index[missing], "after": None}
            )
        for added in sorted(set(after_index) - set(before_index), key=str):
            changes.append(
                {"path": f"{collection}[{added}]", "kind": "added",
                 "before": None, "after": after_index[added]}
            )
        for common in sorted(set(before_index) & set(after_index), key=str):
            if before_index[common] != after_index[common]:
                changes.append(
                    {"path": f"{collection}[{common}]", "kind": "changed",
                     "before": before_index[common], "after": after_index[common]}
                )

    return changes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/tests/unit/test_rule_diff.py -q`
Expected: PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/read/rule_diff.py backend/tests/unit/test_rule_diff.py
git commit -m "feat(wp4): add pure rule-version schema diff"
```

---

## Task 3: Response models and candidate read service

**Files:**
- Create: `backend/app/schemas/read.py`, `backend/app/services/read/candidates.py`
- Test: `backend/tests/unit/test_read_serializers.py`

**Interfaces:**
- Produces response models in `schemas/read.py`: `RankedCandidateItem`, `RankedCandidateList`, `CandidateListItem`, `CandidateList`, `CandidateDetail`, `ScoreDetail`, `RawFileLink`, `JDItem`, `JDList`, `JDDetail`, `RuleVersionItem`, `RuleVersionList`, `RuleDiffChange`, `RuleDiffResponse`.
- Produces in `services/read/candidates.py`: `async list_ranked_for_jd(db, jd_code, grade, page) -> tuple[list[RankedCandidateItem], int]`; `async list_candidates(db, state, page) -> tuple[list[CandidateListItem], int]`; `async get_candidate_detail(db, candidate_id, *, actor, trace_id) -> CandidateDetail | None` (decrypts + writes one `pii_decrypt` audit row); `async get_score_detail(db, candidate_id, score_id) -> ScoreDetail | None`.

This task builds the read service and its response models; its endpoints land in Task 4. Because the service uses a real async session, its behavior is covered by integration tests in Task 4; this task's unit test covers only PII-free serialization of the list item models.

- [ ] **Step 1: Write the failing unit test**

```python
# backend/tests/unit/test_read_serializers.py
from backend.app.schemas.read import CandidateListItem, RankedCandidateItem


def test_list_items_expose_no_pii_fields():
    ranked = set(RankedCandidateItem.model_fields)
    flat = set(CandidateListItem.model_fields)
    forbidden = {"name", "phone", "email", "name_cipher", "raw_file_key", "parsed_markdown"}
    assert ranked.isdisjoint(forbidden)
    assert flat.isdisjoint(forbidden)
    assert {"candidate_id", "score_id", "total_score", "grade"} <= ranked
    assert {"candidate_id", "created_at", "latest_state", "scored_jd_codes"} <= flat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/unit/test_read_serializers.py -q`
Expected: FAIL with `ModuleNotFoundError: backend.app.schemas.read`.

- [ ] **Step 3: Create the response models**

```python
# backend/app/schemas/read.py
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.app.services.read.pagination import PageMeta


class RankedCandidateItem(BaseModel):
    candidate_id: int
    score_id: int
    total_score: float
    grade: str
    rule_version: str
    scored_at: datetime


class RankedCandidateList(PageMeta):
    items: list[RankedCandidateItem]


class CandidateListItem(BaseModel):
    candidate_id: int
    created_at: datetime
    latest_state: str | None
    scored_jd_codes: list[str]


class CandidateList(PageMeta):
    items: list[CandidateListItem]


class CandidateScoreSummary(BaseModel):
    score_id: int
    jd_code: str
    total_score: float
    grade: str
    rule_version: str


class CandidateDetail(BaseModel):
    candidate_id: int
    name: str
    phone: str | None
    email: str | None
    age: int | None
    education: str | None
    experiences: list[dict]
    source: str
    created_at: datetime
    scores: list[CandidateScoreSummary]


class ScoreDetail(BaseModel):
    score_id: int
    candidate_id: int
    jd_code: str
    rule_version: str
    total_score: float
    grade: str
    hard_filter_result: dict
    rule_dimensions: dict
    judge_dimensions: dict | None


class RawFileLink(BaseModel):
    url: str
    expires_in_seconds: int


class JDItem(BaseModel):
    code: str
    name: str
    status: str
    active_rule_version: str | None


class JDList(PageMeta):
    items: list[JDItem]


class JDDetail(BaseModel):
    code: str
    name: str
    description: str | None
    status: str
    active_rule_version: dict | None


class RuleVersionItem(BaseModel):
    id: int
    version: str
    published_at: datetime
    published_by_user_id: int | None
    notes: str | None
    golden_set_metrics: dict | None
    is_active: bool


class RuleVersionList(PageMeta):
    items: list[RuleVersionItem]


class RuleDiffChange(BaseModel):
    path: str
    kind: str
    before: dict | int | float | str | None
    after: dict | int | float | str | None


class RuleDiffResponse(BaseModel):
    jd_code: str
    from_version: str
    to_version: str
    changes: list[RuleDiffChange]
```

- [ ] **Step 4: Implement the candidate read service**

```python
# backend/app/services/read/candidates.py
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, AuditLog, Candidate, IngestionJob, RuleVersion, Score
from backend.app.schemas.read import (
    CandidateDetail,
    CandidateListItem,
    CandidateScoreSummary,
    RankedCandidateItem,
    ScoreDetail,
)
from backend.app.security.crypto import decrypt_pii
from backend.app.services.read.pagination import Page


async def list_ranked_for_jd(
    db: AsyncSession, jd_code: str, grade: str | None, page: Page
) -> tuple[list[RankedCandidateItem], int] | None:
    jd = (await db.execute(select(JD).where(JD.code == jd_code))).scalar_one_or_none()
    if jd is None:
        return None
    if not jd.active_rule_version_id:
        return [], 0
    base = (
        select(Score, RuleVersion.version)
        .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
        .where(Score.jd_id == jd.id, Score.rule_version_id == jd.active_rule_version_id)
    )
    if grade is not None:
        base = base.where(Score.grade == grade)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Score.total_score.desc(), Score.id.asc())
            .offset(page.offset).limit(page.page_size)
        )
    ).all()
    items = [
        RankedCandidateItem(
            candidate_id=score.candidate_id, score_id=score.id,
            total_score=float(score.total_score), grade=score.grade,
            rule_version=version, scored_at=score.created_at,
        )
        for score, version in rows
    ]
    return items, total


async def list_candidates(
    db: AsyncSession, state: str | None, page: Page
) -> tuple[list[CandidateListItem], int]:
    # latest ingestion job state per candidate via a correlated subquery
    latest_state = (
        select(IngestionJob.state)
        .where(IngestionJob.candidate_id == Candidate.id)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    base = select(Candidate.id, Candidate.created_at, latest_state.label("latest_state"))
    if state is not None:
        base = base.where(latest_state == state)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Candidate.created_at.desc()).offset(page.offset).limit(page.page_size)
        )
    ).all()
    items: list[CandidateListItem] = []
    for candidate_id, created_at, state_value in rows:
        codes = (
            await db.execute(
                select(JD.code).join(Score, Score.jd_id == JD.id).where(Score.candidate_id == candidate_id).distinct()
            )
        ).scalars().all()
        items.append(
            CandidateListItem(
                candidate_id=candidate_id, created_at=created_at,
                latest_state=state_value, scored_jd_codes=list(codes),
            )
        )
    return items, total


async def get_candidate_detail(
    db: AsyncSession, candidate_id: int, *, actor: str, trace_id: str | None
) -> CandidateDetail | None:
    candidate = (
        await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    ).scalar_one_or_none()
    if candidate is None:
        return None
    extracted = candidate.extracted_json or {}
    score_rows = (
        await db.execute(
            select(Score, JD.code, RuleVersion.version)
            .join(JD, JD.id == Score.jd_id)
            .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
            .where(Score.candidate_id == candidate_id)
        )
    ).all()
    db.add(
        AuditLog(
            event_type="pii_decrypt", actor=actor, target_type="candidate",
            target_id=candidate_id,
            payload={"purpose": "candidate_detail", "trace_id": trace_id},
        )
    )
    await db.commit()
    return CandidateDetail(
        candidate_id=candidate.id,
        name=decrypt_pii(candidate.name_cipher),
        phone=decrypt_pii(candidate.phone_cipher) if candidate.phone_cipher else None,
        email=decrypt_pii(candidate.email_cipher) if candidate.email_cipher else None,
        age=extracted.get("age"),
        education=extracted.get("education"),
        experiences=extracted.get("experiences", []),
        source=candidate.source,
        created_at=candidate.created_at,
        scores=[
            CandidateScoreSummary(
                score_id=s.id, jd_code=code, total_score=float(s.total_score),
                grade=s.grade, rule_version=version,
            )
            for s, code, version in score_rows
        ],
    )


async def get_score_detail(
    db: AsyncSession, candidate_id: int, score_id: int
) -> ScoreDetail | None:
    row = (
        await db.execute(
            select(Score, JD.code, RuleVersion.version)
            .join(JD, JD.id == Score.jd_id)
            .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
            .where(Score.id == score_id, Score.candidate_id == candidate_id)
        )
    ).first()
    if row is None:
        return None
    score, jd_code, version = row
    return ScoreDetail(
        score_id=score.id, candidate_id=score.candidate_id, jd_code=jd_code,
        rule_version=version, total_score=float(score.total_score), grade=score.grade,
        hard_filter_result=score.hard_filter_result, rule_dimensions=score.rule_dimensions,
        judge_dimensions=score.judge_dimensions,
    )
```

- [ ] **Step 5: Run the unit test**

Run: `uv run pytest backend/tests/unit/test_read_serializers.py -q`
Expected: PASS.

- [ ] **Step 6: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/schemas/ backend/app/services/read/candidates.py backend/tests/unit/test_read_serializers.py
git commit -m "feat(wp4): add read response models and candidate read service"
```

---

## Task 4: Candidate read router (lists, detail, score, raw-file)

**Files:**
- Create: `backend/app/routers/candidates_read.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_candidates_read_api.py`

**Interfaces:**
- Consumes: the Task 3 read service and response models; `require_roles`, `get_db`, `page_params`, `ResumeStorageService`.
- Produces routes: `GET /api/v1/jds/{code}/candidates`, `GET /api/v1/candidates`, `GET /api/v1/candidates/{candidate_id}`, `GET /api/v1/candidates/{candidate_id}/scores/{score_id}`, `GET /api/v1/candidates/{candidate_id}/raw-file`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_candidates_read_api.py
import pytest
from sqlalchemy import func, select

from backend.app.models import JD, AuditLog, Candidate, RuleVersion, Score
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration


async def _seed(db):
    jd = JD(code="FT", name="Foreign Trade", description="", status="active")
    db.add(jd); await db.flush()
    from datetime import datetime, timezone
    rv = RuleVersion(jd_id=jd.id, version="v1", schema_json={}, published_at=datetime.now(timezone.utc))
    db.add(rv); await db.flush()
    jd.active_rule_version_id = rv.id
    cand = Candidate(source="upload", name_cipher=encrypt_pii("张三"),
                     phone_cipher=encrypt_pii("13800000000"),
                     pii_hash="h1", extracted_json={"age": 30, "education": "本科", "experiences": []})
    db.add(cand); await db.flush()
    score = Score(candidate_id=cand.id, jd_id=jd.id, rule_version_id=rv.id, total_score=80,
                  grade="L4", hard_filter_result={"passed": True}, rule_dimensions={}, is_suspicious=False)
    db.add(score); await db.commit()
    return jd, cand, score


async def test_ranked_list_no_pii_no_audit(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)
    before = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    resp = await client.get(f"/api/v1/jds/{jd.code}/candidates", headers=await auth_headers("hr"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["candidate_id"] == cand.id
    assert "name" not in body["items"][0] and "张三" not in resp.text
    after = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    assert after == before  # list writes no audit


async def test_detail_decrypts_and_writes_one_audit(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)
    before = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    resp = await client.get(f"/api/v1/candidates/{cand.id}", headers=await auth_headers("hr"))
    assert resp.status_code == 200
    assert resp.json()["name"] == "张三"
    after = (await db_session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.event_type == "pii_decrypt"))).scalar_one()
    assert after == before + 1


async def test_score_detail_and_unknown(client, db_session, auth_headers):
    jd, cand, score = await _seed(db_session)
    resp = await client.get(f"/api/v1/candidates/{cand.id}/scores/{score.id}", headers=await auth_headers("hr"))
    assert resp.status_code == 200 and resp.json()["grade"] == "L4"
    missing = await client.get(f"/api/v1/candidates/{cand.id}/scores/999999", headers=await auth_headers("hr"))
    assert missing.status_code == 404


async def test_read_requires_auth(client):
    resp = await client.get("/api/v1/candidates")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest backend/tests/integration/test_candidates_read_api.py -q`
Expected: FAIL with 404s (routes not registered).

- [ ] **Step 3: Implement the router**

```python
# backend/app/routers/candidates_read.py
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import User
from backend.app.schemas.read import (
    CandidateDetail, CandidateList, RankedCandidateList, RawFileLink, ScoreDetail,
)
from backend.app.services.read.candidates import (
    get_candidate_detail, get_score_detail, list_candidates, list_ranked_for_jd,
)
from backend.app.services.read.pagination import Page, page_params
from backend.app.services.storage import ResumeStorageService, StorageError

router = APIRouter(prefix="/api/v1", tags=["read"])
READ_ROLES = ("hr", "hr_lead", "admin")


def _not_found(resource: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "not_found", "message": f"{resource} not found"})


@router.get("/jds/{code}/candidates", response_model=RankedCandidateList)
async def ranked_candidates(
    code: str, grade: str | None = None, page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*READ_ROLES)),
) -> RankedCandidateList:
    result = await list_ranked_for_jd(db, code, grade, page)
    if result is None:
        raise _not_found("JD")
    items, total = result
    return RankedCandidateList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get("/candidates", response_model=CandidateList)
async def candidates(
    state: str | None = None, page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*READ_ROLES)),
) -> CandidateList:
    items, total = await list_candidates(db, state, page)
    return CandidateList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
async def candidate_detail(
    candidate_id: int, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> CandidateDetail:
    trace_id = structlog.contextvars.get_contextvars().get("trace_id")
    detail = await get_candidate_detail(db, candidate_id, actor=f"user:{user.id}", trace_id=trace_id)
    if detail is None:
        raise _not_found("candidate")
    return detail


@router.get("/candidates/{candidate_id}/scores/{score_id}", response_model=ScoreDetail)
async def score_detail(
    candidate_id: int, score_id: int, db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> ScoreDetail:
    detail = await get_score_detail(db, candidate_id, score_id)
    if detail is None:
        raise _not_found("score")
    return detail


@router.get("/candidates/{candidate_id}/raw-file", response_model=RawFileLink)
async def raw_file(
    candidate_id: int, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> RawFileLink:
    from sqlalchemy import select
    from backend.app.models import AuditLog, Candidate

    candidate = (await db.execute(select(Candidate).where(Candidate.id == candidate_id))).scalar_one_or_none()
    if candidate is None or not candidate.raw_file_key:
        raise _not_found("candidate raw file")
    settings = get_settings()
    ttl = settings.RAW_FILE_PRESIGN_TTL_SECONDS
    try:
        url = await ResumeStorageService().presigned_get_url(candidate.raw_file_key, expires_seconds=ttl)
    except StorageError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "object_storage_unavailable", "message": "Resume storage is unavailable"}
        ) from exc
    trace_id = structlog.contextvars.get_contextvars().get("trace_id")
    db.add(AuditLog(event_type="raw_file_access", actor=f"user:{user.id}", target_type="candidate",
                    target_id=candidate_id, payload={"purpose": "raw_file_download", "trace_id": trace_id}))
    await db.commit()
    return RawFileLink(url=url, expires_in_seconds=ttl)
```

Note: `ResumeStorageService` must expose an async `presigned_get_url(key, *, expires_seconds)` wrapping `MinIOStorage.presigned_get_url`. If it does not yet, add it in this task:

```python
# in backend/app/services/storage/resume_storage.py, on ResumeStorageService
    async def presigned_get_url(self, key: str, *, expires_seconds: int) -> str:
        return await to_thread.run_sync(
            lambda: self.storage.presigned_get_url(key, expires_seconds=expires_seconds)
        )
```

Register both routers in `backend/app/main.py`: add `from backend.app.routers import candidates_read as candidates_read_router` and `from backend.app.routers import jds as jds_router` (jds created in Task 5) and `app.include_router(candidates_read_router.router)` / `app.include_router(jds_router.router)`. (Add the `jds_router` import/registration now only if Task 5 lands together; otherwise register `candidates_read_router` here and `jds_router` in Task 5.)

- [ ] **Step 4: Run the tests**

Run: `MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest backend/tests/integration/test_candidates_read_api.py -q`
Expected: PASS.

- [ ] **Step 5: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/routers/candidates_read.py backend/app/main.py backend/app/services/storage/resume_storage.py backend/tests/integration/test_candidates_read_api.py
git commit -m "feat(wp4): candidate list/detail/score/raw-file read endpoints"
```

---

## Task 5: JD read service and router (JD list/detail, rule-version list, diff)

**Files:**
- Create: `backend/app/services/read/jds.py`, `backend/app/routers/jds.py`
- Modify: `backend/app/main.py` (register `jds` router if not already)
- Test: `backend/tests/integration/test_jds_api.py`

**Interfaces:**
- Consumes: `diff_schemas` (Task 2), response models (Task 3), `page_params`, `require_roles`.
- Produces: `services/read/jds.py` with `async list_jds(db, status, page)`, `async get_jd_detail(db, code)`, `async list_rule_versions(db, code, page)`, `async rule_version_diff(db, code, from_version, to_version)`; router routes `GET /api/v1/jds`, `GET /api/v1/jds/{code}`, `GET /api/v1/jds/{code}/rule-versions`, `GET /api/v1/jds/{code}/rule-versions/{from_version}/diff/{to_version}`.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_jds_api.py
from datetime import datetime, timezone

import pytest

from backend.app.models import JD, RuleVersion

pytestmark = pytest.mark.integration


async def _seed_two_versions(db):
    jd = JD(code="QC", name="Quality", description="d", status="active")
    db.add(jd); await db.flush()
    v1 = RuleVersion(jd_id=jd.id, version="v1", published_at=datetime.now(timezone.utc),
                     schema_json={"passing_threshold": 40, "rule_dimensions": [{"id": "a", "weight": 10}]})
    v2 = RuleVersion(jd_id=jd.id, version="v2", published_at=datetime.now(timezone.utc),
                     schema_json={"passing_threshold": 50, "rule_dimensions": [{"id": "a", "weight": 20}]})
    db.add_all([v1, v2]); await db.flush()
    jd.active_rule_version_id = v2.id
    await db.commit()
    return jd


async def test_jd_list_and_detail(client, db_session, auth_headers):
    jd = await _seed_two_versions(db_session)
    lst = await client.get("/api/v1/jds", headers=await auth_headers("hr"))
    assert lst.status_code == 200 and any(i["code"] == "QC" for i in lst.json()["items"])
    detail = await client.get(f"/api/v1/jds/{jd.code}", headers=await auth_headers("hr"))
    assert detail.status_code == 200 and detail.json()["active_rule_version"]["version"] == "v2"


async def test_rule_versions_and_diff(client, db_session, auth_headers):
    jd = await _seed_two_versions(db_session)
    versions = await client.get(f"/api/v1/jds/{jd.code}/rule-versions", headers=await auth_headers("hr"))
    assert versions.status_code == 200 and versions.json()["total"] == 2
    diff = await client.get(f"/api/v1/jds/{jd.code}/rule-versions/v1/diff/v2", headers=await auth_headers("hr"))
    assert diff.status_code == 200
    paths = {(c["path"], c["kind"]) for c in diff.json()["changes"]}
    assert ("passing_threshold", "changed") in paths
    assert ("rule_dimensions[a]", "changed") in paths


async def test_unknown_jd_404(client, auth_headers):
    resp = await client.get("/api/v1/jds/NOPE", headers=await auth_headers("hr"))
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/tests/integration/test_jds_api.py -q`
Expected: FAIL (routes not registered).

- [ ] **Step 3: Implement the JD read service**

```python
# backend/app/services/read/jds.py
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, RuleVersion
from backend.app.schemas.read import (
    JDDetail, JDItem, RuleDiffResponse, RuleVersionItem,
)
from backend.app.services.read.pagination import Page
from backend.app.services.read.rule_diff import diff_schemas


async def _active_version(db: AsyncSession, jd: JD) -> RuleVersion | None:
    if not jd.active_rule_version_id:
        return None
    return (
        await db.execute(select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id))
    ).scalar_one_or_none()


async def list_jds(db: AsyncSession, status: str | None, page: Page) -> tuple[list[JDItem], int]:
    base = select(JD)
    if status is not None:
        base = base.where(JD.status == status)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    jds = (await db.execute(base.order_by(JD.code).offset(page.offset).limit(page.page_size))).scalars().all()
    items = []
    for jd in jds:
        active = await _active_version(db, jd)
        items.append(JDItem(code=jd.code, name=jd.name, status=jd.status,
                            active_rule_version=active.version if active else None))
    return items, total


async def get_jd_detail(db: AsyncSession, code: str) -> JDDetail | None:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        return None
    active = await _active_version(db, jd)
    return JDDetail(
        code=jd.code, name=jd.name, description=jd.description, status=jd.status,
        active_rule_version=(
            {"id": active.id, "version": active.version, "published_at": active.published_at.isoformat()}
            if active else None
        ),
    )


async def list_rule_versions(db: AsyncSession, code: str, page: Page) -> tuple[list[RuleVersionItem], int] | None:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        return None
    base = select(RuleVersion).where(RuleVersion.jd_id == jd.id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    versions = (
        await db.execute(base.order_by(RuleVersion.published_at.desc()).offset(page.offset).limit(page.page_size))
    ).scalars().all()
    items = [
        RuleVersionItem(
            id=v.id, version=v.version, published_at=v.published_at,
            published_by_user_id=v.published_by_user_id, notes=v.notes,
            golden_set_metrics=v.golden_set_metrics, is_active=(v.id == jd.active_rule_version_id),
        )
        for v in versions
    ]
    return items, total


async def rule_version_diff(
    db: AsyncSession, code: str, from_version: str, to_version: str
) -> RuleDiffResponse | None:
    jd = (await db.execute(select(JD).where(JD.code == code))).scalar_one_or_none()
    if jd is None:
        return None
    async def _load(version: str) -> RuleVersion | None:
        return (
            await db.execute(
                select(RuleVersion).where(RuleVersion.jd_id == jd.id, RuleVersion.version == version)
            )
        ).scalar_one_or_none()
    a, b = await _load(from_version), await _load(to_version)
    if a is None or b is None:
        return None
    changes = diff_schemas(a.schema_json, b.schema_json)
    return RuleDiffResponse(jd_code=code, from_version=from_version, to_version=to_version, changes=changes)
```

- [ ] **Step 4: Implement the JD router**

```python
# backend/app/routers/jds.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import User
from backend.app.schemas.read import JDDetail, JDList, RuleDiffResponse, RuleVersionList
from backend.app.services.read.jds import (
    get_jd_detail, list_jds, list_rule_versions, rule_version_diff,
)
from backend.app.services.read.pagination import Page, page_params

router = APIRouter(prefix="/api/v1/jds", tags=["jds"])
READ_ROLES = ("hr", "hr_lead", "admin")


def _not_found(resource: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "not_found", "message": f"{resource} not found"})


@router.get("", response_model=JDList)
async def jds(
    status: str | None = None, page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*READ_ROLES)),
) -> JDList:
    items, total = await list_jds(db, status, page)
    return JDList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get("/{code}", response_model=JDDetail)
async def jd_detail(code: str, db: AsyncSession = Depends(get_db),
                    _u: User = Depends(require_roles(*READ_ROLES))) -> JDDetail:
    detail = await get_jd_detail(db, code)
    if detail is None:
        raise _not_found("JD")
    return detail


@router.get("/{code}/rule-versions", response_model=RuleVersionList)
async def rule_versions(
    code: str, page: Page = Depends(page_params), db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> RuleVersionList:
    result = await list_rule_versions(db, code, page)
    if result is None:
        raise _not_found("JD")
    items, total = result
    return RuleVersionList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get("/{code}/rule-versions/{from_version}/diff/{to_version}", response_model=RuleDiffResponse)
async def rule_diff(
    code: str, from_version: str, to_version: str, db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> RuleDiffResponse:
    result = await rule_version_diff(db, code, from_version, to_version)
    if result is None:
        raise _not_found("JD or rule version")
    return result
```

Ensure `backend/app/main.py` registers `jds_router` (if not already in Task 4).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest backend/tests/integration/test_jds_api.py -q`
Expected: PASS.

- [ ] **Step 6: Ruff, mypy, commit**

```bash
uv run ruff check backend && uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add backend/app/services/read/jds.py backend/app/routers/jds.py backend/app/main.py backend/tests/integration/test_jds_api.py
git commit -m "feat(wp4): JD list/detail, rule-version list, and diff endpoints"
```

---

## Task 6: OpenAPI contract tests, docs, and WP4 exit review

**Files:**
- Create: `backend/tests/integration/test_openapi_contract.py`
- Modify: `README.md`, `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`, `docs/superpowers/plans/README.md`

- [ ] **Step 1: Write the OpenAPI contract test**

```python
# backend/tests/integration/test_openapi_contract.py
import pytest

pytestmark = pytest.mark.integration


async def test_openapi_lists_read_routes_with_models(client):
    spec = (await client.get("/openapi.json")).json()
    paths = spec["paths"]
    for route in [
        "/api/v1/jds/{code}/candidates",
        "/api/v1/candidates",
        "/api/v1/candidates/{candidate_id}",
        "/api/v1/candidates/{candidate_id}/scores/{score_id}",
        "/api/v1/candidates/{candidate_id}/raw-file",
        "/api/v1/jds",
        "/api/v1/jds/{code}",
        "/api/v1/jds/{code}/rule-versions",
        "/api/v1/jds/{code}/rule-versions/{from_version}/diff/{to_version}",
    ]:
        assert route in paths, f"missing {route}"
        assert "200" in paths[route]["get"]["responses"]
```

- [ ] **Step 2: Run it**

Run: `uv run pytest backend/tests/integration/test_openapi_contract.py -q`
Expected: PASS.

- [ ] **Step 3: Update README**

Document the WP4 read surface: the two candidate lists, candidate detail (audited PII), score detail with evidence, raw-file presigned download (audited), JD list/detail, rule-version list and diff, offset pagination, and that list endpoints never decrypt or audit.

- [ ] **Step 4: Full local matrix**

```bash
uv run pytest -m "not integration and not external_contract" -q
MINIO_ENDPOINT=127.0.0.1:9000 uv run pytest -m integration -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: offline and integration suites pass; Ruff and mypy clean.

- [ ] **Step 5: Update roadmap and plan index**

Mark WP4 In progress; update the traceability-matrix rows "Candidate query API", "Candidate list and scorecard", and "Rule editor and version workflow" (read/diff portion). Do NOT mark WP4 Complete or WP5 Ready until hosted CI passes.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/integration/test_openapi_contract.py README.md docs/
git commit -m "test(wp4): OpenAPI contract tests and read-API docs"
```

---

## Task 7: Push, hosted CI, and WP4 exit review

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin codex/wp4-read-apis
gh pr create --title "WP4: read APIs and rule visibility" --body "<summary + exit evidence>"
```

- [ ] **Step 2: Confirm hosted CI** — require `unit-and-static (3.10)`, `unit-and-static (3.14)`, and `integration` to pass.

- [ ] **Step 3: Record completion evidence** in this plan and the roadmap: exact commit range, hosted run URL, offline/integration counts.

- [ ] **Step 4: Mark WP4 Complete and WP5 Ready for planning** only after every offline and integration exit criterion and hosted CI pass.

---

## Self-Review

**Spec coverage:**
- §5.1 ranked list → Task 3/4. §5.2 flat list → Task 3/4. §5.3 candidate detail + audit → Task 3/4. §5.4 score detail → Task 3/4. §5.5 raw-file download → Task 4. §5.6 JD list/detail → Task 5. §5.7 rule-version list + diff → Task 2/5. §6 diff → Task 2. §7 read-service layer → Tasks 1/3/5. §8 errors/auth/leak → Tasks 4/5 (404/401/403/503, no-PII in lists). §9 config → Task 1. §10 tests → Tasks 1–6 (unit, integration, OpenAPI contract). §11 rollout / §12 exit → Tasks 6/7.
- Gap check: §5.3 says the pii_decrypt audit is committed with request completion — the read service commits it; acceptable for a read that intentionally writes one audit row.

**Placeholder scan:** none — every code step contains real code; the only conditional is Task 4's `ResumeStorageService.presigned_get_url` which is provided inline if missing.

**Type consistency:** response-model names (`RankedCandidateList`, `CandidateDetail`, `ScoreDetail`, `RawFileLink`, `JDList`, `JDDetail`, `RuleVersionList`, `RuleDiffResponse`) and service function names (`list_ranked_for_jd`, `list_candidates`, `get_candidate_detail`, `get_score_detail`, `list_jds`, `get_jd_detail`, `list_rule_versions`, `rule_version_diff`, `diff_schemas`) are used consistently across Tasks 2–5. `Page`/`page_params`/`PageMeta` from Task 1 are consumed by all list endpoints and every list response model inherits `PageMeta`.
