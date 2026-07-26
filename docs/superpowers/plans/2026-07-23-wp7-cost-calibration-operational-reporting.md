# WP7 Cost, Calibration, and Operational Reporting Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add attempt-level LLM cost accounting, non-blocking budget alerts, immutable quality releases, deterministic rejection analysis, recoverable cross-engine checks, and an accessible operations-and-quality workspace.

**Architecture:** Keep a modular monolith in FastAPI/PostgreSQL/Celery. `LLMGateway` delegates all non-content accounting to an independent-session `UsageRecorder`; reporting reads the append-only ledger; quality releases atomically bind a content-addressed golden snapshot to exact active rule versions; durable cross-check rows drive Celery and project only the current result onto `Score`. The Next.js application adopts a grouped responsive shell and four focused operational work surfaces.

**Tech Stack:** Python 3.10–3.14, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL, Pydantic v2, Celery/Redis, OpenAI-compatible async client; Next.js 15 App Router, React 19, TypeScript, Tailwind 4, Base UI-based shadcn, TanStack Query, Zod, Lucide, Vitest, Playwright.

---

## Global Constraints

- Authoritative design: `docs/superpowers/specs/2026-07-23-wp7-cost-calibration-operational-reporting-design.md`.
- Work in `codex/wp7-cost-quality-operations`. Do not push or create a PR. Never stage `.superpowers/` or `backend.zip`.
- TDD for every task: add a failing test, run the narrow test and confirm the expected failure, implement minimally, rerun narrow tests, run the task gate, then commit.
- Every commit message below is exact. Add a blank line and this trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Backend integration commands on this host require:
  `DATABASE_URL="postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" DATABASE_URL_SYNC="postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test" MINIO_ENDPOINT="127.0.0.1:9000"`.
- If the test stack is stopped, from the repository root run:
  `SMARTSCREEN_TEST_PG_PORT=25432 SMARTSCREEN_TEST_MINIO_PORT=9000 SMARTSCREEN_TEST_MINIO_CONSOLE_PORT=9001 docker compose -f docker-compose.test.yml up -d`.
- Backend task gate:
  `uv run pytest -m "not integration and not external_contract" -q`,
  `uv run ruff check backend`, and
  `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports`.
- Integration task gate uses the prefix above and the task's named test file(s).
- Frontend task gate from `frontend/`:
  `npm run lint`, `npm run typecheck`, `npm run test`.
- Final frontend gate also runs `npm run e2e` (allow the build/start webServer about
  180 seconds) and `npm run build`.
- Ruff line length is 100; use `.encode()` rather than `.encode("utf-8")`.
- FastAPI errors use `HTTPException(detail={"code": ..., "message": ...})`.
  Candidate-content-free responses may contain IDs and authorized staff
  `{user_id, display_name}`, but never candidate names, ciphertext, object keys,
  prompts, resume text, evidence quotes, or reasoning.
- Money uses `Decimal`; accepted configured rates have at most six fractional
  digits; per-attempt cost is quantized to 12 digits with `ROUND_HALF_UP`.
- All metric/report windows are timezone-aware half-open `[start, end)` ranges.
  Store UTC; compute calendar periods in `Asia/Shanghai`.
- Budget warning/exceeded states and audit events never block an LLM call.
  Missing price or inability to create the pre-call ledger row does block the
  provider call.
- Terminal `llm_usage_attempts` accounting fields are immutable. A new Score is
  correlated through `call_group_id`/`Score.llm_judge_call_group_id`, not a
  post-terminal ledger mutation.
- `Score.cost_tokens`/`cost_cny` remain compatibility fields and are not report
  sources.
- Cross-check output never replaces the primary total/grade and never persists
  secondary reasoning/evidence.
- Base UI, not Radix: `<Button>` uses `onClick`/`render`, never `asChild`.
  Cookie-reading server components export `dynamic = "force-dynamic"`; Next 15
  dynamic route params are awaited.

## File Structure

### Backend — create

- `backend/app/models/llm_usage.py` — usage attempts and budget reconciliation cursors.
- `backend/app/models/cross_check.py` — durable cross-engine queue/result.
- `backend/app/models/quality_release.py` — golden snapshots and immutable releases.
- `backend/app/services/llm/pricing.py` — validated rate book and Decimal cost.
- `backend/app/services/llm/usage.py` — immutable call context and independent-session recorder.
- `backend/app/services/operations/budgets.py` — budget windows/state/audit reconciliation.
- `backend/app/services/operations/reporting.py` — usage summary and detail queries.
- `backend/app/services/quality/metrics.py` — classification, evidence, confidence, agreement, trend functions.
- `backend/app/services/quality/releases.py` — preview/fingerprint/snapshot/create/read.
- `backend/app/services/quality/batch.py` — deterministic rejection aggregates.
- `backend/app/services/cross_check/sampling.py` — exact hash and risk triggers.
- `backend/app/services/cross_check/state.py` — idempotent ensure/claim/finalize/sweep.
- `backend/app/services/cross_check/worker.py` — sanitized secondary computation.
- `backend/app/schemas/operations.py`, `quality.py`, `batch_report.py`,
  `cross_check.py` — Pydantic API contracts.
- `backend/app/routers/operations.py`, `quality.py`, `batch_report.py`,
  `cross_check.py` — role-gated HTTP surfaces.
- `backend/app/tasks/wp7.py` — cross-check worker and shared WP7 sweeper tasks.
- Backend unit/integration tests named in each task.

### Backend — modify

- `backend/app/config.py`, `.env.example`, `backend/tests/test_bootstrap.py` — WP7 settings/prices.
- `backend/app/models/__init__.py`, `score.py` — exports, judge call-group projection.
- `backend/app/rules/schema.py` — finite nonnegative dimension weights.
- `backend/app/services/llm/{schemas,gateway,errors}.py` — metered attempts and optional secondary override.
- `backend/app/services/parser/extractor.py`, `backend/app/scoring/llm_judge.py`,
  `pipeline.py`, `backend/app/tasks/ingest.py`, `backend/app/routers/candidates.py`
  — propagate immutable call context.
- `backend/app/services/{feedback,golden_set}.py` and their routers — caller-owned
  commit plus atomic cross-check trigger.
- `backend/app/services/read/candidates.py`, `backend/app/routers/candidates_read.py`
  — audited score-detail read.
- `backend/app/tasks/celery_app.py`, `backend/app/main.py` — task/router registration.
- `backend/tests/integration/test_db_migrations.py`, `scripts/verify.py` — new head.

### Frontend — create

- `frontend/src/components/app-sidebar.tsx`, `mobile-app-nav.tsx` — responsive grouped navigation.
- `frontend/src/components/operations/{metric-rail,budget-status,cost-trend,usage-ledger}.tsx`.
- `frontend/src/components/quality/{quality-release-view,release-sheet,confidence-bins}.tsx`.
- `frontend/src/components/batch-report-view.tsx`,
  `frontend/src/components/cross-check-view.tsx`.
- `frontend/src/components/ui/sheet.tsx`.
- Routes: `frontend/src/app/(app)/reports/{operations,quality,batch,cross-checks}/page.tsx`.
- Component tests and `frontend/e2e/wp7-operations.spec.ts`,
  `frontend/e2e/wp7-quality.spec.ts`.

### Frontend — modify

- `frontend/src/components/app-shell.tsx`, `frontend/src/app/globals.css`.
- `frontend/src/lib/schemas.ts`.
- `frontend/e2e/fixtures/stub-backend.ts`, `frontend/e2e/a11y.spec.ts`.

### Documentation — modify

- `README.md`.
- `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`.

---

## Chunk 1: Persistent Usage Ledger Foundation

### Task 1: WP7 persistence, configuration, and rule-weight guard

**Files:**
- Create: `migrations/versions/7d3c9b1a4e62_wp7_operational_quality_tables.py`
- Create: `backend/app/models/llm_usage.py`
- Create: `backend/app/models/cross_check.py`
- Create: `backend/app/models/quality_release.py`
- Modify: `backend/app/models/score.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Modify: `backend/tests/test_bootstrap.py`
- Modify: `backend/app/rules/schema.py`
- Modify: `backend/tests/unit/test_models.py`
- Modify: `backend/tests/unit/test_rule_schema.py`
- Create: `backend/tests/unit/test_wp7_config.py`
- Modify: `backend/tests/integration/test_db_migrations.py`
- Modify: `backend/tests/integration/conftest.py`
- Modify: `scripts/verify.py`

**Produces:** all WP7 tables/constraints/indexes; nullable indexed (not unique)
`Score.llm_judge_call_group_id`; validated WP7 settings; dimension weights
finite and nonnegative.

- [ ] **Step 1: Write the failing ORM contract test**

Add to `backend/tests/unit/test_models.py`:

```python
from backend.app.models import (
    GoldenSetSnapshot,
    GoldenSetSnapshotEntry,
    LLMUsageAttempt,
    OperationsReconciliationState,
    QualityRelease,
    QualityReleaseJD,
    ScoreCrossCheck,
)


def test_wp7_models_expose_required_columns() -> None:
    assert {
        "call_group_id", "trace_id", "ingestion_job_id", "score_id", "jd_id",
        "rule_version_id", "operation", "attempt_role", "requested_model",
        "actual_model", "prompt_version", "status", "input_tokens",
        "output_tokens", "input_price_cny_per_million",
        "output_price_cny_per_million", "estimated_cost_cny", "latency_ms",
        "error_code", "started_at", "finished_at",
    } <= set(LLMUsageAttempt.__table__.columns.keys())
    assert {"key", "next_period_start", "updated_at"} <= set(
        OperationsReconciliationState.__table__.columns.keys()
    )
    assert {"lease_token", "sample_reasons", "secondary_dimensions"} <= set(
        ScoreCrossCheck.__table__.columns.keys()
    )
    assert GoldenSetSnapshotEntry.__table__.c.snapshot_id.foreign_keys
    assert QualityReleaseJD.__table__.c.quality_release_id.foreign_keys
    assert "llm_judge_call_group_id" in Score.__table__.columns
```

- [ ] **Step 2: Run the ORM test and confirm failure**

Run: `uv run pytest backend/tests/unit/test_models.py -q`

Expected: collection fails because the WP7 model exports do not exist.

- [ ] **Step 3: Add the three focused model files**

Implement these table contracts exactly:

```python
# backend/app/models/llm_usage.py
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class LLMUsageAttempt(Base):
    __tablename__ = "llm_usage_attempts"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('extract','judge','cross_check','lightweight')",
            name="ck_llm_usage_operation",
        ),
        CheckConstraint(
            "attempt_role IN ('primary','fallback','secondary')",
            name="ck_llm_usage_attempt_role",
        ),
        CheckConstraint(
            "status IN ('pending','succeeded','unavailable','invalid_response',"
            "'configuration_error','abandoned')",
            name="ck_llm_usage_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND finished_at IS NULL) OR "
            "(status <> 'pending' AND finished_at IS NOT NULL)",
            name="ck_llm_usage_terminal_time",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_llm_usage_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_llm_usage_output_tokens",
        ),
        CheckConstraint(
            "input_price_cny_per_million >= 0 AND output_price_cny_per_million >= 0",
            name="ck_llm_usage_prices",
        ),
        CheckConstraint(
            "estimated_cost_cny IS NULL OR estimated_cost_cny >= 0",
            name="ck_llm_usage_cost",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_llm_usage_latency",
        ),
        Index("ix_llm_usage_started_id", "started_at", "id"),
        Index("ix_llm_usage_status_started", "status", "started_at"),
        Index("ix_llm_usage_jd_rule_started", "jd_id", "rule_version_id", "started_at"),
        Index("ix_llm_usage_operation_started", "operation", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_group_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ingestion_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_jobs.id"), index=True
    )
    score_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("scores.id"), index=True)
    jd_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("jds.id"), index=True)
    rule_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("rule_versions.id"), index=True
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_role: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(128), nullable=False)
    actual_model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    input_price_cny_per_million: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    output_price_cny_per_million: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    estimated_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationsReconciliationState(Base):
    __tablename__ = "operations_reconciliation_state"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    next_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

```python
# backend/app/models/cross_check.py
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class ScoreCrossCheck(Base, TimestampMixin):
    __tablename__ = "score_cross_checks"
    __table_args__ = (
        UniqueConstraint(
            "score_id", "secondary_model", "prompt_version",
            name="uq_cross_checks_score_model_prompt",
        ),
        CheckConstraint(
            "state IN ('queued','running','completed','retryable_failed','terminal_failed')",
            name="ck_cross_checks_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_cross_checks_attempts"),
        Index("ix_cross_checks_state_lease", "state", "lease_expires_at"),
        Index("ix_cross_checks_score_id_id", "score_id", "id"),
        Index("ix_cross_checks_completed_diff", "completed_at", "absolute_diff"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scores.id"), nullable=False)
    secondary_model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    secondary_total_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    secondary_dimensions: Mapped[list | None] = mapped_column(JSONB)
    absolute_diff: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    threshold_snapshot: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`backend/app/models/quality_release.py` contains the four classes and exact
constraints from design §6.3–6.4:

```python
class GoldenSetSnapshot(Base):
    __tablename__ = "golden_set_snapshots"
    __table_args__ = (
        CheckConstraint("item_count >= 0", name="ck_golden_snapshot_item_count"),
        UniqueConstraint(
            "content_sha256", name="uq_golden_snapshots_content_sha256"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GoldenSetSnapshotEntry(Base):
    __tablename__ = "golden_set_snapshot_entries"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "candidate_id", "jd_id",
            name="uq_golden_snapshot_candidate_jd",
        ),
        CheckConstraint(
            "label IN ('advance','reject','borderline')",
            name="ck_golden_snapshot_label",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("golden_set_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("candidates.id"), nullable=False)
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)


class QualityRelease(Base):
    __tablename__ = "quality_releases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('meets_target','below_target')",
            name="ck_quality_release_status",
        ),
        Index("ix_quality_release_created_id", "created_at", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    golden_snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("golden_set_snapshots.id"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    targets_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QualityReleaseJD(Base):
    __tablename__ = "quality_release_jds"
    __table_args__ = (
        UniqueConstraint(
            "quality_release_id", "jd_id", name="uq_quality_release_jd"
        ),
        Index("ix_quality_release_jds_jd_release", "jd_id", "quality_release_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    quality_release_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quality_releases.id", ondelete="CASCADE"), nullable=False
    )
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False)
    rule_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rule_versions.id"), nullable=False
    )
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
```

Use explicit imports, line-wrap at 100, export every class from
`backend/app/models/__init__.py`, and add to `Score`:

```python
from uuid import UUID
from sqlalchemy.dialects.postgresql import UUID as PGUUID

llm_judge_call_group_id: Mapped[UUID | None] = mapped_column(
    PGUUID(as_uuid=True), index=True
)
```

- [ ] **Step 4: Verify and commit the ORM layer**

```powershell
uv run pytest backend/tests/unit/test_models.py -q
uv run ruff check backend/app/models backend/tests/unit/test_models.py
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/models/__init__.py backend/app/models/score.py backend/app/models/llm_usage.py backend/app/models/cross_check.py backend/app/models/quality_release.py backend/tests/unit/test_models.py
git commit -m "feat(wp7): add operations quality models"
```

Expected: tests, ruff, and mypy pass; commit succeeds with only the listed
files.

- [ ] **Step 5: Write failing configuration and rule-schema tests**

In `backend/tests/unit/test_rule_schema.py`, add `_rule_payload()` using
`FIXTURE.read_text(encoding="utf-8")`, import `RuleDimension`, and test:

```python
@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), True, False])
def test_dimension_weight_must_be_finite_nonnegative_and_not_bool(bad: object) -> None:
    dimension = _rule_payload()["rule_dimensions"][0]
    dimension["weight"] = bad
    with pytest.raises(ValidationError):
        RuleDimension.model_validate(dimension)


def test_zero_weight_remains_valid() -> None:
    payload = _rule_payload()
    payload["rule_dimensions"][0]["weight"] = 0
    payload["total_score"] = sum(
        d["weight"] for d in payload["rule_dimensions"] + payload["judge_dimensions"]
    )
    assert RuleSchema.model_validate(payload).rule_dimensions[0].weight == 0
```

Create `backend/tests/unit/test_wp7_config.py` with the default assertions from
design §16 and a parametrized `ValidationError` test for: warn ratio `0`,
F1 `1.1`, minimum bucket `0`, sample percent `101`, lease `600`, max attempts
`0`, diff threshold `inf`, warn ratio `NaN`, daily budget `-1`, and monthly
budget `inf`. Every environment-mutating test clears `get_settings` before
loading and in `finally`. Also add the inequality red test now:

```python
def test_enabled_secondary_must_differ_from_primary(monkeypatch) -> None:
    monkeypatch.setenv("CROSS_ENGINE_MODEL", "test-judge")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="secondary"):
            get_settings()
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 6: Run configuration/rule tests and confirm failure**

Run:
`uv run pytest backend/tests/unit/test_rule_schema.py backend/tests/unit/test_wp7_config.py -q`

Expected: FAIL because WP7 settings do not exist, invalid weights are accepted,
and the secondary model is not compared with the primary judge model.

- [ ] **Step 7: Add settings/test values and harden weights**

Add every setting from design §16 to `Settings`, including the JSON rate-book
string. Use these exact types/bounds; Task 2 adds rate-book membership
validation after the parser exists:

```python
LLM_PRICE_CNY_PER_MILLION_JSON: str
LLM_BUDGET_WARN_RATIO: float = Field(default=0.80, gt=0, le=1, allow_inf_nan=False)
LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN: int = Field(default=31, ge=1, le=366)
LLM_USAGE_PENDING_TIMEOUT_SECONDS: int = Field(default=600, ge=1)
LLM_USAGE_FINALIZE_MAX_RETRIES: int = Field(default=3, ge=1, le=10)
QUALITY_F1_TARGET: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
QUALITY_EVIDENCE_COVERAGE_TARGET: float = Field(
    default=0.95, ge=0, le=1, allow_inf_nan=False
)
QUALITY_CONFIDENCE_MIN_BUCKET_SIZE: int = Field(default=10, ge=1)
CROSS_ENGINE_MODEL: str = ""
CROSS_ENGINE_SAMPLE_PERCENT: int = Field(default=10, ge=0, le=100)
CROSS_ENGINE_LOW_CONFIDENCE: float = Field(
    default=0.60, ge=0, le=1, allow_inf_nan=False
)
CROSS_ENGINE_DIFF_THRESHOLD: float = Field(default=10, ge=0, allow_inf_nan=False)
CROSS_ENGINE_MAX_ATTEMPTS: int = Field(default=3, ge=1)
CROSS_ENGINE_LEASE_SECONDS: int = Field(default=900, gt=600)
CROSS_ENGINE_SWEEP_INTERVAL_SECONDS: int = Field(default=60, ge=1)
CROSS_ENGINE_BACKFILL_MAX: int = Field(default=500, ge=1)
```

Add a model validator for secondary-model inequality when enabled.
Also tighten the existing budget fields:

```python
DAILY_LLM_BUDGET_CNY: float = Field(default=100.0, ge=0, allow_inf_nan=False)
MONTHLY_LLM_BUDGET_CNY: float = Field(default=1500.0, ge=0, allow_inf_nan=False)
```

Set deterministic test values:

```python
"LLM_PRICE_CNY_PER_MILLION_JSON": (
    '{"test-extract":{"input":1.000000,"output":2.000000},'
    '"test-extract-fallback":{"input":1.000000,"output":2.000000},'
    '"test-judge":{"input":1.000000,"output":2.000000},'
    '"test-judge-fallback":{"input":1.000000,"output":2.000000},'
    '"test-light":{"input":1.000000,"output":2.000000},'
    '"test-secondary":{"input":1.000000,"output":2.000000}}'
),
"LLM_BUDGET_WARN_RATIO": "0.8",
"LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN": "31",
"LLM_USAGE_PENDING_TIMEOUT_SECONDS": "600",
"LLM_USAGE_FINALIZE_MAX_RETRIES": "3",
"QUALITY_F1_TARGET": "0.75",
"QUALITY_EVIDENCE_COVERAGE_TARGET": "0.95",
"QUALITY_CONFIDENCE_MIN_BUCKET_SIZE": "10",
"CROSS_ENGINE_MODEL": "test-secondary",
"CROSS_ENGINE_SAMPLE_PERCENT": "10",
"CROSS_ENGINE_LOW_CONFIDENCE": "0.6",
"CROSS_ENGINE_DIFF_THRESHOLD": "10",
"CROSS_ENGINE_MAX_ATTEMPTS": "3",
"CROSS_ENGINE_LEASE_SECONDS": "900",
"CROSS_ENGINE_SWEEP_INTERVAL_SECONDS": "60",
"CROSS_ENGINE_BACKFILL_MAX": "500",
```

In `rules/schema.py`, use a shared alias with a pre-validator so
bool/non-finite/negative are rejected:

```python
from typing import Annotated
from pydantic import BeforeValidator, Field


def _reject_boolean_weight(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("dimension weight must be numeric, not boolean")
    return value

DimensionWeight = Annotated[
    float,
    BeforeValidator(_reject_boolean_weight),
    Field(ge=0, allow_inf_nan=False),
]
```

Replace only the existing `weight: float` annotation in both
`RuleDimension` and `JudgeDimension` with `weight: DimensionWeight`; retain
every other field and validator.

- [ ] **Step 8: Verify and commit configuration validation**

```powershell
uv run pytest backend/tests/unit/test_rule_schema.py backend/tests/unit/test_wp7_config.py backend/tests/test_bootstrap.py -q
uv run ruff check backend/app/config.py backend/app/rules/schema.py backend/tests/test_bootstrap.py backend/tests/unit/test_rule_schema.py backend/tests/unit/test_wp7_config.py
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- .env.example backend/app/config.py backend/app/rules/schema.py backend/tests/test_bootstrap.py backend/tests/unit/test_rule_schema.py backend/tests/unit/test_wp7_config.py
git commit -m "feat(wp7): validate operations configuration"
```

Expected: tests, ruff, and mypy pass; commit contains only the listed files.

- [ ] **Step 9: Write the failing migration contract and head assertions**

Set `WP3_HEAD_REVISION = "7d3c9b1a4e62"` in
`backend/tests/integration/test_db_migrations.py` and
`HEAD_REVISION = "7d3c9b1a4e62"` in `scripts/verify.py`.

Before the migration exists, extend `test_alembic_round_trip_from_base` to query
`information_schema.tables`, `information_schema.columns`, `pg_constraint`,
and `pg_indexes`. After upgrade assert:

```python
assert {
    "llm_usage_attempts", "operations_reconciliation_state",
    "score_cross_checks", "golden_set_snapshots",
    "golden_set_snapshot_entries", "quality_releases", "quality_release_jds",
} <= tables
assert score_columns["llm_judge_call_group_id"] == "YES"
assert {
    "ck_llm_usage_operation", "ck_llm_usage_attempt_role",
    "ck_llm_usage_status", "ck_llm_usage_terminal_time",
    "ck_llm_usage_input_tokens", "ck_llm_usage_output_tokens",
    "ck_llm_usage_prices", "ck_llm_usage_cost", "ck_llm_usage_latency",
    "uq_cross_checks_score_model_prompt", "ck_cross_checks_state",
    "ck_cross_checks_attempts", "ck_golden_snapshot_item_count",
    "uq_golden_snapshots_content_sha256",
    "uq_golden_snapshot_candidate_jd", "ck_golden_snapshot_label",
    "ck_quality_release_status", "uq_quality_release_jd",
} <= constraints
assert {
    "ix_llm_usage_started_id", "ix_llm_usage_status_started",
    "ix_llm_usage_jd_rule_started", "ix_llm_usage_operation_started",
    "ix_llm_usage_attempts_call_group_id", "ix_llm_usage_attempts_trace_id",
    "ix_llm_usage_attempts_ingestion_job_id", "ix_llm_usage_attempts_score_id",
    "ix_llm_usage_attempts_jd_id", "ix_llm_usage_attempts_rule_version_id",
    "ix_cross_checks_state_lease", "ix_cross_checks_score_id_id",
    "ix_cross_checks_completed_diff", "ix_quality_release_created_id",
    "ix_quality_release_jds_jd_release",
    "ix_scores_llm_judge_call_group_id",
} <= indexes
```

Then:

1. downgrade to `25954dc70368`;
2. assert every listed WP7 table/constraint/index and Score call-group column is
   gone while WP6c status still exists;
3. continue the existing downgrade to `f412481450cf` and its WP6c assertions.

Prepend these WP7 children to `_CLEAN_TABLES` in
`backend/tests/integration/conftest.py` so tests with null job/score links do
not leak rows:

```python
"quality_release_jds",
"quality_releases",
"golden_set_snapshot_entries",
"golden_set_snapshots",
"score_cross_checks",
"llm_usage_attempts",
"operations_reconciliation_state",
```

- [ ] **Step 10: Run the migration test and confirm failure**

Run with the explicit 25432 environment:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_db_migrations.py -q
```

Expected: FAIL because Alembic head `7d3c9b1a4e62` and its schema do not exist.

- [ ] **Step 11: Generate and fill the migration**

Run:
`uv run alembic revision --rev-id 7d3c9b1a4e62 -m "wp7 operational quality tables"`
(never `--autogenerate`). Confirm `down_revision = "25954dc70368"` and the
exact generated filename.

The upgrade creates in FK order:
`llm_usage_attempts`, `operations_reconciliation_state`,
`score_cross_checks`, `golden_set_snapshots`,
`golden_set_snapshot_entries`, `quality_releases`,
`quality_release_jds`, then adds `scores.llm_judge_call_group_id` and its
non-unique index. Create the six single-column ledger indexes and every
model CHECK/unique/index name asserted above explicitly.
The downgrade drops the score index/column, then tables in reverse FK order.
Keep `import sqlalchemy as sa`; it is used.

- [ ] **Step 12: Run migration and static gates**

Run:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_db_migrations.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass; Alembic current output contains `7d3c9b1a4e62`.

- [ ] **Step 13: Commit the migration**

Stage only the Task 1 files and the single generated migration:

```powershell
git add -- backend/tests/integration/conftest.py backend/tests/integration/test_db_migrations.py scripts/verify.py migrations/versions/7d3c9b1a4e62_wp7_operational_quality_tables.py
git commit -m "feat(wp7): migrate operations quality schema"
```

### Task 2: Immutable price book and startup membership validation

**Files:**
- Create: `backend/app/services/llm/pricing.py`
- Create: `backend/tests/unit/test_llm_pricing.py`
- Modify: `backend/app/services/llm/errors.py`
- Modify: `backend/app/config.py`
- Modify: `backend/tests/unit/test_wp7_config.py`

**Produces:** validated six-decimal price snapshots; exact cost calculation;
startup rejection when any enabled model lacks a configured price.

- [ ] **Step 1: Write failing pricing tests**

```python
# backend/tests/unit/test_llm_pricing.py
from decimal import Decimal

import pytest

from backend.app.services.llm.pricing import (
    InvalidPriceBook,
    ModelPriceMissing,
    estimate_cost,
    parse_price_book,
)


def test_price_book_normalizes_and_cost_is_reconstructable() -> None:
    prices = parse_price_book('{"m":{"input":1.25,"output":10}}')
    price = prices.require("m")
    assert price.input_cny_per_million == Decimal("1.250000")
    assert price.output_cny_per_million == Decimal("10.000000")
    assert estimate_cost(price, 1, 1) == Decimal("0.000011250000")


@pytest.mark.parametrize("raw", [
    '{"m":{"input":1.0000001,"output":2}}',
    '{"m":{"input":-1,"output":2}}',
    '{"m":{"input":true,"output":2}}',
    '{"m":{"input":NaN,"output":2}}',
    '[]',
    '{}',
    '{"":{"input":1,"output":2}}',
    '{"m":{"input":1}}',
    '{"m":{"input":1,"output":2,"cached":1}}',
    '{"m":{"input":1,"output":2},"m":{"input":3,"output":4}}',
])
def test_invalid_rate_is_rejected(raw: str) -> None:
    with pytest.raises(InvalidPriceBook):
        parse_price_book(raw)


def test_missing_model_is_typed() -> None:
    with pytest.raises(ModelPriceMissing):
        parse_price_book('{"m":{"input":1,"output":2}}').require("other")


def test_partial_unknown_usage_has_null_cost() -> None:
    price = parse_price_book('{"m":{"input":1,"output":2}}').require("m")
    assert estimate_cost(price, None, 1) is None
    assert estimate_cost(price, 1, None) is None


def test_price_book_is_immutable() -> None:
    prices = parse_price_book('{"m":{"input":1,"output":2}}')
    with pytest.raises(TypeError):
        prices.models["m"] = prices.require("m")  # type: ignore[index]
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest backend/tests/unit/test_llm_pricing.py -q`

Expected: FAIL because `pricing.py` does not exist.

- [ ] **Step 3: Implement the price book**

First add `ModelPriceMissing(LLMConfigurationError)` to `errors.py`. Define
`InvalidPriceBook(ValueError)` in `pricing.py` and import/re-export
`ModelPriceMissing` there so the test import is stable.
Implement the complete shape/normalization path:

```python
RATE_QUANTUM = Decimal("0.000001")
COST_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True)
class ModelPrice:
    input_cny_per_million: Decimal
    output_cny_per_million: Decimal


@dataclass(frozen=True)
class PriceBook:
    models: Mapping[str, ModelPrice]

    def require(self, model: str) -> ModelPrice:
        try:
            return self.models[model]
        except KeyError as exc:
            raise ModelPriceMissing(model) from exc


def _reject_constant(value: str) -> NoReturn:
    raise InvalidPriceBook(f"non-finite JSON constant: {value}")


def _object_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPriceBook(f"duplicate model/rate key: {key}")
        result[key] = value
    return result


def _rate(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise InvalidPriceBook("rates must be JSON numbers")
    if not value.is_finite() or value < 0:
        raise InvalidPriceBook("rates must be finite and nonnegative")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -6:
        raise InvalidPriceBook("rates must be finite, nonnegative, scale <= 6")
    return value.quantize(RATE_QUANTUM)


def parse_price_book(raw: str) -> PriceBook:
    try:
        decoded = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_no_duplicates,
        )
    except (InvalidPriceBook, JSONDecodeError) as exc:
        if isinstance(exc, InvalidPriceBook):
            raise
        raise InvalidPriceBook("invalid price JSON") from exc
    if not isinstance(decoded, dict) or not decoded:
        raise InvalidPriceBook("price book must be a non-empty object")
    models: dict[str, ModelPrice] = {}
    for model, rates in decoded.items():
        if not isinstance(model, str) or not model.strip():
            raise InvalidPriceBook("model names must be non-empty strings")
        if not isinstance(rates, dict) or set(rates) != {"input", "output"}:
            raise InvalidPriceBook("each model requires only input/output rates")
        models[model] = ModelPrice(_rate(rates["input"]), _rate(rates["output"]))
    return PriceBook(MappingProxyType(models))


def estimate_cost(
    price: ModelPrice, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    if input_tokens is None or output_tokens is None:
        return None
    raw = (
        Decimal(input_tokens) * price.input_cny_per_million
        + Decimal(output_tokens) * price.output_cny_per_million
    ) / Decimal(1_000_000)
    return raw.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
```

- [ ] **Step 4: Run the price-book test and confirm green**

Run: `uv run pytest backend/tests/unit/test_llm_pricing.py -q`

Expected: PASS before changing the configuration validator.

- [ ] **Step 5: Add failing startup/config membership tests**

In `test_wp7_config.py`, use `monkeypatch.setenv`, call
`get_settings.cache_clear()` before loading, and clear it again in a `finally`
block. Add:

```python
def test_every_configured_model_requires_a_price(monkeypatch) -> None:
    monkeypatch.setenv(
        "LLM_PRICE_CNY_PER_MILLION_JSON",
        '{"test-extract":{"input":1,"output":2}}',
    )
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="price"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_distinct_enabled_secondary_requires_its_own_price(monkeypatch) -> None:
    monkeypatch.setenv("CROSS_ENGINE_MODEL", "missing-secondary")
    monkeypatch.setenv(
        "LLM_PRICE_CNY_PER_MILLION_JSON",
        '{"test-extract":{"input":1,"output":2},'
        '"test-extract-fallback":{"input":1,"output":2},'
        '"test-judge":{"input":1,"output":2},'
        '"test-judge-fallback":{"input":1,"output":2},'
        '"test-light":{"input":1,"output":2}}',
    )
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="price"):
            get_settings()
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 6: Run the membership tests and confirm failure**

Run: `uv run pytest backend/tests/unit/test_wp7_config.py -q`

Expected: FAIL until Settings parses the price book, requires prices for
extract primary/fallback, judge primary/fallback, lightweight, and enabled
secondary.

- [ ] **Step 7: Implement startup price membership validation**

Extend the Task 1 `model_validator(mode="after")` to parse the book and require
those model keys, converting
`InvalidPriceBook`/`ModelPriceMissing` to `ValueError` for Pydantic.

- [ ] **Step 8: Verify and commit pricing/config membership**

```powershell
uv run pytest backend/tests/unit/test_llm_pricing.py backend/tests/unit/test_wp7_config.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/llm/errors.py backend/app/services/llm/pricing.py backend/app/config.py backend/tests/unit/test_llm_pricing.py backend/tests/unit/test_wp7_config.py
git commit -m "feat(wp7): add immutable llm price book"
```

Expected: both narrow and offline suites pass, ruff/mypy pass, and the commit
contains only the listed files.

---

## Chunk 2: Usage Recorder and Metered Gateway

### Task 3: Independent-session usage recorder

**Files:**
- Create: `backend/app/services/llm/usage.py`
- Create: `backend/tests/integration/test_llm_usage.py`
- Modify: `backend/app/services/llm/errors.py`

**Produces:** immutable call context and handle; fail-closed pending insert;
guarded, bounded, independently retried terminal writes; stale-row abandonment.

- [ ] **Step 1: Write failing recorder integration tests with real fixtures**

`backend/tests/integration/test_llm_usage.py` imports
`AsyncSessionLocal`, uses the existing `db_session` fixture for assertions,
and defines all helpers in-file:

```python
pytestmark = pytest.mark.integration


def _prices() -> PriceBook:
    return parse_price_book(
        '{"test-judge":{"input":1.000000,"output":2.000000}}'
    )


def _context() -> LLMCallContext:
    return LLMCallContext(
        operation="judge", call_group_id=uuid4(), trace_id="trace-1",
        ingestion_job_id=None, score_id=None, jd_id=None, rule_version_id=None,
    )


async def _load(db_session: AsyncSession, attempt_id: int) -> LLMUsageAttempt:
    row = await db_session.get(LLMUsageAttempt, attempt_id)
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_begin_and_finalize_success_are_content_free(db_session) -> None:
    recorder = UsageRecorder(session_factory=AsyncSessionLocal, prices=_prices())
    context = LLMCallContext(
        operation="judge", call_group_id=uuid4(), trace_id="trace-1",
        ingestion_job_id=None, score_id=None, jd_id=None, rule_version_id=None,
    )
    handle = await recorder.begin(
        context=context, requested_model="test-judge",
        attempt_role="primary", prompt_version="resume_judge_v1",
    )
    assert handle.attempt_id > 0
    assert await recorder.finalize(
        handle, status="succeeded", actual_model="actual",
        input_tokens=10, output_tokens=5, latency_ms=123, error_code=None,
    )
    row = await _load(db_session, handle.attempt_id)
    assert row.status == "succeeded"
    assert row.estimated_cost_cny == Decimal("0.000020000000")
    assert set(row.__table__.columns.keys()).isdisjoint(
        {"prompt", "response", "candidate_name", "object_key"}
    )


@pytest.mark.asyncio
async def test_terminal_attempt_cannot_be_finalized_twice(db_session) -> None:
    recorder = UsageRecorder(session_factory=AsyncSessionLocal, prices=_prices())
    handle = await recorder.begin(
        context=_context(), requested_model="test-judge",
        attempt_role="primary", prompt_version="resume_judge_v1",
    )
    assert await recorder.finalize(handle, status="unavailable", error_code="provider_unavailable")
    assert not await recorder.finalize(handle, status="succeeded", input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_begin_failure_is_fail_closed(monkeypatch) -> None:
    class BrokenContext:
        async def __aenter__(self):
            raise OSError("database unavailable")

        async def __aexit__(self, *_args):
            return False

    recorder = UsageRecorder(session_factory=lambda: BrokenContext(), prices=_prices())
    with pytest.raises(UsageLedgerUnavailable):
        await recorder.begin(
            context=_context(), requested_model="test-judge",
            attempt_role="primary", prompt_version="resume_judge_v1",
        )


@pytest.mark.asyncio
async def test_finalize_retries_fresh_sessions_and_leaves_pending(
    db_session, caplog,
) -> None:
    seeded = UsageRecorder(session_factory=AsyncSessionLocal, prices=_prices())
    handle = await seeded.begin(
        context=_context(), requested_model="test-judge",
        attempt_role="primary", prompt_version="resume_judge_v1",
    )
    contexts = []
    class BrokenContext:
        async def __aenter__(self):
            raise OSError("database unavailable")
        async def __aexit__(self, *_args):
            return False
    def factory():
        context = BrokenContext()
        contexts.append(context)
        return context
    failing = UsageRecorder(
        session_factory=factory, prices=_prices(), finalize_retries=3,
    )
    with caplog.at_level(logging.CRITICAL):
        assert not await failing.finalize(
            handle, status="succeeded", actual_model="actual",
            input_tokens=1, output_tokens=1, latency_ms=1, error_code=None,
        )
    assert (len(contexts), len({id(context) for context in contexts})) == (3, 3)
    row = await _load(db_session, handle.attempt_id)
    await db_session.refresh(row)
    assert (row.status, row.finished_at) == ("pending", None)
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert critical[0].attempt_id == handle.attempt_id
    assert critical[0].trace_id == "trace-1"
    assert critical[0].retry_count == 3
    assert "database unavailable" not in caplog.text


@pytest.mark.asyncio
async def test_stale_pending_becomes_abandoned(db_session) -> None:
    recorder = UsageRecorder(session_factory=AsyncSessionLocal, prices=_prices())
    handle = await recorder.begin(
        context=_context(), requested_model="test-judge",
        attempt_role="primary", prompt_version="resume_judge_v1",
    )
    row = await _load(db_session, handle.attempt_id)
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    await db_session.commit()
    assert await abandon_stale_attempts(
        db_session,
        older_than=datetime.now(timezone.utc) - timedelta(minutes=10),
    ) == [handle.attempt_id]
    await db_session.refresh(row)
    assert row.status == "abandoned"
    assert row.finished_at is not None
    assert row.error_code == "usage_finalization_missing"
    assert (row.input_tokens, row.output_tokens, row.estimated_cost_cny) == (
        None, None, None,
    )


@pytest.mark.asyncio
async def test_requested_price_snapshot_is_not_rewritten_by_actual_model(db_session) -> None:
    recorder = UsageRecorder(session_factory=AsyncSessionLocal, prices=_prices())
    handle = await recorder.begin(
        context=_context(), requested_model="test-judge",
        attempt_role="primary", prompt_version="resume_judge_v1",
    )
    await recorder.finalize(
        handle, status="succeeded", actual_model="provider-versioned-name",
        input_tokens=1, output_tokens=1, latency_ms=1, error_code=None,
    )
    row = await _load(db_session, handle.attempt_id)
    assert row.requested_model == "test-judge"
    assert row.actual_model == "provider-versioned-name"
    assert row.input_price_cny_per_million == Decimal("1.000000")
```

- [ ] **Step 2: Run and confirm failure**

Run:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_llm_usage.py -q
```

Expected: FAIL because `UsageRecorder`, call context, and new errors do not
exist.

- [ ] **Step 3: Implement errors, context, and recorder**

Add:

```python
# errors.py
class UsageLedgerUnavailable(LLMUnavailableError):
    pass
```

`usage.py` public contract:

```python
@dataclass(frozen=True)
class LLMCallContext:
    operation: Literal["extract", "judge", "cross_check", "lightweight"]
    call_group_id: UUID
    trace_id: str | None = None
    ingestion_job_id: int | None = None
    score_id: int | None = None
    jd_id: int | None = None
    rule_version_id: int | None = None


@dataclass(frozen=True)
class UsageAttemptHandle:
    attempt_id: int
    price: ModelPrice
    started_at: datetime
    trace_id: str | None
```

Implement `UsageRecorder` with constructor arguments `session_factory`
(default `AsyncSessionLocal`), `prices` (default parsed from Settings), and
`finalize_retries` (default Settings). `begin(...)` must:

1. call `prices.require(requested_model)` before opening a transaction;
2. capture one UTC `started_at`;
3. open `async with session_factory() as db`, add one `pending`
   `LLMUsageAttempt` copying only context IDs/operation, model/role/version,
   and the two requested-model price snapshots;
4. flush for the ID and commit before returning
   `UsageAttemptHandle(id, price, started_at, context.trace_id)`; and
5. on `SQLAlchemyError`/connection `OSError`, roll back through the context,
   emit only error class/trace metadata, and raise `UsageLedgerUnavailable`
   without embedding the exception message.

`finalize(...)` validates a terminal status, nonnegative nullable token/latency
values, and computes cost only when both counts exist. For each bounded retry,
create `session_factory()` *inside* the loop and execute one guarded
`UPDATE llm_usage_attempts SET ... finished_at=func.now()
WHERE id=:id AND status='pending'`. Commit and return `True` for rowcount 1;
commit and return `False` for rowcount 0 (already terminal/missing). On
`SQLAlchemyError`/`OSError`, let that fresh context roll back and continue.
After the last failure, emit exactly one `CRITICAL` record with structured
extras `attempt_id`, `trace_id`, `retry_count`, and `exception_class`, then
return `False`; never include exception text or rethrow in a way that repeats
a paid call.

`abandon_stale_attempts(db, older_than)` performs one
`UPDATE ... RETURNING id`, sets status `abandoned`, finished time, and
`error_code="usage_finalization_missing"` without inventing usage.
Finalization logs contain only attempt ID, trace ID, retry count, and exception
class; never exception messages, prompts, responses, names, or object keys.

- [ ] **Step 4: Run narrow and task gates**

Run:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_llm_usage.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add -- backend/app/services/llm/errors.py backend/app/services/llm/usage.py backend/tests/integration/test_llm_usage.py
git commit -m "feat(wp7): add independent usage recorder"
```

### Task 4: Meter every gateway attempt and correlate scoring context

**Files:**
- Modify: `backend/app/services/llm/schemas.py`
- Modify: `backend/app/services/llm/gateway.py`
- Modify: `backend/app/services/parser/extractor.py`
- Modify: `backend/app/scoring/llm_judge.py`
- Modify: `backend/app/scoring/pipeline.py`
- Modify: `backend/app/tasks/ingest.py`
- Modify: `backend/app/routers/candidates.py`
- Modify: `backend/tests/unit/test_llm_gateway.py`
- Modify: `backend/tests/unit/test_extractor.py`
- Modify: `backend/tests/unit/test_llm_judge.py`
- Modify: `backend/tests/integration/test_candidates_api.py`
- Modify: `backend/tests/integration/test_pipeline.py`
- Modify: `backend/tests/integration/test_tasks_ingest.py`
- Modify: `backend/tests/integration/test_llm_usage.py`
- Modify: `backend/tests/external/test_newapi_runtime_contract.py`

**Produces:** every primary/fallback/secondary/lightweight provider request has
one pre-call attempt; missing price/ledger prevents the call; Score gets its
judge call group without mutating terminal ledger rows.

- [ ] **Step 1: Rewrite gateway tests first**

Update helpers so every call passes `LLMCallContext`. Inject a mocked
`UsageRecorder` into `LLMGateway(recorder=recorder)`. Add:

```python
@pytest.mark.asyncio
async def test_primary_failure_and_fallback_are_separate_attempts(monkeypatch) -> None:
    recorder = SimpleNamespace(
        begin=AsyncMock(side_effect=[_handle(1), _handle(2)]),
        finalize=AsyncMock(return_value=True),
    )
    gateway = LLMGateway(recorder=recorder)
    gateway._client.chat.completions.create = AsyncMock(
        side_effect=[APIConnectionError(request=_request()), _provider_success()]
    )
    result = await gateway.judge(
        {"resume_markdown": "private"}, schema={"type": "object"},
        context=_context("judge"),
    )
    assert result.used_fallback is True
    assert [c.kwargs["attempt_role"] for c in recorder.begin.await_args_list] == [
        "primary", "fallback",
    ]
    assert [c.kwargs["status"] for c in recorder.finalize.await_args_list] == [
        "unavailable", "succeeded",
    ]


@pytest.mark.asyncio
async def test_pre_call_ledger_failure_does_not_call_provider() -> None:
    recorder = SimpleNamespace(
        begin=AsyncMock(side_effect=UsageLedgerUnavailable("ledger unavailable")),
        finalize=AsyncMock(),
    )
    gateway = LLMGateway(recorder=recorder)
    gateway._client.chat.completions.create = AsyncMock()
    with pytest.raises(UsageLedgerUnavailable):
        await gateway.extract("private", schema={}, context=_context("extract"))
    gateway._client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_price_does_not_call_provider() -> None:
    recorder = SimpleNamespace(
        begin=AsyncMock(side_effect=ModelPriceMissing("test-judge")),
        finalize=AsyncMock(),
    )
    gateway = LLMGateway(recorder=recorder)
    gateway._client.chat.completions.create = AsyncMock()
    with pytest.raises(ModelPriceMissing):
        await gateway.judge({}, schema={}, context=_context("judge"))
    gateway._client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_secondary_override_is_one_secondary_attempt() -> None:
    recorder = _recorder()
    gateway = LLMGateway(recorder=recorder)
    gateway._client.chat.completions.create = AsyncMock(return_value=_provider_success())
    await gateway.judge(
        {}, schema={}, context=_context("cross_check"),
        model_override="test-secondary", attempt_role="secondary",
    )
    assert recorder.begin.await_args.kwargs["requested_model"] == "test-secondary"
    assert recorder.begin.await_args.kwargs["attempt_role"] == "secondary"


@pytest.mark.asyncio
async def test_finalize_failure_does_not_repeat_paid_call() -> None:
    recorder = _recorder()
    recorder.finalize.return_value = False
    gateway = LLMGateway(recorder=recorder)
    gateway._client.chat.completions.create = AsyncMock(return_value=_provider_success())
    result = await gateway.judge({}, schema={}, context=_context("judge"))
    assert result.content
    gateway._client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "role", "override"),
    [
        ("judge", "secondary", "test-secondary"),
        ("cross_check", "primary", "test-secondary"),
        ("cross_check", "secondary", None),
    ],
)
async def test_invalid_secondary_override_is_rejected_before_call(
    operation: str, role: str, override: str | None,
) -> None:
    gateway = LLMGateway(recorder=_recorder())
    gateway._client.chat.completions.create = AsyncMock()
    with pytest.raises(LLMConfigurationError):
        await gateway.judge(
            {}, schema={}, context=_context(operation),
            model_override=override, attempt_role=role,
        )
    gateway._client.chat.completions.create.assert_not_awaited()
```

Change the existing missing-usage test to assert
`input_tokens is None`, `output_tokens is None`, and recorder finalization gets
null usage. Parameterize provider failure tests to assert these exact terminal
ledger pairs, never exception class/message strings:

```text
transport/rate-limit/timeout -> ("unavailable", "provider_unavailable")
provider malformed/schema-invalid output -> ("invalid_response", "invalid_response")
provider configuration/authentication failure ->
  ("configuration_error", "provider_configuration_error")
unexpected provider exception -> ("unavailable", "provider_unexpected_error")
```

- [ ] **Step 2: Run gateway tests and confirm failure**

Run: `uv run pytest backend/tests/unit/test_llm_gateway.py -q`

Expected: FAIL because the gateway has no recorder/context/override interface
and currently converts missing usage to zero.

- [ ] **Step 3: Extend response and gateway contracts**

Change:

```python
@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    call_group_id: UUID
    prompt_version: str = ""
    latency_ms: int = 0
    used_fallback: bool = False
```

`LLMGateway.__init__` accepts `recorder: UsageRecorder | None` and defaults to a
real recorder. `_call_once` receives `context` and `attempt_role`; it must:

1. call `recorder.begin` before constructing/sending the request;
2. invoke the provider only after begin commits;
3. map each existing provider exception to the same public exception plus
   terminal ledger status/error code;
4. await `recorder.finalize` before returning/raising;
5. never pass messages/content to the recorder or logs;
6. use nullable token counts when provider usage is absent; and
7. return the context call group.

`_call_with_fallback` passes `primary` then `fallback` roles. Domain-validation
fallback-only calls remain a separate fallback attempt. `judge(...,
model_override, attempt_role)` accepts override only when
`context.operation == "cross_check"` and role is secondary; it calls once with
no fallback. `lightweight` also requires a context.

- [ ] **Step 4: Rerun gateway tests and confirm green**

Run: `uv run pytest backend/tests/unit/test_llm_gateway.py -q`

Expected: PASS before downstream signatures are changed.

- [ ] **Step 5: Write failing downstream propagation tests**

Before editing downstream production code, add these exact assertions:

- `test_llm_judge.py`: `dims=[]` returns `call_group_id is None`, tokens zero,
  and never calls the gateway.
- `test_llm_judge.py`: invalid primary dimension IDs followed by valid
  `fallback_only` output calls the gateway twice with the same context and the
  second call has `fallback_only=True`. A real Gateway + mocked recorder variant
  asserts recorder roles `primary`, then `fallback`, and both paid HTTP-success
  attempts finalize as `status="succeeded", error_code=None`; downstream
  domain validation must not rewrite them as ledger `invalid_response`.
- `test_extractor.py`: invalid primary payload then valid fallback uses the same
  context and returns the fallback call group.
- `test_pipeline.py`: judged Score call group equals `JudgeResult.call_group_id`;
  hard reject and empty judge schema persist null.
- `test_pipeline.py`: with a real `LLMGateway`/`UsageRecorder` and mocked
  provider, the committed Score group equals both terminal judge-attempt rows;
  both ledger `score_id` values remain null, and reloading after Score commit
  proves status/tokens/cost/finished time did not change.
- `test_tasks_ingest.py`: both `run_job` and legacy `run_parse_and_score` pass
  job/trace/JD context into extractor and scoring.
- `test_tasks_ingest.py`: `ModelPriceMissing` produces terminal failed job code
  `model_price_missing` and is not classified retryable.
- `test_llm_usage.py`: real recorder + mocked provider produces two durable rows
  for unavailable primary/fallback success, with one call group, distinct roles,
  and no seeded plaintext name.
- `test_candidates_api.py`: `UsageLedgerUnavailable` maps to
  `503/detail.code == "usage_ledger_unavailable"` and the provider is untouched.
- `test_candidates_api.py`: `ModelPriceMissing` maps to
  `503/detail == {"code":"model_price_missing",
  "message":"Configured LLM model price is unavailable"}` and the provider is
  untouched.

- [ ] **Step 6: Run downstream tests and confirm failure**

Run:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/unit/test_llm_judge.py backend/tests/unit/test_extractor.py backend/tests/integration/test_pipeline.py backend/tests/integration/test_tasks_ingest.py backend/tests/integration/test_llm_usage.py backend/tests/integration/test_candidates_api.py -q
```

Expected: FAIL on missing context parameters, call-group fields, and error
mapping.

- [ ] **Step 7: Propagate context through extractor, judge, and pipeline**

Use these signatures:

```python
ResumeExtractor.extract(markdown: str, *, context: LLMCallContext) -> ExtractedResume
LLMJudge.score(*, resume_text: str, dims: list[JudgeDimension],
               context: LLMCallContext,
               model_override: str | None = None) -> JudgeResult
ScoringPipeline.run(*, candidate_id: int, jd_id: int,
                    ingestion_job_id: int | None = None,
                    trace_id: str | None = None) -> PipelineResult
```

For extraction, both ingestion paths create a new extract group and pass
available job/trace/JD context. For scoring, after loading the active
RuleVersion, create a judge context with new group and job/trace/JD/rule IDs.
Add `JudgeResult.call_group_id: UUID | None`; the empty-dimension return sets
it to `None`. Store a non-null value on `Score.llm_judge_call_group_id`.
Hard-filter and empty-dimension paths create no provider attempt and persist
null.

Update synchronous `/candidates/{id}/score` to pass ambient trace.
Map `ModelPriceMissing` to
`503 {"code":"model_price_missing",
"message":"Configured LLM model price is unavailable"}` and
`UsageLedgerUnavailable` to
`503 {"code":"usage_ledger_unavailable","message":"LLM usage ledger unavailable"}`.
Add ledger unavailable to ingestion retryable errors and missing price to
terminal configuration errors.

Because tokens are optional, compatibility totals use:

```python
raw_tokens=(response.input_tokens or 0) + (response.output_tokens or 0)
```

The ledger remains authoritative for unknown usage.

- [ ] **Step 8: Update all remaining callers, including external probes**

All fake gateways accept `context`; every synthetic `LLMResponse` supplies
`call_group_id=uuid4()`. In
`backend/tests/external/test_newapi_runtime_contract.py`, define a local
content-free `_ProbeRecorder` whose async `begin` returns a handle and
`finalize` returns true; construct `LLMGateway(recorder=_ProbeRecorder())` and
pass explicit extract/judge contexts. This provider-only contract probe remains
independent of PostgreSQL without weakening application behavior.

Run:
`rg -n "\\.(extract|judge|lightweight)\\(" backend --glob "*.py"`
and update every production/test call site to the new signature.

- [ ] **Step 9: Run narrow tests and gates**

Run:

```powershell
uv run pytest backend/tests/unit/test_llm_gateway.py backend/tests/unit/test_extractor.py backend/tests/unit/test_llm_judge.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_candidates_api.py backend/tests/integration/test_pipeline.py backend/tests/integration/test_tasks_ingest.py backend/tests/integration/test_llm_usage.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: all pass. Do not run live `external_contract` without explicit
credentials; offline collection/type use is covered.

- [ ] **Step 10: Commit**

```powershell
git add -- backend/app/services/llm/schemas.py backend/app/services/llm/gateway.py backend/app/services/parser/extractor.py backend/app/scoring/llm_judge.py backend/app/scoring/pipeline.py backend/app/tasks/ingest.py backend/app/routers/candidates.py backend/tests/unit/test_llm_gateway.py backend/tests/unit/test_extractor.py backend/tests/unit/test_llm_judge.py backend/tests/integration/test_candidates_api.py backend/tests/integration/test_pipeline.py backend/tests/integration/test_tasks_ingest.py backend/tests/integration/test_llm_usage.py backend/tests/external/test_newapi_runtime_contract.py
git commit -m "feat(wp7): meter every llm provider attempt"
```

### Task 5: Non-blocking, crash-safe budget alerts

**Files:**
- Create: `backend/app/services/operations/__init__.py`
- Create: `backend/app/services/operations/budgets.py`
- Create: `backend/app/tasks/wp7.py`
- Create: `backend/tests/unit/test_budget_periods.py`
- Create: `backend/tests/integration/test_budget_alerts.py`
- Modify: `backend/app/services/llm/usage.py`
- Modify: `backend/app/tasks/celery_app.py`

- [ ] **Step 1: Write failing period/state unit tests**

Test `local_periods(now)` at Shanghai midnight and month rollover, including
UTC conversion and half-open ends. Test `budget_state(spend, budget, warn)`:
zero budget with zero spend is normal, positive spend is exceeded; equality at
warn is warning; equality at budget is exceeded. Test
`thresholds_crossed()` returns both warning and exceeded on a direct jump.

- [ ] **Step 2: Run the unit file and confirm failure**

Run: `uv run pytest backend/tests/unit/test_budget_periods.py -q`

Expected: FAIL because `operations.budgets` does not exist.

- [ ] **Step 3: Implement pure budget calculations**

Use `ZoneInfo("Asia/Shanghai")`, convert local daily/monthly boundaries to UTC,
and return explicit `{scope, period_start, period_end, budget, spend,
unknown_cost_count, state}` values. Sum all non-null costs regardless of
terminal outcome. `thresholds_crossed` independently evaluates warn and 100%
thresholds; budget state is informational only.

- [ ] **Step 4: Rerun period/state tests and confirm green**

Run: `uv run pytest backend/tests/unit/test_budget_periods.py -q`

Expected: PASS before database reconciliation work starts.

- [ ] **Step 5: Write failing reconciliation integration tests**

Use real PostgreSQL and unique trace IDs. Seed/assert with `db_session`, but
the concurrency case must launch each evaluator with its own
`async with AsyncSessionLocal() as session` and transaction. Cover:

- two concurrent evaluations of one period produce exactly one audit row per
  dedupe key `scope:period_start:threshold`;
- a direct normal-to-exceeded jump writes both `llm_budget_warning` and
  `llm_budget_exceeded`;
- enqueue failure after successful usage finalization does not change the
  successful return or ledger terminal state;
- a cursor behind by two complete daily periods processes them in order and
  advances only after each successful period;
- `max_periods=1` leaves backlog for the next run, while the current partial
  period is still evaluated;
- first use initializes each missing cursor from the earliest ledger period
  (or current local period when the ledger is empty); two concurrent first-use
  calls create exactly one cursor row and both finish safely;
- audit payload contains only scope, period timestamps, threshold, budget, and
  observed aggregate; the composed dedupe key is never persisted.

- [ ] **Step 6: Run the integration test and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_budget_alerts.py -q
```

Expected: FAIL because reconciliation and task hooks do not exist.

- [ ] **Step 7: Implement alert insertion, cursors, and tasks**

In `budgets.py`, implement:

1. `aggregate_period(db, start, end)` with `started_at >= start AND
   started_at < end`, `coalesce(sum(estimated_cost_cny), 0)`, and unknown count;
2. `_insert_threshold_once` using
   `pg_advisory_xact_lock(hashtextextended(:dedupe_key, 0))`, followed by an
   `AuditLog` existence query on event type plus the permitted payload
   scope/period-start/threshold fields, then a metadata-only insert. The
   composed key exists only in memory and as the advisory-lock input;
3. `evaluate_current_budgets(db, now)` for daily and monthly partial periods;
4. `reconcile_budget_scope(db, scope, now, max_periods)` first derives the
   earliest ledger period (or current period when empty), performs PostgreSQL
   `INSERT ... ON CONFLICT DO NOTHING`, then `SELECT ... FOR UPDATE` on
   `OperationsReconciliationState`, processing
   complete periods oldest-first and moving `next_period_start` only in the
   same successful transaction; and
5. `reconcile_budgets` for both scopes plus the current partial periods.

In `tasks/wp7.py`, add synchronous Celery wrappers that call async services via
the repository's `asyncio.run` bridge:
`wp7.evaluate_budget_attempt(attempt_id)`,
`wp7.reconcile_budgets`, and `wp7.sweep_stale_usage`. The attempt task reloads
the terminal row and evaluates its containing daily/monthly periods. After a
guarded finalization updates one row, `UsageRecorder` best-effort enqueues the
attempt task; broker failure is metadata-only logged and never changes the paid
call result. Every wrapper owns `AsyncSessionLocal`, commits on success, lets
the context roll back on failure, and calls `await engine.dispose()` in
`finally`, matching `tasks/sweep.py`. All threshold audits use
`actor="system:wp7"`.

Add WP7 to Celery `include`. Schedule `wp7.reconcile_budgets` every `300.0`
seconds and `wp7.sweep_stale_usage` every `60.0` seconds; the latter computes
its cutoff from `LLM_USAGE_PENDING_TIMEOUT_SECONDS`.

- [ ] **Step 8: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_budget_periods.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_budget_alerts.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/operations/__init__.py backend/app/services/operations/budgets.py backend/app/services/llm/usage.py backend/app/tasks/wp7.py backend/app/tasks/celery_app.py backend/tests/unit/test_budget_periods.py backend/tests/integration/test_budget_alerts.py
git commit -m "feat(wp7): add non-blocking budget alerts"
```

Expected: all tests/static gates pass and only the listed files are committed.

### Task 6: Operations summary and usage APIs

**Files:**
- Create: `backend/app/services/operations/reporting.py`
- Create: `backend/app/schemas/operations.py`
- Create: `backend/app/routers/operations.py`
- Create: `backend/tests/unit/test_operations_windows.py`
- Create: `backend/tests/unit/test_operations_schemas.py`
- Create: `backend/tests/integration/test_operations_api.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing window and schema unit tests**

Test exact UTC bounds for `today`, `7d`, and `30d`: today compares the same
elapsed duration from the prior Shanghai midnight; N-day windows compare the
immediately preceding equal duration. Reject unsupported names, naive
datetimes, inverted usage bounds, and ranges over 90 days.
In `test_operations_schemas.py`, construct every response model listed in Step
3, assert `Decimal` values serialize as JSON strings without float conversion,
UTC datetimes retain offsets, the usage page has exactly the declared fields,
and a seeded `"private-name"`/`"object/key"` cannot appear in serialized JSON.

- [ ] **Step 2: Run the unit file and confirm failure**

Run:
`uv run pytest backend/tests/unit/test_operations_windows.py backend/tests/unit/test_operations_schemas.py -q`

Expected: FAIL because reporting window helpers and schemas do not exist.

- [ ] **Step 3: Implement window and response contracts**

Define these exact metadata-only shapes:

```text
OperationsTotals {
  attempt_count:int, known_cost_cny:Decimal,
  known_token_total:int, unknown_usage_count:int,
  succeeded_count:int, failed_count:int, abandoned_count:int, pending_count:int,
  p50_latency_ms:Decimal|null, p95_latency_ms:Decimal|null,
  last_completed_at:datetime|null
}
OperationsDelta { absolute:Decimal|null, percentage:Decimal|null }
OperationsSeriesPoint {
  local_date:date, attempt_count:int, known_cost_cny:Decimal,
  unknown_usage_count:int
}
OperationsBreakdown {
  key:str, attempt_count:int, known_cost_cny:Decimal, unknown_usage_count:int
}
BudgetSnapshot {
  scope:"daily"|"monthly", period_start:datetime, period_end:datetime,
  budget_cny:Decimal, spend_cny:Decimal, ratio:Decimal|null,
  unknown_cost_count:int, state:"normal"|"warning"|"exceeded"
}
OperationsSummary {
  window:"today"|"7d"|"30d", current_start:datetime, current_end:datetime,
  previous_start:datetime, previous_end:datetime,
  current:OperationsTotals, previous:OperationsTotals,
  cost_delta:OperationsDelta, attempt_delta:OperationsDelta,
  daily_series:list[OperationsSeriesPoint],
  by_operation/by_requested_model/by_actual_model/by_outcome/by_attempt_role:
    list[OperationsBreakdown],
  budgets:list[BudgetSnapshot]
}
UsageItem {
  id:int, call_group_id:UUID, trace_id:str|null,
  ingestion_job_id/score_id/jd_id/rule_version_id:int|null,
  operation:str, attempt_role:str, requested_model:str, actual_model:str|null,
  prompt_version:str, status:str, input_tokens/output_tokens:int|null,
  input_price_cny_per_million/output_price_cny_per_million:Decimal,
  estimated_cost_cny:Decimal|null, latency_ms:int|null, error_code:str|null,
  started_at:datetime, finished_at:datetime|null
}
UsagePage { items:list[UsageItem], page:int, page_size:int, total:int }
```

`failed_count` is exactly statuses `unavailable`, `invalid_response`, and
`configuration_error`; abandoned and pending are separate. `known_token_total`
sums input+output only for rows where both are non-null.
`unknown_usage_count` counts rows where either is null. Daily series buckets by
Shanghai local calendar date and serializes ISO `YYYY-MM-DD`. Breakdown keys
use `"(unknown)"` for null actual model.

Use timezone-aware half-open bounds. Keep `Decimal` through services/schemas;
never expose prompts, responses, candidate fields, object keys, evidence, or
reasoning.

- [ ] **Step 4: Rerun window tests and confirm green**

Run:
`uv run pytest backend/tests/unit/test_operations_windows.py backend/tests/unit/test_operations_schemas.py -q`

Expected: PASS before API/database work starts.

- [ ] **Step 5: Write failing API integration tests**

Seed distinct attempts at exact start/end boundaries and cover:

- `hr_lead` and `admin` receive 200; plain `hr` receives 403;
- missing authentication receives 401;
- summary current/previous aggregates, daily series, breakdowns, budgets,
  unknown counts, last-completed time, and PostgreSQL continuous p50/p95;
- percentile fixtures include a deliberately malformed pending row with
  non-null latency and prove only terminal rows with known latency contribute;
- end-boundary exclusion and prior equal-length window;
- usage pagination and every filter: operation, requested/actual model, status,
  role, trace ID, ingestion job ID, score ID, and JD code;
- unsupported summary window returns
  `422 {"detail":{"code":"invalid_operations_window","message":"Unsupported operations window"}}`;
  naive/inverted ranges return
  `422 {"detail":{"code":"invalid_usage_range","message":"Usage range must be timezone-aware and increasing"}}`;
  and >90 days returns
  `422 {"detail":{"code":"usage_range_too_large","message":"Usage range cannot exceed 90 days"}}`;
- serialized bodies lack seeded candidate name, ciphertext, object key,
  prompt, response, evidence, and reasoning strings.

Capture `lead_headers = auth_headers("hr_lead")` once per test when repeated
requests must use the same user.

- [ ] **Step 6: Run the API file and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_operations_api.py -q
```

Expected: FAIL with missing schemas/router/service.

- [ ] **Step 7: Implement queries and role-gated routes**

`reporting.py` uses SQL aggregates over the ledger, PostgreSQL
`percentile_cont(0.50/0.95) WITHIN GROUP (ORDER BY latency_ms)` filtered to
`status <> 'pending' AND latency_ms IS NOT NULL`, and joins JD only to filter
by code. Apply every filter before count/page, order usage by
`started_at DESC, id DESC`, and use WP4 `Page/page_params`.

Add:

- `GET /api/v1/operations/summary?window=today|7d|30d`
- `GET /api/v1/operations/usage?from=...&to=...&...`

Both depend on the existing current-user guard and reject roles outside
`{"hr_lead", "admin"}`. Use explicit query dependencies to emit the three
stable 422 detail objects above rather than FastAPI's generic validation body.
Register the router in `main.py`.

- [ ] **Step 8: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_operations_windows.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_operations_api.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/operations/reporting.py backend/app/schemas/operations.py backend/app/routers/operations.py backend/app/main.py backend/tests/unit/test_operations_windows.py backend/tests/unit/test_operations_schemas.py backend/tests/integration/test_operations_api.py
git commit -m "feat(wp7): add operations reporting api"
```

Expected: all pass and only the Task 6 files are committed.

---

## Chunk 3: Immutable Quality Releases

### Task 7: Pure quality metric engine

**Files:**
- Create: `backend/app/services/quality/__init__.py`
- Create: `backend/app/services/quality/metrics.py`
- Create: `backend/tests/unit/test_quality_metrics.py`

- [ ] **Step 1: Write failing metric tests**

Build content-free fixtures with golden label, bound rule weights, matching
Score grade/rule/judge payload, and feedback agreement. Cover:

- classification reuses `metric_stats`: advance positive, reject negative,
  `grade != "rejected"` predicts positive, borderline excluded, uncovered
  separate, and zero denominator returns null;
- evidence denominator includes every expected judge dimension for scores that
  reached judge; unknown/missing/no-evidence is uncovered; hard rejects are
  excluded; no judge dimensions is `not_applicable`; none reached is
  `insufficient_data`;
- weighted confidence excludes unknown/missing/zero-weight dimensions, uses
  five exact bins `[0,.2) ... [.8,1]`, treats out-of-range/non-finite persisted
  confidence as unavailable, suppresses accuracy/gap for bins below minimum,
  and computes ECE only over sufficient bins; include two JDs sharing one
  dimension ID with different weights to prove lookup is keyed by `(jd_id,id)`;
- agreement excludes hold/null from denominator and reports agreed,
  disagreed, hold, and rate;
- target statuses/rollup include insufficient F1 => below, not-applicable
  evidence neutral, and below evidence => below; and
- output recursively contains no evidence quote/reasoning fixture strings.

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest backend/tests/unit/test_quality_metrics.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement metric dataclasses and pure functions**

Use these exact content-free normalized inputs:

```text
BoundJudgeDimension { jd_id:int, id:str, weight:Decimal }
JudgeObservation {
  dimension_id:str, tier:str, score:Decimal|null,
  confidence:Decimal|null, has_validated_evidence:bool
}
QualityItem {
  jd_id:int, candidate_id:int, golden_label:"advance"|"reject"|"borderline",
  grade:str|null, reached_judge:bool, judge:list[JudgeObservation]
}
AgreementObservation { jd_id:int, ai_agreed:bool|null }
```

The release query layer converts persisted judge rows to this shape:
`tier == "unknown"` or null score is unknown; missing dimension is unknown;
confidence must be finite in `[0,1]`, otherwise it becomes unavailable (never
clamped); `has_validated_evidence` is true only for a non-empty persisted
evidence list. Metric signatures are:

```text
classification_metrics(items) -> {
  labeled_total, covered, uncovered, borderline_excluded,
  confusion:{tp,fp,tn,fn}, precision, recall, f1, accuracy
}
evidence_metrics(items, expected_dimensions) -> {
  participating_candidates, hard_filter_rejects, expected_count, covered_count,
  value, status
}
confidence_metrics(items, dimensions, minimum_bucket_size) -> {
  available_count, confidence_unavailable, bins:[
    {lower,upper,upper_inclusive,count,mean_confidence,
     decision_accuracy,absolute_gap,status}
  ], ece
}
agreement_metrics(observations) -> {
  agreed, disagreed, hold, denominator, agreement_rate
}
target_result(value, target, null_status) -> {value,target,status}
release_rollup(f1_result, evidence_result) -> "meets_target"|"below_target"
```

`classification_metrics` counts covered non-borderline items and calls
`backend.app.services.golden_set.metric_stats` directly.
`evidence_metrics` matches judge entries by dimension ID, requiring
unknown tier is false, numeric score, and non-empty persisted validated
evidence list. `confidence_metrics` computes weighted means only over known
positive-weight matches; place confidence exactly `1` in the final bin. A
sufficient bin reports Decimal mean/accuracy/gap; ECE denominator is the sum
of counts in sufficient bins only. `agreement_metrics` consumes only
already-version/window-filtered observations. `target_result` and
`release_rollup` implement design §10.6 exactly.

- [ ] **Step 4: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_quality_metrics.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/quality/__init__.py backend/app/services/quality/metrics.py backend/tests/unit/test_quality_metrics.py
git commit -m "feat(wp7): add quality metric engine"
```

Expected: all pass and only the three Task 7 files are committed.

### Task 8: Preview and create immutable quality releases

**Files:**
- Create: `backend/app/services/quality/releases.py`
- Create: `backend/app/schemas/quality.py`
- Create: `backend/app/routers/quality.py`
- Create: `backend/tests/unit/test_quality_fingerprint.py`
- Create: `backend/tests/unit/test_quality_schemas.py`
- Create: `backend/tests/integration/test_quality_releases_api.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing fingerprint/window unit tests**

Test canonical SHA-256 stability under input ordering and change sensitivity
for any golden label, candidate/JD identity, selected rule version, UTC window,
target, or `QUALITY_METRIC_SCHEMA_VERSION="wp7_v1"`. Canonical golden rows use
the ordered tuple `(jd_id, candidate_id, label)`. Test default preceding
30-day window, timezone/order/future-end checks, and 365-day maximum. Fingerprint
input is canonical compact JSON with sorted keys containing only golden content
hash, sorted JD/version bindings, normalized window, target snapshot, and schema
version.
The target snapshot and fingerprint use these exact immutable fields:

```text
metric_schema_version: "wp7_v1"
f1_target: JSON number from QUALITY_F1_TARGET (default 0.75)
evidence_coverage_target: JSON number from QUALITY_EVIDENCE_COVERAGE_TARGET
  (default 0.95)
confidence_bin_boundaries: JSON numbers [0, 0.2, 0.4, 0.6, 0.8, 1]
confidence_min_bucket_size: JSON integer from
  QUALITY_CONFIDENCE_MIN_BUCKET_SIZE (default 10)
evidence_definition: "expected_non_unknown_numeric_with_validated_evidence"
classification_labels: {
  positive:"advance", negative:"reject", excluded:["borderline"],
  predict_positive:"grade_not_rejected"
}
```

All persisted target values/boundaries are JSON numbers, never strings. For
fingerprint bytes, convert each settings-derived number through
`Decimal(str(value))`; emit a finite fixed-point JSON number token with trailing
fractional zeros and a trailing dot removed (`-0` normalized to `0`), unquoted.
Sort object keys and use compact separators. Parse those canonical bytes back
to ordinary JSON numeric values for JSONB persistence; never derive fingerprint
tokens from binary-float formatting.

Test every target field changes the fingerprint. When preview defaults the
window, it returns the exact resolved `window_start` and `window_end`; test a
create succeeds by echoing those two values with the preview fingerprint.
In `test_quality_schemas.py`, import the not-yet-created response models and
assert exact preview/list/detail aggregate and per-JD field sets, Decimal ratios
serialize numerically without binary-float drift, null ratios retain their
denominators/status, and serialized output omits candidate IDs/names,
ciphertext, object keys, evidence, and reasoning.

- [ ] **Step 2: Run unit tests and confirm failure**

Run:
`uv run pytest backend/tests/unit/test_quality_fingerprint.py backend/tests/unit/test_quality_schemas.py -q`

Expected: FAIL because release helpers do not exist.

- [ ] **Step 3: Implement canonical helpers and schema contracts**

Define request `{window_start?, window_end?, jd_codes?, expected_input_fingerprint?}`.
Preview returns resolved `window_start/window_end`, selected
`{jd_id,jd_code,rule_version_id}`, golden item/label
counts, matching-score covered/uncovered counts, target snapshot, and
`input_fingerprint`. Detail adds ID/status, snapshot hash/count, aggregate and
per-JD metric objects, creator `{user_id,display_name}`, and created/window
timestamps. List is WP4-paginated newest-first. Decimal ratios remain numeric
0..1 or null with denominators.

Run both unit files again; expected PASS.

- [ ] **Step 4: Write failing release API integration tests**

Use unique JD codes/PII hashes and capture reusable headers once. Cover:

- preview is read-only, deterministic, and changes after golden/rule/window
  input changes;
- create permissions: `hr_lead`/`admin` 201, `hr` 403, unauthenticated 401;
  read permissions allow all three roles;
- omitted JD selection resolves golden-set JDs; empty => 409
  `golden_set_empty`; missing active rule => 409 `active_rule_missing`;
  malformed pre-existing active weights => 409 `invalid_active_rule`;
- optional stale fingerprint => 409 `release_input_changed`; invalid/future/
  inverted/naive windows =>
  `422 {"detail":{"code":"invalid_release_window",
  "message":"Release window must be timezone-aware, ordered, and end no later than now"}}`;
  spans over 365 days =>
  `422 {"detail":{"code":"release_window_too_large",
  "message":"Release window cannot exceed 365 days"}}`;
  serialization retry exhaustion => 503
  `release_transaction_conflict`;
- latest Score must match bound version and `[start,end)`; end-boundary,
  wrong-version, and old scores are uncovered;
- snapshot content hash reuses identical selected content, while each request
  creates a new release; later golden/rule/feedback/price/target changes do not
  alter prior detail;
- overall/per-JD classification, evidence, confidence, agreement, and
  current/prior operation metrics use correct attribution, continuous
  terminal-known latency percentiles, and active binding;
- below target still persists 201 and atomically writes both audit events;
  meets target writes only `quality_release_created`; actor/target/payload are
  exact and candidate-content-free;
- injected failure before commit leaves no release, bindings, snapshot
  children, or audits; and concurrent snapshot conflict retries the *whole*
  transaction with a fresh repeatable-read session;
- list filters by JD/status and detail/list expose no snapshot entries,
  candidate IDs/names, ciphertext, keys, evidence, or reasoning.

- [ ] **Step 5: Run integration tests and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_quality_releases_api.py -q
```

Expected: FAIL because service/routes are missing.

- [ ] **Step 6: Implement preview, transaction retries, metrics, and reads**

`preview_release(db, request, now)` resolves selection and bindings, validates
active `RuleSchema`, computes selected canonical golden rows sorted by
`(jd_id,candidate_id,label)`, content hash, coverage, targets, and fingerprint without
writing.

`create_release(session_factory, request, actor, now)` loops at most three
times. Each attempt creates a *new* session, begins a transaction, executes
`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ`, and repeats every read from
preview. Check expected fingerprint before writes. Insert/reuse snapshot by
content hash and children, select newest matching Score per candidate/JD with
`row_number(created_at DESC,id DESC)` inside the current half-open window,
filter feedback by matching Score version and
`coalesce(Feedback.updated_at, Feedback.created_at)` in the half-open window,
and query
release-attributed ledger/current-prior operation metrics. Persist release,
bindings, target/metric JSON, and audits before one commit.

Retry only PostgreSQL serialization/deadlock errors and snapshot-hash
`IntegrityError`; roll back/close and restart from the first read. All other
errors propagate. After three conflicts raise
`ReleaseTransactionConflict`. Audit actor is `user:{id}`, target is the release,
and payload contains only snapshot hash, JD/rule IDs, aggregate target states,
and window.

Routes map the exact 409/422/503 codes above, enforce roles, and never expose
snapshot-entry endpoints. Register in `main.py`.

- [ ] **Step 7: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_quality_fingerprint.py backend/tests/unit/test_quality_schemas.py backend/tests/unit/test_quality_metrics.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_quality_releases_api.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/quality/releases.py backend/app/schemas/quality.py backend/app/routers/quality.py backend/app/main.py backend/tests/unit/test_quality_fingerprint.py backend/tests/unit/test_quality_schemas.py backend/tests/integration/test_quality_releases_api.py
git commit -m "feat(wp7): add immutable quality releases"
```

Expected: all pass and only Task 8 files are committed.

### Task 9: Deterministic batch rejection report and audited score reads

**Files:**
- Create: `backend/app/services/quality/batch.py`
- Create: `backend/app/schemas/batch_report.py`
- Create: `backend/app/routers/batch_report.py`
- Create: `backend/tests/unit/test_batch_reasons.py`
- Create: `backend/tests/integration/test_batch_report_api.py`
- Modify: `backend/app/services/read/candidates.py`
- Modify: `backend/app/routers/candidates_read.py`
- Modify: `backend/tests/integration/test_candidates_read_api.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing deterministic reason unit tests**

Use rejected-score snapshots and bound dimension weights. Assert hard filters
count persisted `audit_tag`; rule/judge numeric scores are low only when
`score < 0.5 * weight` (equality is not low); judge unknown is separate;
zero-weight dimensions never low; multiple occurrences in one score increment
occurrences but distinct affected score once; percentages divide by total
rejected and need not sum to 100. Output contains stable reason type/key,
occurrences, affected-score count/percentage, and no text reasoning/evidence.
The public reason types and key sources are exact:

```text
hard_filter  -> persisted hard_filter_result audit_tag
rule_low     -> persisted rule dimension ID
judge_low    -> persisted judge dimension ID
judge_unknown -> persisted judge dimension ID
```

- [ ] **Step 2: Run unit test and confirm failure**

Run: `uv run pytest backend/tests/unit/test_batch_reasons.py -q`

Expected: FAIL because batch service does not exist.

- [ ] **Step 3: Implement pure aggregation and confirm green**

Implement `aggregate_rejection_reasons(scores, weights)` with deterministic
ordering by affected-score count desc, occurrences desc, reason type/key asc.
Run the unit file again; expected PASS.

- [ ] **Step 4: Write failing API/audit integration tests**

Cover read roles (`hr`, `hr_lead`, `admin`), 401/403, default 30-day/max
90-day half-open window, and mandatory at least one of exact batch ID, JD code,
or explicit time window. Assert 422 detail codes `batch_filter_required`,
`invalid_batch_window`, and `batch_window_too_large`. Seed scores across
boundaries and verify total rejected, grade distribution, reasons, filters,
multi-reason percentages, and leak blacklist.

Extend candidate detail tests: every authorized score-detail read writes
`score_detail_read` with `actor=user:{id}`, target type `score`, target ID the
Score ID, and payload exactly `{candidate_id, jd_code}`; list reads do not create it; unauthorized/missing
reads create no audit.

- [ ] **Step 5: Run integration tests and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_batch_report_api.py backend/tests/integration/test_candidates_read_api.py -q
```

Expected: FAIL on missing route and detail-read audit.

- [ ] **Step 6: Implement routes, queries, and audit**

Query the complete filtered Score population with exact RuleVersion schema and
IngestionJob batch relation to build grade distribution. Pass only its rejected
subset to reason aggregation and total-rejected calculation. Return only
filter echo, window, total rejected, grade counts, reason rows, and explicit
`percentages_may_overlap=true`. Enforce roles and stable `detail` errors.

In candidate detail service, add the audit row in the caller-owned transaction
after authorization and Score resolution; `candidates_read.py` passes the
authenticated actor and commits before returning.
Register batch router in `main.py`.

- [ ] **Step 7: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_batch_reasons.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_batch_report_api.py backend/tests/integration/test_candidates_read_api.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/quality/batch.py backend/app/schemas/batch_report.py backend/app/routers/batch_report.py backend/app/services/read/candidates.py backend/app/routers/candidates_read.py backend/app/main.py backend/tests/unit/test_batch_reasons.py backend/tests/integration/test_batch_report_api.py backend/tests/integration/test_candidates_read_api.py
git commit -m "feat(wp7): add deterministic rejection reporting"
```

Expected: all pass and only Task 9 files are committed.

---

## Chunk 4: Recoverable Cross-Engine Checks

### Task 10: Deterministic sampling and durable queue state

**Files:**
- Create: `backend/app/services/cross_check/__init__.py`
- Create: `backend/app/services/cross_check/sampling.py`
- Create: `backend/app/services/cross_check/state.py`
- Create: `backend/tests/unit/test_cross_check_sampling.py`
- Create: `backend/tests/integration/test_cross_check_state.py`

- [ ] **Step 1: Write failing sampling tests**

Test the exact sample algorithm:
`sha256(f"wp7:{score_id}:{prompt_version}".encode()).digest()[:8]`,
unsigned big-endian integer modulo 100, selected when below configured percent.
Pin several expected buckets, 0%/100%, and Unicode-free stable prompt versions.
Test eligibility requires configured distinct secondary model, non-empty bound
judge schema, and non-empty persisted dimensions matching schema IDs.

Test reason evaluation/ordering:
`deterministic_sample`, `low_confidence`, `golden_error`,
`ai_hr_disagreement`, `admin_backfill`; weighted confidence uses `(jd,id)`
weights, zero weights excluded, all unknown triggers low confidence, borderline
does not trigger golden error.

- [ ] **Step 2: Run unit tests and confirm failure**

Run: `uv run pytest backend/tests/unit/test_cross_check_sampling.py -q`

Expected: FAIL because sampling module does not exist.

- [ ] **Step 3: Implement sampling and confirm green**

Implement pure `sample_bucket`, `eligible`, and `trigger_reasons`; return
reasons deduplicated in the fixed order above. Persist no prompt content.
Run the unit file again; expected PASS.

- [ ] **Step 4: Write failing queue-state integration tests**

With real independent sessions, cover:

- `ensure_cross_check` inserts one queued row for
  `(score_id,secondary_model,prompt_version)`, unions later reasons in fixed
  order, and concurrent ensures remain one row;
- a new model/prompt row becomes greatest ID and atomically clears
  `Score.cross_engine_diff/is_suspicious`; an existing same-config ensure does
  not clear a completed projection;
- claim locks queued only, increments attempts, creates random token/expiry,
  and refuses max-attempt rows;
- completion/failure conditional on row ID + running + token; duplicate/stale
  worker cannot mutate;
- completion stores sanitized dimensions/total/diff and projects Score only
  when the row is still greatest ID;
- retryable failure and expired lease become queued when attempts remain,
  otherwise terminal_failed; terminal failure stays terminal; and
- delayed older completion remains historical and cannot overwrite current
  projection.

- [ ] **Step 5: Run state tests and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_cross_check_state.py -q
```

Expected: FAIL because state service does not exist.

- [ ] **Step 6: Implement transactional state machine**

`ensure_cross_check(db, score_id, model, prompt_version, reasons, threshold)`
uses PostgreSQL insert-on-conflict then row lock. New rows start queued,
attempts 0, no lease; merge reasons for an existing row. Only insertion of a
new greatest-ID configuration clears Score projection fields.

`claim_cross_check` uses `SELECT FOR UPDATE` and commits are caller-owned.
`complete_cross_check`/`fail_cross_check` use guarded updates. Completion then
locks Score and verifies `row.id == max(id for score_id)` before projection;
suspicious is `absolute_diff >= threshold_snapshot`.
`sweep_cross_checks(db, now, max_attempts)` handles retryable_failed and expired
running rows deterministically and returns requeued IDs for post-commit
delivery. Stable retry codes:
`provider_unavailable`, `usage_ledger_unavailable`, `database_unavailable`,
`cross_check_unexpected`;
terminal codes: `model_price_missing`, `provider_configuration_error`,
`invalid_secondary_output`, `source_missing`.

- [ ] **Step 7: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_cross_check_sampling.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_cross_check_state.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/cross_check/__init__.py backend/app/services/cross_check/sampling.py backend/app/services/cross_check/state.py backend/tests/unit/test_cross_check_sampling.py backend/tests/integration/test_cross_check_state.py
git commit -m "feat(wp7): add durable cross-check state machine"
```

### Task 11: Secondary worker and atomic trigger hooks

**Files:**
- Create: `backend/app/services/cross_check/worker.py`
- Create: `backend/tests/unit/test_cross_check_worker.py`
- Create: `backend/tests/integration/test_cross_check_triggers.py`
- Modify: `backend/app/tasks/wp7.py`
- Modify: `backend/app/tasks/celery_app.py`
- Modify: `backend/app/scoring/llm_judge.py`
- Modify: `backend/app/scoring/pipeline.py`
- Modify: `backend/app/services/feedback.py`
- Modify: `backend/app/services/golden_set.py`
- Modify: `backend/app/routers/feedback.py`
- Modify: `backend/app/routers/golden_set.py`
- Modify: `backend/app/routers/candidates.py`
- Modify: `backend/app/tasks/ingest.py`
- Modify: `backend/tests/unit/test_feedback_service.py`
- Modify: `backend/tests/unit/test_golden_set_service.py`
- Modify: `backend/tests/integration/test_feedback_api.py`
- Modify: `backend/tests/integration/test_golden_set_api.py`
- Modify: `backend/tests/integration/test_pipeline.py`
- Modify: `backend/tests/integration/test_candidates_api.py`
- Modify: `backend/tests/integration/test_tasks_ingest.py`

- [ ] **Step 1: Write failing worker unit tests**

Mock claim/source/gateway/finalization boundaries. Assert the worker calls the
same `LLMJudge` validation with `model_override=CROSS_ENGINE_MODEL`,
`attempt_role="secondary"`, and cross-check context; reuses stored rule
subtotal, computes total and imports `_grade_from` from `scoring.pipeline`;
persists `secondary_dimensions` containing only
`{id,tier,score,confidence}`—never primary comparisons, evidence, reasoning,
questions, resume, or PII. Comparison rows are derived on read. Assert primary
Score total/grade are unchanged.

Invalid secondary output is validated twice: exactly two paid attempts use the
same cross-check call group, same secondary model, and role `secondary`; it
never calls primary or configured fallback. Cover both structurally
invalid/empty provider responses (`LLMInvalidResponseError`) and schema/domain
invalid outputs (`LLMInvalidOutputError`); two invalid outputs terminate as
`invalid_secondary_output`.

Parameterize this exact catch order/classification and assert the lease token is
always used:

```text
ModelPriceMissing -> terminal model_price_missing
UsageLedgerUnavailable -> retryable usage_ledger_unavailable
LLMConfigurationError -> terminal provider_configuration_error
LLMInvalidResponseError or LLMInvalidOutputError after two secondary-only
  validations -> terminal invalid_secondary_output
LLMUnavailableError -> retryable provider_unavailable
SQLAlchemyError/OSError -> retryable database_unavailable
missing Score/RuleVersion/Candidate/markdown -> terminal source_missing
other Exception -> retryable cross_check_unexpected
```

- [ ] **Step 2: Run worker unit tests and confirm failure**

Run: `uv run pytest backend/tests/unit/test_cross_check_worker.py -q`

Expected: FAIL because worker module does not exist.

- [ ] **Step 3: Implement worker and confirm green**

Claim in one short session/transaction, load Score/RuleVersion/Candidate in a
separate session, close before the paid call, run metered secondary judge, then
open a final short session for guarded completion/failure. Rule subtotal is
`Decimal(str(score.rule_dimensions["subtotal"]))`; add it to the validated
secondary judge subtotal, then call `_grade_from`. Extend `LLMJudge` secondary
mode so its one validation retry calls the same override/role again with no
fallback. Reject malformed/missing source as terminal `source_missing`. Use
bound schema/threshold snapshots.
Run the unit file again; expected PASS.

- [ ] **Step 4: Write failing atomic-trigger integration tests**

Cover:

- Score creation ensures deterministic/low-confidence/current golden triggers
  in the same transaction; injected rollback removes Score and queue row;
- feedback upsert and golden import/update ensure disagreement triggers in the
  same transaction; their services no longer commit internally; injected
  rollback removes both business and queue writes;
- routers and ingestion tasks commit once, then send Celery IDs; broker failure
  leaves committed queued rows for sweeper recovery;
- two concurrent/duplicate `wp7.run_cross_check` deliveries yield one
  successful claim and exactly one paid provider call; the loser exits without
  changing attempts/result;
- eligibility false creates nothing; same config is idempotent and reason union
  deterministic; and
- sweeper task requeues lost delivery, expires leases, also abandons stale
  usage, commits before send, and disposes engine.

- [ ] **Step 5: Run trigger tests and confirm failure**

```powershell
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_cross_check_triggers.py backend/tests/integration/test_feedback_api.py backend/tests/integration/test_golden_set_api.py backend/tests/integration/test_pipeline.py backend/tests/integration/test_candidates_api.py backend/tests/integration/test_tasks_ingest.py -q
```

Expected: FAIL on missing hooks/caller-owned commit behavior.

- [ ] **Step 6: Implement hooks and Celery delivery**

Have pipeline, feedback, and golden services return queued cross-check IDs
without committing. Update every caller to commit business+queue writes first,
then `celery_app.send_task("wp7.run_cross_check", args=[id])`; never send on
rollback. Add `wp7.run_cross_check` and `wp7.sweep_cross_checks` wrappers with
owned sessions, commit/rollback, `engine.dispose()` in finally. The sweep beat
uses exact `float(CROSS_ENGINE_SWEEP_INTERVAL_SECONDS)`.

- [ ] **Step 7: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_cross_check_worker.py backend/tests/unit/test_feedback_service.py backend/tests/unit/test_golden_set_service.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_cross_check_triggers.py backend/tests/integration/test_feedback_api.py backend/tests/integration/test_golden_set_api.py backend/tests/integration/test_pipeline.py backend/tests/integration/test_candidates_api.py backend/tests/integration/test_tasks_ingest.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/services/cross_check/worker.py backend/app/tasks/wp7.py backend/app/tasks/celery_app.py backend/app/scoring/llm_judge.py backend/app/scoring/pipeline.py backend/app/services/feedback.py backend/app/services/golden_set.py backend/app/routers/feedback.py backend/app/routers/golden_set.py backend/app/routers/candidates.py backend/app/tasks/ingest.py backend/tests/unit/test_cross_check_worker.py backend/tests/unit/test_feedback_service.py backend/tests/unit/test_golden_set_service.py backend/tests/integration/test_cross_check_triggers.py backend/tests/integration/test_feedback_api.py backend/tests/integration/test_golden_set_api.py backend/tests/integration/test_pipeline.py backend/tests/integration/test_candidates_api.py backend/tests/integration/test_tasks_ingest.py
git commit -m "feat(wp7): run recoverable cross-engine checks"
```

Expected: all pass and only the listed files are committed.

### Task 12: Suspicious list and bounded backfill APIs

**Files:**
- Create: `backend/app/schemas/cross_check.py`
- Create: `backend/app/routers/cross_check.py`
- Create: `backend/tests/unit/test_cross_check_schemas.py`
- Create: `backend/tests/integration/test_cross_check_api.py`
- Modify: `backend/app/services/cross_check/state.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing schema and API contract tests**

Schema tests pin suspicious item fields: cross-check/score/candidate IDs, JD
code, primary/secondary totals, absolute diff, threshold, sanitized dimension
differences, reasons, model, completion time; page metadata; and backfill counts
`selected/already_existing/would_queue/newly_queued`. Assert content blacklist.

Integration tests cover list roles `hr/hr_lead/admin`, 401, filters for JD,
minimum diff, reason, and half-open completion window; only greatest-ID
completed threshold-meeting rows appear. Backfill is admin-only; requires
timezone-aware increasing max-90-day window, `1 <= limit <=
CROSS_ENGINE_BACKFILL_MAX`; exact 422 detail codes
`invalid_cross_check_window`, `cross_check_window_too_large`,
`invalid_cross_check_limit`. Dry run performs no writes/sends and returns
counts; confirmed mode idempotently queues only new rows with
`admin_backfill`, commits then sends.
For a fixed request and unchanged database, first call `dry_run=true`, then
confirm with identical filters/limit: assert selected/already-existing counts
match and `newly_queued == prior would_queue`.

- [ ] **Step 2: Run tests and confirm failure**

```powershell
uv run pytest backend/tests/unit/test_cross_check_schemas.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_cross_check_api.py -q
```

Expected: FAIL because contracts/routes are missing.

- [ ] **Step 3: Implement list/backfill and role gates**

Use a greatest-ID-per-score subquery, require completed and
`absolute_diff >= threshold_snapshot`, apply filters before WP4 pagination,
order completion/id descending, and project only sanitized fields.

Backfill selects eligible Scores ordered `created_at DESC,id DESC`, capped by
limit/config; counts same-config existing rows, uses `ensure_cross_check` for
confirmed writes, and sends only after commit. Dry run uses rollback/read-only
logic and never calls ensure/send. Register router in `main.py`.

- [ ] **Step 4: Verify and commit**

```powershell
uv run pytest backend/tests/unit/test_cross_check_schemas.py -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest backend/tests/integration/test_cross_check_api.py -q
uv run pytest -m "not integration and not external_contract" -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
git add -- backend/app/schemas/cross_check.py backend/app/routers/cross_check.py backend/app/services/cross_check/state.py backend/app/main.py backend/tests/unit/test_cross_check_schemas.py backend/tests/integration/test_cross_check_api.py
git commit -m "feat(wp7): add cross-engine operations api"
```

Expected: all pass and only the listed files are committed.

---

## Chunk 5: Responsive Operations and Quality Workspace

### Task 13: Responsive grouped shell, Sheet primitive, and API schemas

**Files:**
- Create: `frontend/src/components/app-sidebar.tsx`
- Create: `frontend/src/components/mobile-app-nav.tsx`
- Create: `frontend/src/components/app-session-context.tsx`
- Create: `frontend/src/components/ui/sheet.tsx`
- Create: `frontend/src/components/app-shell.test.tsx`
- Create: `frontend/src/lib/wp7-schemas.test.ts`
- Modify: `frontend/src/components/app-shell.tsx`
- Modify: `frontend/src/app/(app)/layout.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/lib/schemas.ts`

- [ ] **Step 1: Write failing navigation/schema tests**

Test desktop navigation groups/labels/routes and active state; operations link
is visible only to `hr_lead/admin`, while quality/batch/cross-check links are
visible to all authenticated roles. Test mobile menu has an accessible name,
opens/closes with Base UI focus trap/Escape/focus return, and renders the same
allowed links. Test contextual header exposes breadcrumb, last-refresh slot,
user name/role, and logout. A mounted report registers breadcrumb/refresh
metadata; updating its query timestamp updates the header; unmount restores the
default and cannot leave stale metadata.

Schema tests parse representative exact Task 6/8/9/12 payloads, preserve
nullable values, reject missing required metadata, and prove PII/content keys
are stripped by Zod objects. Pin wire types field-by-field:

- operations Decimal-backed costs, rates, percentile/delta values, budget
  amounts, and ratios are JSON strings; counts/tokens/integer latency are
  numbers;
- quality ratios/targets/bin means/gaps/ECE are JSON numbers 0..1 or null,
  while quality operation-cost amounts are strings;
- batch affected percentages are JSON numbers; and
- cross-check totals/differences/thresholds/dimension numeric comparisons are
  JSON strings, while IDs/counts are numbers.

- [ ] **Step 2: Run tests and confirm failure**

```powershell
Set-Location frontend
npm run test -- src/components/app-shell.test.tsx src/lib/wp7-schemas.test.ts
```

Expected: FAIL because grouped shell, Sheet, and schemas do not exist.

- [ ] **Step 3: Implement shell and schemas**

At `lg` and above render a fixed 208px sidebar with groups Recruitment,
Review & Rules, Operations & Quality; below `lg` render a compact header and
Sheet trigger. Use Lucide icons, existing Geist fonts, 44px minimum touch
targets, one cobalt interactive accent, text+icon status, 150–200ms transitions,
and `motion-reduce:transition-none`. Main content is fluid with readable max
width, not a generic card grid.

Implement Sheet from `@base-ui/react/dialog`, matching existing Dialog API:
Root/Trigger/Close/Backdrop/Popup/Title/Description; right-side and left-side
variants; Trigger uses `render`, buttons use `onClick`/`render`, never
`asChild`. Add visually stable focus rings and scroll lock.

Add exact Zod contracts corresponding to backend schemas and export inferred
types. `AppSessionProvider` exposes typed `{displayName,role}` plus
`useShellHeader({breadcrumbs,lastRefreshedAt})`. It owns header state; the hook
registers in `useEffect`, updates on value changes, and restores defaults on
cleanup. `AppShell` renders that state. The server layout passes the verified
session and wraps `AppShell`/children.
Add `export const dynamic = "force-dynamic"` to that cookie-reading layout.
Preserve all existing links/routes.

- [ ] **Step 4: Verify and commit**

```powershell
Set-Location frontend
npm run test -- src/components/app-shell.test.tsx src/lib/wp7-schemas.test.ts
npm run lint
npm run typecheck
npm run test
git add -- src/components/app-sidebar.tsx src/components/mobile-app-nav.tsx src/components/app-session-context.tsx src/components/ui/sheet.tsx src/components/app-shell.tsx src/components/app-shell.test.tsx 'src/app/(app)/layout.tsx' src/app/globals.css src/lib/schemas.ts src/lib/wp7-schemas.test.ts
git commit -m "feat(wp7): add responsive operations shell"
```

Expected: all frontend gates pass and only the listed files are committed.

### Task 14: Operations cost and usage workspace

**Files:**
- Create: `frontend/src/components/operations/metric-rail.tsx`
- Create: `frontend/src/components/operations/budget-status.tsx`
- Create: `frontend/src/components/operations/cost-trend.tsx`
- Create: `frontend/src/components/operations/usage-ledger.tsx`
- Create: `frontend/src/components/operations/operations-view.tsx`
- Create: `frontend/src/components/operations/operations-view.test.tsx`
- Create: `frontend/src/app/(app)/reports/operations/page.tsx`

- [ ] **Step 1: Write failing component tests**

Mock API responses and test:

- window tabs today/7d/30d update query key and accessible selected state;
- four-metric rail shows current values, exact prior deltas, unknown counts,
  and no invented percentage when prior is zero;
- daily semantic SVG/CSS trend has accessible name, exact-value table/list,
  anomaly summary, and no color-only signal;
- daily/monthly budgets show normal/warning/exceeded text+icon+color and
  unknown-cost caveat;
- operation/model/outcome breakdown is readable without hover;
- filter Sheet applies/clears filters and shows active count; desktop ledger is
  semantic table, mobile is expandable list; pagination/order/empty/error/retry
  use `DataState`; and
- no candidate/private fixture content renders.
- workspace registers breadcrumb `Operations / Usage & Cost`, updates shell
  refresh time from the newest successful summary/usage `dataUpdatedAt`, and
  cleans it on unmount.

Pin the four metrics to known CNY cost, attempt count, success rate
`succeeded_count / (attempt_count - pending_count)`, and terminal-known p95
latency. Success rate is null when the non-pending denominator is zero.
Cost/attempt deltas use backend values. Success/p95 absolute and percentage
deltas are both null when current or previous is null; otherwise absolute is
current minus previous, while percentage is `(current - previous) / previous`
and remains null when previous is zero. A trend point is anomalous only when known
cost is at least 50% above the immediately preceding nonzero day; the adjacent
summary states both exact values.

- [ ] **Step 2: Run test and confirm failure**

Run from frontend:
`npm run test -- src/components/operations/operations-view.test.tsx`

Expected: FAIL because components/page do not exist.

- [ ] **Step 3: Implement the operations page**

`page.tsx` is a thin client entry rendering `OperationsView`.
TanStack Query calls summary and usage endpoints with validated schemas.
Call `useShellHeader` with the breadcrumb and maximum successful query
`dataUpdatedAt`.
The default usage range is the summary's `current_start/current_end`; changing
window resets page to 1. Map filters exactly to `from`, `to`, `operation`,
`requested_model`, `actual_model`, `status`, `attempt_role`, `trace_id`,
`ingestion_job_id`, `score_id`, `jd_code`, `page`, and `page_size` (default
25), omitting only empty optional values.
Composition order: compact status strip; metric rail; cost/anomaly trend with
adjacent exact values; budget comparison; operation/model/outcome sections;
usage ledger. Keep primary filters visible on wide screens and move all filters
to right Sheet on mobile. Use tabular numbers/monospace IDs, sticky table
header, horizontal overflow only where necessary, skeletons with stable height,
and URL/query state that survives refresh.

- [ ] **Step 4: Verify and commit**

```powershell
Set-Location frontend
npm run test -- src/components/operations/operations-view.test.tsx
npm run lint
npm run typecheck
npm run test
git add -- src/components/operations/metric-rail.tsx src/components/operations/budget-status.tsx src/components/operations/cost-trend.tsx src/components/operations/usage-ledger.tsx src/components/operations/operations-view.tsx src/components/operations/operations-view.test.tsx 'src/app/(app)/reports/operations/page.tsx'
git commit -m "feat(wp7): add operations cost workspace"
```

### Task 15: Immutable quality release workspace

**Files:**
- Create: `frontend/src/components/quality/confidence-bins.tsx`
- Create: `frontend/src/components/quality/release-sheet.tsx`
- Create: `frontend/src/components/quality/quality-release-view.tsx`
- Create: `frontend/src/components/quality/quality-release-view.test.tsx`
- Create: `frontend/src/app/(app)/reports/quality/page.tsx`

- [ ] **Step 1: Write failing component tests**

Test latest release first, target status text/icons, F1/evidence/agreement/ECE
with denominators, fixed confidence bins including insufficient state,
current/prior trend metrics, horizontally scrollable per-JD table with sticky
JD column, history selection, empty/error/retry, and content blacklist.

For `hr_lead/admin`, test Create opens right Sheet, validates JD/window, calls
preview, displays resolved bounds/bindings/counts/targets, then opens immutable
confirmation Dialog. Confirm posts *resolved* bounds plus fingerprint. A
`release_input_changed` error refreshes preview and disables stale confirmation.
Below-target 201 renders saved warning, not failure. Plain `hr` has no create
control. Test focus trap/Escape/return for Sheet and Dialog.
Test breadcrumb `Operations / Quality Releases`, latest successful list/detail
query refresh time, updates, and cleanup through `useShellHeader`.

- [ ] **Step 2: Run test and confirm failure**

Run:
`npm run test -- src/components/quality/quality-release-view.test.tsx`

Expected: FAIL because quality components/page do not exist.

- [ ] **Step 3: Implement the quality page**

Use TanStack Query for list/detail/preview and mutation for create; invalidate
list/detail on success. Preserve preflight inputs, display fingerprint as
abbreviated mono metadata, and state plainly that release is append-only.
Register the quality breadcrumb and maximum successful list/detail
`dataUpdatedAt` through `useShellHeader`.
Never display snapshot entries. Visual hierarchy uses one metric rail, thin
section dividers, restrained status badges, and semantic tables—not nested card
grids. `page.tsx` passes the role available from the authenticated shell
context from Task 13.

- [ ] **Step 4: Verify and commit**

```powershell
Set-Location frontend
npm run test -- src/components/quality/quality-release-view.test.tsx
npm run lint
npm run typecheck
npm run test
git add -- src/components/quality/confidence-bins.tsx src/components/quality/release-sheet.tsx src/components/quality/quality-release-view.tsx src/components/quality/quality-release-view.test.tsx 'src/app/(app)/reports/quality/page.tsx'
git commit -m "feat(wp7): add quality release workspace"
```

### Task 16: Batch and cross-check workspaces

**Files:**
- Create: `frontend/src/components/batch-report-view.tsx`
- Create: `frontend/src/components/batch-report-view.test.tsx`
- Create: `frontend/src/components/cross-check-view.tsx`
- Create: `frontend/src/components/cross-check-view.test.tsx`
- Create: `frontend/src/app/(app)/reports/batch/page.tsx`
- Create: `frontend/src/app/(app)/reports/cross-checks/page.tsx`

- [ ] **Step 1: Write failing batch/cross-check tests**

Batch tests require at least one bounded filter, show grade distribution and
ranked exact reason type/key/count/affected percentage, explain overlapping
percentages, keep filters in mobile Sheet, and render no free text evidence or
reasoning.

Cross-check tests cover filters/pagination, desktop table/mobile expandable
list, right inspector with sanitized primary/secondary dimension comparisons,
status/reason text, and audited scorecard link
`/candidates/{candidate_id}/scores/{score_id}`. Admin backfill Sheet first calls
`dry_run=true`; confirmation Dialog sends identical filters with false and
shows matching preview/queued counts. Non-admin has no backfill control.
Test empty/error/retry, focus behavior, and leak blacklist.
After a successful dry run, changing any JD/window/limit filter disables
confirmation and clears preview counts; a new `dry_run=true` response is
required before confirmed submission.
Each view test asserts its own breadcrumb (`Operations / Batch Analysis` or
`Operations / Cross-Engine`), latest successful primary-query `dataUpdatedAt`,
subsequent update, and cleanup.

- [ ] **Step 2: Run tests and confirm failure**

```powershell
Set-Location frontend
npm run test -- src/components/batch-report-view.test.tsx src/components/cross-check-view.test.tsx
```

Expected: FAIL because views/pages do not exist.

- [ ] **Step 3: Implement both bounded workspaces**

Use schema-validated TanStack queries, URL-backed filters, semantic desktop
tables, mobile lists, and shared Sheet/Dialog/DataState patterns. Render the
sanitized comparison rows already returned by the suspicious API; never
request/embed candidate detail in that endpoint. Candidate drill-down is only
the audited scorecard link. Keep batch reasons deterministic and backfill
preview immutable until any filter changes. Each view calls `useShellHeader`
with its breadcrumb and successful report/list `dataUpdatedAt`.

- [ ] **Step 4: Verify and commit**

```powershell
Set-Location frontend
npm run test -- src/components/batch-report-view.test.tsx src/components/cross-check-view.test.tsx
npm run lint
npm run typecheck
npm run test
git add -- src/components/batch-report-view.tsx src/components/batch-report-view.test.tsx src/components/cross-check-view.tsx src/components/cross-check-view.test.tsx 'src/app/(app)/reports/batch/page.tsx' 'src/app/(app)/reports/cross-checks/page.tsx'
git commit -m "feat(wp7): add quality operations workspaces"
```

---

## Chunk 6: End-to-End Verification and Documentation

### Task 17: Desktop/mobile E2E, full gates, and In-progress documentation

**Files:**
- Create: `frontend/e2e/wp7-operations.spec.ts`
- Create: `frontend/e2e/wp7-quality.spec.ts`
- Modify: `frontend/e2e/fixtures/stub-backend.ts`
- Modify: `frontend/e2e/a11y.spec.ts`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`

**Acceptance exception:** Tasks 13–16 already used red-first component TDD.
Task 17 adds post-implementation browser acceptance coverage, so its new E2E
tests are expected to pass immediately and are the sole exception to the global
red-first rule. Any exposed defect re-enters red/green component TDD in Step 4.

- [ ] **Step 1: Extend deterministic E2E fixtures first**

Add route fixtures for every WP7 endpoint with exact backend wire types and
PII-free response bodies. Keep leak-canary values only in simulated unreturned
source records, never in the fulfilled JSON. Provide role-specific signed
sessions for `hr`, `hr_lead`, and `admin`. Track preview/create/backfill request
bodies so tests can assert resolved bounds/fingerprint and dry-run filter
identity. Capture every successful fulfilled response body and assert forbidden
keys/values are absent before the browser renders it. Do not call the real
backend.

- [ ] **Step 2: Add operations browser acceptance E2E**

`wp7-operations.spec.ts` runs in both configured desktop and Pixel 7 projects:

- lead/admin grouped navigation reaches Usage & Cost; `hr` cannot see/reach the
  role-gated link;
- window/filter/pagination requests contain exact parameters, metrics/budgets/
  trend/breakdowns render; intercepted responses and DOM both pass the leak
  blacklist;
- desktop uses semantic table/sidebar; mobile opens filter/nav Sheets,
  expandable usage rows, Escape closes, and focus returns;
- loading, empty, error/retry states remain usable; and
- 375, 768, 1024, and 1440 targeted viewport checks have no horizontal page
  overflow (only designated table scrollers).

Run from `frontend/`:
`npm run e2e -- wp7-operations.spec.ts`

These are post-implementation acceptance tests. Expected: PASS; any failure is
a product/contract defect handled in Step 4, not an artificial red phase.

- [ ] **Step 3: Add quality/analysis browser acceptance E2E**

`wp7-quality.spec.ts`, in both projects, covers:

- `hr` reads release history but cannot create; lead/admin preview returns
  resolved bounds/fingerprint, confirmation posts them, below-target save is a
  visible success-warning, and stale fingerprint forces a new preview;
- batch bounded filters, overlapping reason explanation, grade distribution,
  and no free-text content;
- suspicious inspector, audited scorecard link, admin dry-run then identical
  confirmed backfill, and filter change invalidates preview;
- Sheet/Dialog keyboard focus/Escape/return and mobile tables/lists; and
- no leak canary, evidence, reasoning, ciphertext, object key, or prompt.
  Assert both intercepted successful response JSON and rendered DOM.

Run:
`npm run e2e -- wp7-quality.spec.ts`

These are post-implementation acceptance tests. Expected: PASS; any failure is
handled in Step 4.

- [ ] **Step 4: Complete fixtures/tests and extend accessibility coverage**

Do not weaken an assertion to match a defect. If acceptance exposes wrong
production behavior, first add/extend the owning Task 13–16 component test,
confirm red, fix production code, rerun that component test plus lint/typecheck,
then create a dedicated
`fix(wp7): resolve frontend acceptance regression` commit before
continuing. Stage only the affected paths from the explicit Task 13–16 file
inventories and record them in the task log; never mix that fix into the final
E2E/docs commit.

Make fixture corrections only when the fixture contradicts the reviewed
backend wire contract. Add all four report routes to `a11y.spec.ts`; run axe in
desktop and mobile projects with no serious/critical violations. Assert
landmarks, one page heading, dialog names, status text, chart accessible names,
keyboard reachability, and reduced-motion behavior.

- [ ] **Step 5: Run the complete backend gate**

From repository root:

```powershell
uv run pytest -m "not integration and not external_contract" -q
$env:DATABASE_URL='postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:DATABASE_URL_SYNC='postgresql://smartscreen:smartscreen@127.0.0.1:25432/smartscreen_test'
$env:MINIO_ENDPOINT='127.0.0.1:9000'
uv run pytest -m integration -q
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
```

Expected: offline and full integration suites pass; ruff and mypy are clean.
Record exact pass counts for handoff.

- [ ] **Step 6: Run the complete frontend gate**

From `frontend/`:

```powershell
npm run lint
npm run typecheck
npm run test
npm run e2e
npm run build
```

Allow the Playwright webServer build/start up to 180 seconds. Expected: lint,
typecheck, every Vitest file, both Playwright projects, and production build
pass. Record exact Vitest/E2E counts.

- [ ] **Step 7: Update documentation as In progress**

In `README.md`, replace “WP7 Ready for planning” with a concise implemented
surface summary and **WP7 In progress**. Add runtime price/budget/cross-engine
configuration, non-blocking budget semantics, operations/quality/batch/
cross-check routes, migration/head, and local gate evidence from Steps 5–6.

In the roadmap, set WP7 to **In progress**; link this approved design and plan;
summarize immutable attempt ledger/releases, deterministic batch reasons,
recoverable cross-check queue, responsive UI, and local gate evidence. State
plainly that hosted CI, push/PR, merge, and Complete marking remain human work.
Do not claim hosted CI or Complete.

- [ ] **Step 8: Commit and stop before publication**

```powershell
git add -- frontend/e2e/wp7-operations.spec.ts frontend/e2e/wp7-quality.spec.ts frontend/e2e/fixtures/stub-backend.ts frontend/e2e/a11y.spec.ts README.md docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md
git commit -m "test(wp7): add end-to-end operations coverage"
git status --short --branch
git log --oneline main..HEAD
```

Expected: commit succeeds; only `.superpowers/` and `backend.zip` may remain
untracked. Stop and report every task SHA, backend/frontend gate counts, and
every plan-to-repository correction. Do **not** push, create a PR, run hosted
CI, merge, or mark WP7 Complete.
