# SmartScreenAgent Delivery Plan Index

This directory tracks active delivery plans. Current scope and dependency decisions come from [`../specs/2026-07-13-current-state-and-roadmap-design.md`](../specs/2026-07-13-current-state-and-roadmap-design.md).

## Status Values

- **Ready:** specification is approved and an executable implementation plan exists.
- **Ready for planning:** predecessor gates passed, but the package specification or implementation plan is not yet approved.
- **Blocked:** work package depends on an incomplete predecessor.
- **In progress:** implementation has started and plan checkboxes are being maintained.
- **Complete:** exit gate passed and the roadmap traceability matrix was updated.

Historical plans may contain unchecked boxes even when their work was committed. They are marked with historical-status banners and are not tracked here.

## Ordered Work Packages

| Order | Package | Status | Depends on | Plan |
|---:|---|---|---|---|
| 0 | WP0 Reproducible integration baseline | Complete | Current repository | [`2026-07-13-wp0-integration-baseline.md`](2026-07-13-wp0-integration-baseline.md) |
| 1 | WP1 Security and raw-file integrity | Complete | WP0 | Approved [specification](../specs/2026-07-16-wp1-security-and-raw-file-integrity-design.md) and [completion evidence](2026-07-16-wp1-security-and-raw-file-integrity.md#completion-evidence) |
| 2 | WP2 Production parser contract and validated AI output | Complete | WP1 | Approved [specification](../specs/2026-07-16-wp2-production-parser-and-validated-ai-output-design.md) and [completion evidence](2026-07-16-wp2-production-parser-and-validated-ai-output.md#completion-evidence-2026-07-20) |
| 3 | WP3 Durable asynchronous ingestion and batch processing | Complete | WP2 | [`2026-07-20-wp3-durable-async-ingestion.md`](2026-07-20-wp3-durable-async-ingestion.md) |
| 4 | WP4 Read APIs and rule lifecycle | Complete | WP1 and WP3 | Approved [specification](../specs/2026-07-21-wp4-read-apis-design.md) and [implementation plan](2026-07-21-wp4-read-apis.md) |
| 5 | WP5 HR web workspace | Complete | WP4 | Approved [specification](../specs/2026-07-21-wp5-hr-web-workspace-design.md) and [implementation plan](2026-07-21-wp5-hr-web-workspace.md) |
| 6 | WP6 Review, golden set, and rule simulation | In progress (WP6a Complete, WP6b In progress) | WP4 and WP5 | WP6a Complete: approved [specification](../specs/2026-07-22-wp6a-feedback-capture-design.md), [implementation plan](2026-07-22-wp6a-feedback-capture.md), hosted CI [run 29899061689](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29899061689) green (PR #6). WP6b: local gate passed, approved [specification](../specs/2026-07-22-wp6b-golden-set-baseline-metrics-design.md) and [implementation plan](2026-07-22-wp6b-golden-set-baseline-metrics.md); hosted CI pending. WP6c not yet planned |
| 7 | WP7 Cost, calibration, and operational reporting | Blocked | WP3 and WP6 | Written after labeled feedback exists |
| 8 | WP8 DingTalk recruitment sync and MCP/Hermes | Blocked | WP3 and WP4 | Written after stable application services exist |
| 9 | WP9 JD intelligence and cross-position recommendation | Blocked | WP6 and WP7 | Written after offline evaluation data exists |

## Current Evidence

On 2026-07-13, the strict local WP0 gate passed 102 non-integration tests and 16 integration tests with zero skips, plus static, migration, and clean-state checks. Hosted [GitHub Actions run 29237545679](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29237545679) then passed both Python matrix jobs and the integration job at commit `b3447ec`. Exact results are recorded in the [WP0 completion evidence](2026-07-13-wp0-integration-baseline.md#completion-evidence).

On 2026-07-16, the approved WP1 implementation passed the local strict gate: 142 non-integration tests and 36 integration tests with zero skips, Alembic head `b57c2f9e1a6d`, Ruff, mypy, real PostgreSQL/Redis/Celery/MinIO checks, and post-run clean-state checks. The implementation was split into scoped commits, hosted [GitHub Actions run 29474031067](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29474031067) passed Python 3.10, Python 3.14, and strict integration, and the configured deployment database upgraded with zero candidate and legacy rows requiring disposition. WP1 is Complete and WP2 is Ready for planning; exact evidence is in the [WP1 plan](2026-07-16-wp1-security-and-raw-file-integrity.md#completion-evidence).

On 2026-07-20, WP2 completed. Offline suite 233 passed with Ruff and mypy clean on Python 3.10 and 3.14; the local Windows/Python 3.14 external-contract gate passed 9/9 (MinerU official API v4 four formats and new-api `gpt-5.6-sol` structured output). Hosted [GitHub Actions run 29714208508](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29714208508) passed both Python matrix jobs and strict integration at commit `57f9afe` (WP2 range `2145b74..57f9afe`, PR #2). WP2 is Complete and WP3 is Ready for planning; exact evidence is in the [WP2 plan](2026-07-16-wp2-production-parser-and-validated-ai-output.md#completion-evidence-2026-07-20).

On 2026-07-21, WP3 completed. The durable async ingestion path (state machine, `IngestionJobService`, worker orchestration, idempotent score upsert, Beat sweeper with stranded-`queued` recovery, async upload/batch/status endpoints) passed the local gate: 252 offline and 64 integration tests (real PostgreSQL/Redis/MinIO/worker, including end-to-end and crash-recovery), Ruff, mypy, and the Alembic round trip to head `2f27938b430b`. Hosted [GitHub Actions run 29795950194](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29795950194) passed Python 3.10, Python 3.14, and strict integration at commit `4bd7130` (WP3 range `5c57cab..4bd7130`, PR #3). WP3 is Complete and WP4 is Ready for planning.

On 2026-07-21, WP4 completed. The nine read routes (JD-scoped ranked candidate list, flat candidate list, candidate detail with audited PII decrypt, score detail with evidence, audited raw-file presigned download, JD list/detail, rule-version list, and rule-version diff), their response models, and the read-service layer are implemented per the [WP4 design](../specs/2026-07-21-wp4-read-apis-design.md) and [implementation plan](2026-07-21-wp4-read-apis.md). The raw-file endpoint verifies object existence before presigning (missing object -> 404, unreachable store -> 503, neither audited). The local gate passed: 260 offline tests; the full integration suite (OpenAPI contract test over all nine routes, rule-version `is_active`/`published_at`-ordering/unknown-version-404 assertions, object-missing-404 and all-nine-routes-401 sweeps, and a presigned-URL-not-logged leak test) 90 passed against real PostgreSQL/Redis/MinIO; Ruff and mypy clean for 79 application source files. Hosted [GitHub Actions run 29813134361](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29813134361) passed Python 3.10, Python 3.14, and strict integration at commit `24ce55d` (WP4 range `3db197c..24ce55d`, PR #4). WP4 is Complete and WP5 is Ready for planning.

On 2026-07-22, WP5 completed. The `frontend/` Next.js 15 App Router BFF (DingTalk login handoff, HMAC-signed httpOnly session cookie, server-side JWT proxy with a traversal-hardened `/api/v1/` allowlist, PII-free candidate lists, audited candidate detail, scorecard with hard-filter/rule/judge evidence, per-JD re-score, upload with per-file job-status polling, audited raw-file presigned-URL redirect) is built per the [WP5 design](../specs/2026-07-21-wp5-hr-web-workspace-design.md) and [implementation plan](2026-07-21-wp5-hr-web-workspace.md). Every FastAPI-bound route is `readSession`-gated and clears the cookie on upstream 401; `getServerEnv()` fails closed on a placeholder/short session secret; upstream fetches are `cache: "no-store"`; logout clears the query cache. The local gate passed: lint and typecheck clean; Vitest 34 tests across 14 files; Playwright e2e 4 tests (golden-path + accessibility, desktop and mobile projects) exercising candidate list -> PII detail -> evidence scorecard against a stubbed BFF with a real minted signed cookie, no token/presigned-URL leak into the DOM, and zero serious/critical axe violations; `next build` (standalone) succeeded. WP5 changes no backend code; the backend [GitHub Actions run 29880568935](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29880568935) on PR #5 confirmed no backend regression (Python 3.10, 3.14, strict integration green). A dedicated frontend hosted-CI job is a documented follow-up; the local gate is the exit evidence. Built via subagent-driven development (8 task-scoped reviews + an opus whole-branch review; no Critical, 2 Important + 2 Minor fixed). WP5 range `bae8068..639436a`, PR #5. WP5 is Complete and WP6 is Ready for planning.

WP6 was decomposed into three independently shippable sub-projects: WP6a (feedback capture and minimal reporting), WP6b (golden set and baseline metrics), and WP6c (rule publication workflow, What-If, and regression gates, which depends on WP6b). On 2026-07-22, **WP6a implementation completed its local gate and is In progress** (not yet Complete — hosted CI verification is pending and owned by the controller). The [WP6a design](../specs/2026-07-22-wp6a-feedback-capture-design.md) and [implementation plan](2026-07-22-wp6a-feedback-capture.md) add a per-score feedback `PUT`/`GET` API (decision advance/reject/hold + reason, server-derived `ai_agreed`, one row per `(score_id, reviewer_user_id)` via upsert, `422 feedback_reason_required` on an unexplained disagreement), a PII-free `GET /api/v1/feedback/report` aggregate (overall/per-JD agreement rate, paginated disagreement list), a small Alembic migration (`1e9b39dbf340`, revises `2f27938b430b`, adds the `(score_id, reviewer_user_id)` uniqueness constraint and a `decision` CHECK), a `FeedbackPanel` on the WP5 scorecard, and a new `/reports/feedback` report page with a Playwright e2e (`frontend/e2e/feedback.spec.ts`). Local gate on 2026-07-22: backend offline suite 264 passed; the full integration suite 96 passed against real PostgreSQL/Redis/MinIO; Ruff and mypy clean for 82 application source files; frontend lint/typecheck clean, Vitest 36 tests across 16 files, Playwright e2e 6 tests (3 specs, desktop + mobile projects) passed, `next build` succeeded. WP6a is In progress; it will be marked Complete, and WP6b Ready for planning, only after hosted CI passes.

On 2026-07-22, **WP6b implementation completed its local gate and is In progress** (not yet Complete — hosted CI verification is pending and owned by the controller). The [WP6b design](../specs/2026-07-22-wp6b-golden-set-baseline-metrics-design.md) and [implementation plan](2026-07-22-wp6b-golden-set-baseline-metrics.md) add a curated ground-truth golden set distinct from WP6a's per-reviewer feedback: `POST /api/v1/golden-set/import` (role `hr_lead`/`admin`, multipart CSV of `candidate_id, jd_code, label`, per-row validation with a partial-success summary, upsert on the existing `uq_golden_set_cand_jd` constraint, capped by `GOLDEN_IMPORT_MAX_ROWS`), a PII-free paginated `GET /api/v1/golden-set` list, and a live-computed `GET /api/v1/golden-set/metrics` (confusion matrix, precision/recall/F1/accuracy overall and per-JD, `borderline` excluded, `uncovered` reported, most-recent-score selection), reads role-gated `hr`/`hr_lead`/`admin`; a small Alembic migration (`f412481450cf`, revises WP6a's `1e9b39dbf340`, adds a `label` CHECK constraint restricting values to `advance`/`reject`/`borderline`); a `/golden-set` import+list page (import gated to `hr_lead`/`admin`) and a `/reports/baseline` metrics page with a Playwright e2e (`frontend/e2e/golden-set.spec.ts`). Local gate on 2026-07-22: backend offline suite 268 passed; the full integration suite 101 passed against real PostgreSQL/Redis/MinIO; Ruff and mypy clean for 85 application source files; frontend lint/typecheck clean, Vitest 39 tests across 18 files, Playwright e2e 8 tests (4 specs, desktop + mobile projects) passed, `next build` succeeded. WP6b is In progress; it will be marked Complete, and WP6c Ready for planning, only after hosted CI passes.

## Planning Rules

1. Create one feature specification and one implementation plan per work package.
2. Do not write detailed plans for blocked packages; later contracts must reflect the implementation decisions of their predecessors.
3. Update the active plan's checkboxes during execution.
4. On completion, record verification evidence in the plan and update the authoritative roadmap matrix.
5. A skipped integration suite does not satisfy a package exit gate.

## Dependency View

```text
WP0 -> WP1 -> WP2 -> WP3 -> WP4 -> WP5 -> WP6 -> WP7
                         |      |
                         +----> WP8

WP6 + WP7 -> WP9
```

WP4 design may overlap the end of WP3, and WP5 visual design may overlap the end of WP4. Implementation cannot cross an incomplete dependency gate.
