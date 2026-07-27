# WP8 MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose four content-free tools over the Model Context Protocol so a self-hosted Hermes agent can answer questions from a DingTalk group, with no path around REST authorization.

**Architecture:** An MCP server wraps existing WP4 and WP7 services. It is off by default. Its service identity maps to a role that fails `require_roles` on every PII-decrypting path, so "cannot reach candidate content" is a property of the authorization layer rather than of the tool list.

**Tech Stack:** Python 3.10–3.14, FastAPI, SQLAlchemy 2 async, Pydantic v2, `mcp` Python SDK.

## Global Constraints

- Authoritative design: `docs/superpowers/specs/2026-07-27-wp8-dingtalk-sync-and-mcp-design.md` §11.
- Work in `codex/wp8-mcp-server`. Never stage `.superpowers/` or `backend.zip`.
- TDD for every task: failing test, confirm the expected failure, minimal
  implementation, rerun, task gate, commit.
- Commit trailer: blank line then
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Python 3.10 is a supported target.** `from datetime import UTC` is 3.11+ and
  is forbidden; use `timezone.utc`.
- Integration commands need the prefix:
  `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000"`.
- Backend task gate: `uv run pytest -m "not integration and not external_contract" -q`,
  `uv run ruff check backend`,
  `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports`.
- Ruff line length is 100.
- Every new `Settings` field needs a matching `TEST_ENV_DEFAULTS` key.
- **No tool may return a candidate name, phone, email, ciphertext, object key,
  evidence quote, or reasoning string.** This is enforced three ways: the
  service layer does not read them, the service role cannot reach the routes
  that would, and every tool test asserts an exact field set.
- This plan adds no migration. The Alembic head stays at whatever the sync plan
  leaves it.

## File Structure

### Create

- `backend/app/mcp/__init__.py`
- `backend/app/mcp/identity.py` — the service role and its ceiling.
- `backend/app/mcp/tools.py` — the four tool implementations.
- `backend/app/mcp/server.py` — MCP server construction and registration.
- `backend/tests/unit/test_mcp_tools.py`
- `backend/tests/integration/test_mcp_authorization.py`

### Modify

- `backend/app/config.py`, `.env.example`, `backend/tests/test_bootstrap.py`.
- `backend/app/main.py` — mount the MCP app when enabled.
- `backend/app/deps.py` — recognize the service role (read-only).

---

## Task 1: The service identity and its ceiling

**Files:**
- Create: `backend/app/mcp/__init__.py` (empty)
- Create: `backend/app/mcp/identity.py`
- Modify: `backend/app/config.py`, `.env.example`, `backend/tests/test_bootstrap.py`
- Test: `backend/tests/integration/test_mcp_authorization.py`

**Interfaces:**
- Produces: settings `MCP_ENABLED: bool`, `MCP_SERVICE_ROLE: str`,
  `MCP_SERVICE_TOKEN: str`; and
  `async resolve_mcp_user(db, token: str) -> User` raising
  `McpUnauthorized` on a bad token.

- [ ] **Step 1: Write the failing ceiling test**

Create `backend/tests/integration/test_mcp_authorization.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.app.models import JD, Candidate, RuleVersion, Score, User
from backend.app.security.crypto import encrypt_pii
from backend.app.security.jwt import create_access_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)


async def _service_headers(db_session) -> dict[str, str]:
    from backend.app.config import get_settings

    user = User(
        dingtalk_userid=f"mcp-{uuid4().hex}",
        display_name="MCP Service",
        role=get_settings().MCP_SERVICE_ROLE,
    )
    db_session.add(user)
    await db_session.commit()
    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {"Authorization": f"Bearer {token}"}


async def _seed_score(db_session) -> tuple[Candidate, Score]:
    code = f"MCP_{uuid4().hex[:6]}"
    jd = JD(code=code, name=code, description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    version = RuleVersion(
        jd_id=jd.id, version="v1", published_at=NOW, schema_json={"jd_code": code}
    )
    db_session.add(version)
    await db_session.flush()
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("private-name"),
        pii_hash=uuid4().hex,
        extracted_json={},
    )
    db_session.add(candidate)
    await db_session.flush()
    score = Score(
        candidate_id=candidate.id,
        jd_id=jd.id,
        rule_version_id=version.id,
        total_score=70,
        grade="L1",
        hard_filter_result={},
        rule_dimensions={},
        judge_dimensions={
            "dimensions": [
                {
                    "id": "independence",
                    "tier": "high",
                    "score": 10,
                    "confidence": 0.9,
                    "evidence_quotes": ["quotable evidence"],
                    "reasoning": "private reasoning",
                }
            ]
        },
    )
    db_session.add(score)
    await db_session.flush()
    await db_session.commit()
    return candidate, score


async def test_the_service_role_cannot_reach_pii_routes(client, db_session):
    candidate, score = await _seed_score(db_session)
    headers = await _service_headers(db_session)

    # The guarantee is "cannot reach", not "not offered". Even calling the REST
    # routes directly with the service credential must fail.
    assert (
        await client.get(f"/api/v1/candidates/{candidate.id}", headers=headers)
    ).status_code == 403
    assert (
        await client.get(f"/api/v1/candidates/{candidate.id}/raw-file", headers=headers)
    ).status_code == 403
    assert (
        await client.get(
            f"/api/v1/candidates/{candidate.id}/scores/{score.id}", headers=headers
        )
    ).status_code == 403


async def test_the_service_role_can_reach_the_aggregate_routes(client, db_session):
    await _seed_score(db_session)
    headers = await _service_headers(db_session)

    assert (await client.get("/api/v1/jds", headers=headers)).status_code == 200
```

- [ ] **Step 2: Run the ceiling test and confirm failure**

```powershell
uv run pytest backend/tests/integration/test_mcp_authorization.py -q
```

Expected: FAIL — `MCP_SERVICE_ROLE` does not exist.

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`:

```python
    MCP_ENABLED: bool = False
    MCP_SERVICE_ROLE: str = "mcp_service"
    MCP_SERVICE_TOKEN: str = ""
```

Add the matching `TEST_ENV_DEFAULTS` entries:

```python
    "MCP_ENABLED": "false",
    "MCP_SERVICE_ROLE": "mcp_service",
    "MCP_SERVICE_TOKEN": "test-mcp-token",
```

and the same keys in `.env.example` under `# --- WP8 MCP ---`.

- [ ] **Step 4: Run the ceiling test and confirm it passes**

```powershell
uv run pytest backend/tests/integration/test_mcp_authorization.py -q
```

Expected: PASS. `mcp_service` is simply not in any route's `require_roles`
tuple, so the 403s come from the existing guard with no new code. If any
assertion fails, a route's role tuple is wider than the design allows — fix the
route, not the test.

- [ ] **Step 5: Add the token resolver**

Create `backend/app/mcp/identity.py`:

```python
from __future__ import annotations

import hmac

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.models import User


class McpUnauthorized(Exception):
    """The presented MCP token is absent or wrong."""


async def resolve_mcp_user(db: AsyncSession, token: str) -> User:
    """Map the shared MCP token to its service user.

    Compared with `hmac.compare_digest` so a wrong token cannot be recovered
    by timing. The returned user carries the ceiling role; every downstream
    check uses it exactly as it would a human's role.
    """
    settings = get_settings()
    expected = settings.MCP_SERVICE_TOKEN
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise McpUnauthorized("invalid mcp token")
    user = (
        await db.execute(select(User).where(User.role == settings.MCP_SERVICE_ROLE))
    ).scalars().first()
    if user is None:
        raise McpUnauthorized("mcp service user is not provisioned")
    return user
```

- [ ] **Step 6: Run the task gate**

```powershell
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -- backend/app/mcp/__init__.py backend/app/mcp/identity.py backend/app/config.py .env.example backend/tests/test_bootstrap.py backend/tests/integration/test_mcp_authorization.py
git commit -m "feat(wp8): add the mcp service identity ceiling"
```

---

## Task 2: The four tools

**Files:**
- Create: `backend/app/mcp/tools.py`
- Test: `backend/tests/unit/test_mcp_tools.py`, extend
  `backend/tests/integration/test_mcp_authorization.py`

**Interfaces:**
- Consumes: WP4 read services, WP7 `summarize`, Task 1's identity.
- Produces:
  - `async list_jds(db) -> list[dict]` with keys `{jd_code, name, active_rule_version}`
  - `async top_candidates(db, *, jd_code, n, days) -> list[dict]` with keys
    `{candidate_id, total_score, grade, scored_at}`
  - `async score_summary(db, *, score_id) -> dict` with keys
    `{score_id, jd_code, total_score, grade, hard_filter_rejected, dimensions}`
    where each dimension is exactly `{id, tier, score}`
  - `async operations_summary(db, *, window) -> dict`

- [ ] **Step 1: Write the failing projection test**

Create `backend/tests/unit/test_mcp_tools.py`:

```python
from __future__ import annotations

from backend.app.mcp.tools import project_dimensions


def test_a_dimension_projection_keeps_only_comparable_fields() -> None:
    persisted = {
        "dimensions": [
            {
                "id": "independence",
                "tier": "high",
                "score": 10,
                "confidence": 0.9,
                "evidence_quotes": ["quotable evidence"],
                "reasoning": "private reasoning",
                "suggested_interview_questions": ["a question"],
            }
        ]
    }

    (projected,) = project_dimensions(persisted)

    assert set(projected) == {"id", "tier", "score"}
    rendered = str(projected)
    assert "quotable" not in rendered
    assert "reasoning" not in rendered


def test_a_missing_dimension_payload_projects_to_nothing() -> None:
    assert project_dimensions(None) == []
    assert project_dimensions({}) == []


def test_a_malformed_entry_is_dropped_rather_than_partially_copied() -> None:
    assert project_dimensions({"dimensions": ["not-an-object", 3]}) == []
```

- [ ] **Step 2: Run the projection test and confirm failure**

Run: `uv run pytest backend/tests/unit/test_mcp_tools.py -q`

Expected: FAIL — `ModuleNotFoundError: backend.app.mcp.tools`.

- [ ] **Step 3: Implement the projection and the tools**

Create `backend/app/mcp/tools.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, RuleVersion, Score

WINDOWS = ("today", "7d", "30d")


def project_dimensions(payload: dict | None) -> list[dict[str, Any]]:
    """Reduce persisted judge dimensions to the three comparable fields.

    Quotes and reasoning are dropped here, at the boundary, so no tool can
    return them even by accident.
    """
    entries = (payload or {}).get("dimensions")
    if not isinstance(entries, list):
        return []
    return [
        {"id": entry.get("id"), "tier": entry.get("tier"), "score": entry.get("score")}
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]


async def list_jds(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(JD, RuleVersion.version).outerjoin(
                RuleVersion, RuleVersion.id == JD.active_rule_version_id
            )
        )
    ).all()
    return [
        {"jd_code": jd.code, "name": jd.name, "active_rule_version": version}
        for jd, version in rows
    ]


async def top_candidates(
    db: AsyncSession, *, jd_code: str, n: int = 10, days: int = 7
) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(Score)
            .join(JD, JD.id == Score.jd_id)
            .where(JD.code == jd_code, Score.created_at >= since)
            .order_by(Score.total_score.desc(), Score.id.desc())
            .limit(min(max(n, 1), 50))
        )
    ).scalars()
    return [
        {
            "candidate_id": score.candidate_id,
            "total_score": str(score.total_score),
            "grade": score.grade,
            "scored_at": score.created_at.isoformat(),
        }
        for score in rows
    ]


async def score_summary(db: AsyncSession, *, score_id: int) -> dict[str, Any] | None:
    row = (
        await db.execute(
            select(Score, JD.code).join(JD, JD.id == Score.jd_id).where(Score.id == score_id)
        )
    ).first()
    if row is None:
        return None
    score, jd_code = row
    return {
        "score_id": score.id,
        "jd_code": jd_code,
        "total_score": str(score.total_score),
        "grade": score.grade,
        "hard_filter_rejected": bool((score.hard_filter_result or {}).get("rejected")),
        "dimensions": project_dimensions(score.judge_dimensions),
    }


async def operations_summary(db: AsyncSession, *, window: str = "7d") -> dict[str, Any]:
    from backend.app.services.operations.reporting import summarize

    if window not in WINDOWS:
        raise ValueError(f"unsupported window: {window}")
    summary = await summarize(db, window=window, now=datetime.now(timezone.utc))
    return {
        "window": summary.window,
        "known_cost_cny": str(summary.current.known_cost_cny),
        "attempt_count": summary.current.attempt_count,
        "budgets": [
            {"scope": b.scope, "state": b.state, "spend_cny": str(b.spend_cny)}
            for b in summary.budgets
        ],
    }
```

- [ ] **Step 4: Run the projection test and confirm it passes**

Run: `uv run pytest backend/tests/unit/test_mcp_tools.py -q`

Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing content-blacklist test**

Append to `backend/tests/integration/test_mcp_authorization.py`:

```python
async def test_score_summary_returns_an_exact_field_set(db_session):
    from backend.app.mcp.tools import score_summary

    _candidate, score = await _seed_score(db_session)

    summary = await score_summary(db_session, score_id=score.id)

    assert summary is not None
    # An exact set, not a substring sweep: WP7 learned that scanning for
    # "resume" false-positives on the legitimate value "resume_judge_v1".
    assert set(summary) == {
        "score_id",
        "jd_code",
        "total_score",
        "grade",
        "hard_filter_rejected",
        "dimensions",
    }
    assert set(summary["dimensions"][0]) == {"id", "tier", "score"}
    rendered = str(summary)
    for forbidden in ("quotable evidence", "private reasoning", "private-name"):
        assert forbidden not in rendered


async def test_top_candidates_exposes_ids_not_identities(db_session):
    from backend.app.mcp.tools import top_candidates

    _candidate, score = await _seed_score(db_session)
    jd_code = (await score_summary_jd(db_session, score.id))

    rows = await top_candidates(db_session, jd_code=jd_code, n=10, days=7)

    assert rows
    assert set(rows[0]) == {"candidate_id", "total_score", "grade", "scored_at"}


async def score_summary_jd(db_session, score_id: int) -> str:
    from backend.app.mcp.tools import score_summary

    summary = await score_summary(db_session, score_id=score_id)
    assert summary is not None
    return str(summary["jd_code"])
```

- [ ] **Step 6: Run the blacklist test**

```powershell
uv run pytest backend/tests/integration/test_mcp_authorization.py -q
```

Expected: PASS (4 tests).

- [ ] **Step 7: Run the task gate**

```powershell
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add -- backend/app/mcp/tools.py backend/tests/unit/test_mcp_tools.py backend/tests/integration/test_mcp_authorization.py
git commit -m "feat(wp8): add content-free mcp tools"
```

---

## Task 3: Server registration behind the kill switch

**Files:**
- Create: `backend/app/mcp/server.py`
- Modify: `backend/app/main.py`
- Test: extend `backend/tests/integration/test_mcp_authorization.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `build_mcp_app() -> Starlette` mounted at `/mcp` when
  `MCP_ENABLED` is true.

- [ ] **Step 1: Write the failing kill-switch test**

Append to `backend/tests/integration/test_mcp_authorization.py`:

```python
async def test_mcp_is_not_mounted_while_disabled(client):
    from backend.app.config import get_settings

    assert get_settings().MCP_ENABLED is False

    # Disabled must mean absent, not merely guarded: an unmounted route cannot
    # be reached by a misconfigured proxy either.
    response = await client.post("/mcp", json={})

    assert response.status_code == 404
```

- [ ] **Step 2: Run the kill-switch test and confirm failure**

```powershell
uv run pytest backend/tests/integration/test_mcp_authorization.py -q -k mounted
```

Expected: FAIL if `/mcp` is mounted unconditionally; PASS trivially before any
mounting exists — in that case implement Step 3 first, watch this test go red,
then make it green by adding the condition.

- [ ] **Step 3: Implement the server and conditional mount**

Create `backend/app/mcp/server.py`:

```python
from __future__ import annotations

from typing import Any

from mcp.server import Server
from mcp.server.sse import SseServerTransport

from backend.app.database import AsyncSessionLocal
from backend.app.mcp import tools


def build_mcp_server() -> Server:
    """Register the four content-free tools.

    Each handler opens its own short session. Nothing here decrypts, scores, or
    spends money.
    """
    server: Server = Server("smartscreen")

    @server.call_tool()
    async def call(name: str, arguments: dict[str, Any]) -> Any:
        async with AsyncSessionLocal() as db:
            if name == "list_jds":
                return await tools.list_jds(db)
            if name == "top_candidates":
                return await tools.top_candidates(
                    db,
                    jd_code=str(arguments["jd_code"]),
                    n=int(arguments.get("n", 10)),
                    days=int(arguments.get("days", 7)),
                )
            if name == "score_summary":
                return await tools.score_summary(db, score_id=int(arguments["score_id"]))
            if name == "operations_summary":
                return await tools.operations_summary(
                    db, window=str(arguments.get("window", "7d"))
                )
            raise ValueError(f"unknown tool: {name}")

    return server
```

In `backend/app/main.py`, inside `create_app()`, after the other routers:

```python
    if settings.MCP_ENABLED:
        from backend.app.mcp.server import build_mcp_app

        app.mount("/mcp", build_mcp_app())
```

Add `build_mcp_app()` to `server.py` returning the SSE ASGI app wrapping
`build_mcp_server()`, with a dependency that calls `resolve_mcp_user` on the
`Authorization` bearer and returns 401 on `McpUnauthorized`.

**Note for the implementer:** confirm the `mcp` package is a declared
dependency in `pyproject.toml`. If it is not, add it under the main
dependencies and run `uv lock` — CI uses `uv sync --locked` and will fail on a
stale lockfile.

- [ ] **Step 4: Run the kill-switch test and confirm it passes**

```powershell
uv run pytest backend/tests/integration/test_mcp_authorization.py -q
```

Expected: PASS (5 tests).

- [ ] **Step 5: Run the full gate including Python 3.10**

```powershell
uv run pytest -m "not integration and not external_contract" -q
uv run --python 3.10 --extra dev pytest -m "not integration and not external_contract" -q
uv run pytest -m integration -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 6: Update documentation**

Add an `## WP8 — MCP 会话式访问` section to `README.md` covering: the kill
switch, the four tools, why the surface is content-free, that the service role
is refused by every PII route, and that end-user identity passthrough is
deferred pending confirmation of Hermes' capability (design §16.2).

- [ ] **Step 7: Commit**

```bash
git add -- backend/app/mcp/server.py backend/app/main.py backend/tests/integration/test_mcp_authorization.py README.md pyproject.toml uv.lock
git commit -m "feat(wp8): mount the mcp server behind a kill switch"
```
