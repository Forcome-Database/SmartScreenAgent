# WP6a Feedback Capture and Minimal Reporting Design

**Date:** 2026-07-22

**Status:** Draft (pending approval)

**Work package:** WP6a (first sub-project of WP6: Review, golden set, and rule simulation)

**Depends on:** WP4 read APIs (scores, JDs), WP5 HR web workspace (scorecard UI)

## 1. Purpose

WP6a lets an HR reviewer record a verdict and reason on each score, so human
feedback becomes **attributable** (who decided, when) and **reportable**
(AI-versus-HR agreement). It closes the first half of WP6's exit gate — "HR
feedback is attributable and reportable" — and produces the labeled signal that
later sub-projects consume: WP6b (golden set) and WP7 (calibration, consistency
and cost reports).

WP6a is a full-stack slice: a backend feedback API (upsert + list + a minimal
aggregate report) plus a frontend extension of the WP5 scorecard and a simple
report page. It changes no scoring behavior and does not mutate scores; feedback
is a separate record.

WP6 as a whole was decomposed into three independently shippable sub-projects —
WP6a (feedback capture, this spec), WP6b (golden set and baseline metrics), and
WP6c (rule publication workflow, What-If, and regression gates, which depends on
WP6b). WP6a is independent of WP6b/WP6c.

## 2. Baseline and Gaps

### 2.1 Baseline

- The `Feedback` model already exists as a schema stub:
  `feedback(id, score_id → scores.id, reviewer_user_id → users.id,
  decision: str(32), reason: text | null, ai_agreed: bool | null,
  created_at, updated_at)`. It has no unique constraint, no API, no UI.
- The `Score` model holds `grade: str(16)`, `total_score`,
  `hard_filter_result`, `rule_dimensions`, `judge_dimensions`, and JD/rule
  linkage. There is no `rejected` column; the scoring pipeline sets
  `grade == "rejected"` when a candidate is hard-filter rejected, and a tier
  grade (e.g. L1–L5) otherwise.
- WP5 ships the scorecard page (`/candidates/[id]/scores/[sid]`) and its read
  API (`GET /api/v1/candidates/{id}/scores/{score_id}`), plus the BFF proxy,
  auth (`require_roles`), and the app shell an HR user works in.

### 2.2 Gaps

- There is no way for HR to record a verdict/reason on a score, no attribution,
  and no AI-versus-HR agreement signal.
- There is no feedback API and no report.
- `Feedback` has no uniqueness guarantee, so "one decision per reviewer per
  score" is not enforced.

## 3. Goals

- Capture, per score and per reviewer, an HR verdict (`advance | reject | hold`)
  and an optional reason, upserted so a reviewer can revise their own decision.
- Derive and store `ai_agreed` server-side by comparing the HR verdict to the
  AI verdict; require a reason when the reviewer disagrees with the AI.
- Attribute every feedback row to its reviewer and timestamp; allow multiple
  reviewers per score.
- Provide a minimal aggregate report: overall and per-JD agreement rate, counts
  by decision, and a paginated list of disagreements — no PII.
- Surface it in the WP5 UI: a feedback control on the scorecard and a simple
  report page.

## 4. Non-goals

- Golden-set import, the three-label workflow, and baseline metrics (WP6b).
- Rule draft/publication states, controlled publication, What-If simulation, and
  regression gates (WP6c).
- Cost/usage ledger, confidence calibration, cross-engine scoring, and the
  richer AI-HR consistency/trend reports (WP7).
- Mutating a score/grade from feedback, or auto-seeding the golden set from
  feedback (a later, deliberate step).
- Any change to WP1–WP5 contracts beyond the additive feedback routes, or to the
  scoring algorithm.

## 5. Data Model and Migration

`Feedback` is reused. One small Alembic migration adds:

- `UniqueConstraint(score_id, reviewer_user_id)` named
  `uq_feedback_score_reviewer` — enforces one row per reviewer per score and is
  the conflict target for the upsert.
- A `CHECK` constraint `ck_feedback_decision` restricting `decision` to
  `('advance', 'reject', 'hold')`.

`ai_agreed` stays nullable (null when `decision = 'hold'`). No other column
changes. The migration has a working downgrade (drop the two constraints).

## 6. AI-agreement Derivation

Computed server-side (never trusted from the client). The AI verdict is derived
from the score's grade:

- **AI reject** ⟺ `score.grade == "rejected"`; otherwise **AI advance**.

Agreement:

| HR decision | AI verdict | `ai_agreed` |
|---|---|---|
| `advance` | advance | `true` |
| `reject` | reject | `true` |
| `advance` | reject | `false` |
| `reject` | advance | `false` |
| `hold` | (either) | `null` |

A `hold` never counts toward the agreement rate (it is excluded from both
numerator and denominator). When `ai_agreed` is `false`, `reason` is required
(the API returns `422` if it is missing or blank).

(If the product later wants "below `passing_threshold` also counts as AI
reject", that is a one-line change to the AI-verdict derivation; WP6a uses the
`grade == "rejected"` signal, which is the pipeline's explicit rejection.)

## 7. Backend API

All routes require Bearer JWT with role in `("hr", "hr_lead", "admin")`, mirror
the WP4/WP5 error and pagination conventions (`{code, message}` errors; offset
pagination `{items, page, page_size, total}`), and change no existing route.

### 7.1 Upsert feedback

`PUT /api/v1/candidates/{candidate_id}/scores/{score_id}/feedback`

Body `{decision: "advance"|"reject"|"hold", reason?: string}`. Validates the
score exists and belongs to the candidate (`404` otherwise). Computes the AI
verdict from `score.grade`, derives `ai_agreed`, and — if `ai_agreed` is
`false` — requires a non-blank `reason` (`422 feedback_reason_required`).
Upserts on `(score_id, reviewer_user_id = current user)`; returns the stored
feedback `{id, score_id, reviewer, decision, reason, ai_agreed, updated_at}`.

### 7.2 List a score's feedback

`GET /api/v1/candidates/{candidate_id}/scores/{score_id}/feedback`

Returns all reviewers' feedback for the score, each
`{id, reviewer_user_id, reviewer_display_name, decision, reason, ai_agreed,
created_at, updated_at}`, ordered by `updated_at` descending. `404` if the score
does not exist or does not belong to the candidate. No candidate PII.

### 7.3 Aggregate report

`GET /api/v1/feedback/report?jd_code=&page=&page_size=`

Returns:
- `overall`: `{total, agreed, disagreed, hold, agreement_rate}` where
  `agreement_rate = agreed / (agreed + disagreed)` (null/0 when the denominator
  is 0).
- `by_jd`: a list of `{jd_code, total, agreed, disagreed, hold, agreement_rate}`.
- `disagreements`: a paginated list of the `ai_agreed = false` feedback rows —
  `{feedback_id, score_id, candidate_id, jd_code, decision, reason,
  reviewer_display_name, updated_at}` — ordered by `updated_at` descending.
  Optional `jd_code` filter scopes all three sections.

No candidate PII appears in the report (only `candidate_id`/`jd_code`/`score_id`
references, as in WP5 lists).

## 8. Architecture

- Backend: a new `backend/app/routers/feedback.py` and a
  `backend/app/services/feedback.py` (verdict derivation, upsert, list, and the
  report aggregation). Response models in `backend/app/schemas/feedback.py`. The
  service depends on the `Feedback`, `Score`, `JD`, and `User` models. This
  follows the roadmap's repository/service convention already used by WP4's read
  services.
- Frontend (extends WP5, `frontend/`): a `FeedbackPanel` client component on the
  scorecard page (decision radio/select + reason + submit via a new BFF path,
  plus the list of existing feedback), the report page
  `src/app/(app)/reports/feedback/page.tsx`, a nav entry, zod schemas for the
  feedback and report responses, and TanStack Query mutations/queries hitting
  the generic `/api/proxy` (the feedback routes are `/api/v1/*`, so they pass
  the existing proxy allowlist unchanged — no new BFF route needed).

## 9. Errors, Authorization, and Leak Safety

- All routes `require_roles("hr", "hr_lead", "admin")`; unauthorized →
  `401`/`403`.
- Unknown score/candidate → `404 {code, message}`; disagreement without a
  reason → `422 feedback_reason_required`; invalid decision → `422`.
- Feedback and report responses carry no candidate PII, no ciphertext, no object
  keys — only `candidate_id`/`jd_code`/`score_id` references and HR-authored
  reason text. Reason text is authored by HR in the authorized review context.

## 10. Runtime Configuration

None new. Reuses the read pagination settings (`READ_PAGE_SIZE_DEFAULT`,
`READ_PAGE_SIZE_MAX`).

## 11. Testing

Default CI stays offline and deterministic.

### 11.1 Backend offline unit

- `ai_agreed` derivation across all four advance/reject quadrants plus
  `hold → null`.
- Reason-required-on-disagreement (missing/blank → `422`); reason optional on
  agreement and on `hold`.
- Report aggregation math: agreement rate, per-decision counts, zero-denominator
  handling, per-JD grouping.

### 11.2 Backend integration (real PostgreSQL)

- Upsert: first PUT creates, second PUT by the same reviewer updates (one row,
  the `uq_feedback_score_reviewer` conflict target); a different reviewer creates
  a second row.
- Authorization matrix per route (no token → 401; wrong role → 403; allowed →
  200); `404` for a score not owned by the candidate.
- Report reflects seeded feedback correctly (overall + per-JD agreement rate,
  disagreement list), scoped by `jd_code`.
- Alembic upgrade/downgrade round-trip for the new constraints.

### 11.3 Frontend

- Vitest: the `FeedbackPanel` (decision selection, reason-required-on-disagree
  validation, rendering other reviewers' feedback attributably), report-page
  serializers/rendering.
- Playwright e2e (stubbed BFF): submit feedback on the scorecard → the report
  page shows the agreement rate and the disagreement; desktop + mobile; axe
  clean. No PII/token leak assertions preserved.

## 12. Rollout and Rollback

WP6a adds additive feedback routes, response models, a service, a small schema
migration (two constraints), and frontend components. Rollback is the previous
image plus an Alembic downgrade dropping the two constraints (no data loss —
existing feedback rows remain). No existing contract or scoring behavior changes.

## 13. Exit Criteria

WP6a is complete when:

- An HR reviewer can record `advance`/`reject`/`hold` + reason on a scorecard;
  the reviewer can revise their own decision (upsert); multiple reviewers each
  keep an attributable row (`uq_feedback_score_reviewer`).
- `ai_agreed` is derived server-side and correct across all quadrants; a
  disagreement requires a reason.
- The report returns correct overall and per-JD agreement rates, decision
  counts, and a PII-free disagreement list.
- No feedback or report response leaks candidate PII, ciphertext, or object keys.
- Backend offline + integration tests, Alembic round-trip, Ruff, mypy, and
  hosted CI (Python 3.10, 3.14, strict integration) pass; the frontend local
  gate (lint, typecheck, Vitest, Playwright e2e desktop+mobile, build) passes.
- Exact commits, test counts, and run URLs are recorded; WP6b is changed to
  Ready for planning only after every gate passes.

## 14. Approval

Approval means implementation may proceed. WP6a completion remains blocked until
the full backend gate (offline + integration + Alembic + Ruff + mypy + hosted
CI) and the frontend local gate pass.
