# WP6c Rule Publication Workflow, What-If, and Regression Gates Design

**Date:** 2026-07-23

**Status:** Draft (pending approval)

**Work package:** WP6c (third and final sub-project of WP6: Review, golden set, and rule simulation)

**Depends on:** WP3 scoring pipeline, WP4 rule-version read APIs, WP5 HR web workspace, WP6b golden set + baseline metrics

## 1. Purpose

WP6c adds a **controlled rule-publication workflow with a regression gate**: a
rule version cannot become a JD's active (scoring) version without recorded
golden-set regression metrics. A curator authors a **draft** rule version,
runs a **What-If** evaluation that deterministically re-scores the golden set
with the draft's rules and compares it to the current active version's
baseline, and then **publishes** the draft — which is blocked until the
regression metrics are recorded.

This closes WP6's exit gate ("a rule cannot be published without recorded
regression results") and completes WP6, unblocking WP7 (which consumes the
versioned golden-set quality signal). WP6c does not change the scoring
algorithm; it adds a governed path to change *which* rule version is active,
plus the offline What-If preview and the gate.

## 2. Baseline and Gaps

### 2.1 Baseline

- `RuleVersion(id, jd_id → jds.id, version: str(32), schema_json: JSONB,
  published_at: datetime [non-null], published_by_user_id: bigint | null,
  notes: text | null, golden_set_metrics: JSONB | null)`. There is no status
  column and no `(jd_id, version)` uniqueness constraint; `jd_id` is indexed.
  `golden_set_metrics` exists but is unused.
- `JD.active_rule_version_id` (nullable, a deferred FK to `rule_versions.id`)
  points to the JD's active version. The scoring pipeline
  (`backend/app/scoring/pipeline.py`) scores a candidate against
  `jd.active_rule_version_id`; a JD with none raises.
- Rule versions are created only via the `import_rules` CLI; there is no API to
  create or publish a rule version. WP4's read API (`list_rule_versions`,
  `rule_version_diff`) exposes versions, the schema diff, `is_active`, and
  `golden_set_metrics`.
- Scoring is three-stage: **hard filter** (deterministic, over
  `candidate.extracted_json`), **rule engine** (`score_dimensions`,
  deterministic, over `extracted_json`), and **LLM judge** (over the resume
  markdown). `total = rule_total + judge_total`; `grade = _grade_from(total,
  schema)` (highest-threshold-first; below all thresholds → `"rejected"`).
- WP6b's golden set holds ground-truth labels (`advance`/`reject`/`borderline`)
  per `(candidate, JD)`; `golden_metrics` computes AI-vs-golden confusion and
  precision/recall/F1/accuracy for the *active* version's scores.

### 2.2 Gaps

- No way to author a candidate (draft) rule version, evaluate it before it goes
  live, or publish it under a gate.
- No regression measurement of a proposed rule change; nothing enforces the
  exit gate.

## 3. Goals

- Let a curator (`hr_lead`/`admin`) **create a draft** rule version by POSTing a
  validated `schema_json`.
- **Evaluate** a draft with a deterministic What-If: re-score the JD's golden-set
  candidates with the draft's rules (reusing each candidate's stored LLM-judge
  subtotal), compute the draft's confusion matrix + precision/recall/F1/accuracy
  against the golden labels, store it on the draft, and return it beside the
  active version's baseline for comparison.
- **Publish** a draft under a gate: publication is blocked unless the draft's
  regression metrics are recorded; on publish, the draft becomes the JD's active
  version and the previously active version is archived. The human decides
  whether to publish; WP6c records and surfaces the comparison but does not
  auto-block on a metric threshold.
- Surface it in the WP5 UI: a rule-management page per JD (versions + metrics,
  draft creation, What-If evaluation with comparison, and a gated publish).

## 4. Non-goals

- Any change to the scoring algorithm, the LLM judge, or how a live candidate is
  scored.
- An in-UI rule editor (hard filters / dimensions / weights / thresholds). Drafts
  are authored externally (CLI or hand-edited JSON) and POSTed as a
  `schema_json`.
- Re-running the LLM judge during What-If — the judge subtotal is reused from
  each candidate's stored score (deterministic, offline, free). A full
  LLM re-score is a WP7 concern.
- An automatic no-regression threshold that blocks publication (the gate is
  "metrics recorded", not "metrics above X"); a human judges the comparison.
- Cost/usage ledger, calibration, cross-engine scoring, and richer trend reports
  (WP7); cross-position recommendation (WP9).

## 5. Data Model and Migration

`RuleVersion` is extended. One Alembic migration (new head on top of WP6b's
`f412481450cf`) adds:

- A `status` column `String(16)` with a CHECK `ck_rule_versions_status`
  restricting it to `('draft', 'published', 'archived')`. Existing rows are
  backfilled to `'published'` (they are all live/historical published versions);
  the column is `NOT NULL` with server default `'published'`.
- A `UniqueConstraint(jd_id, version)` named `uq_rule_versions_jd_version` —
  version strings must be unique per JD (required for a publication workflow and
  the conflict check). If existing data holds a duplicate `(jd_id, version)` the
  migration will fail, surfacing a pre-existing data problem (CLI import is
  expected to have kept versions unique).
- `published_at` is altered to **nullable** (a draft has no `published_at` until
  it is published).

The downgrade drops the CHECK and the uq and restores `published_at` to
`NOT NULL` after backfilling any nulls (draft rows, which have no meaningful
`published_at`, are removed on downgrade — a best-effort rollback with no
published-data loss).

## 6. Rule Draft Lifecycle and Schema Validation

- A rule version's `status` is `draft`, `published`, or `archived`.
- **draft:** `status = 'draft'`, `published_at = NULL`,
  `golden_set_metrics = NULL` (until evaluated). Not usable for scoring.
- **published:** the JD's live version — `jd.active_rule_version_id` points to
  exactly one `published` version; `published_at`/`published_by_user_id` set;
  `golden_set_metrics` recorded.
- **archived:** a previously published version, superseded by a newer publish.
- A draft is created by POSTing `{version, schema_json, notes?}`. `schema_json`
  is validated with `RuleSchema.model_validate` (invalid → `422 invalid_rule_schema`);
  `version` must be unique per JD (duplicate → `409 version_exists`).

## 7. What-If / Regression Computation (the evaluate step)

Deterministic, offline, no LLM. For each golden-set entry for the JD whose label
is `advance` or `reject` (`borderline` and `uncovered` are excluded) and whose
candidate has a stored `Score`:

- Re-run the hard filter with the draft's `hard_filters` over the candidate's
  `extracted_json`. If rejected, the draft grade is `"rejected"`.
- Otherwise: `rule_total = score_dimensions(extracted, draft.rule_dimensions)`
  (deterministic); `judge_total` is **reused** from the candidate's stored score
  (the stored `total_score` minus the stored rule subtotal); `total =
  rule_total + judge_total`; `grade = _grade_from(total, draft schema)`.
- The AI prediction is `advance` ⟺ `grade != "rejected"`; compare it to the
  golden label to build the confusion matrix (reusing WP6b's `metric_stats`).

Edge handling:

- A candidate whose stored score was **hard-filter-rejected** (no LLM judge ran,
  so there is no reusable judge subtotal) and whom the **draft does not
  hard-reject** is **indeterminate**: excluded from the metrics and counted
  separately (`indeterminate`).
- If the draft's `judge_dimensions` differ from the active version's, the reused
  judge subtotal was computed for different dimensions, so the What-If is an
  approximation: the response carries `judge_dimensions_changed = true` as a
  warning, but the metrics are still computed and stored.

The result — `{confusion, precision, recall, f1, accuracy, evaluated,
indeterminate, borderline_excluded, uncovered}` — is stored on the draft's
`golden_set_metrics` and returned alongside the active version's `baseline`
metrics (same shape, from its stored `golden_set_metrics` or computed live) and
the `judge_dimensions_changed` flag.

## 8. Backend API

All write routes require role `hr_lead`/`admin`; reads require
`hr`/`hr_lead`/`admin`. Errors are `{code, message}`; the routes reuse the WP4
JD/rule conventions and change no existing route.

### 8.1 Create draft

`POST /api/v1/jds/{code}/rule-versions` — body `{version, schema_json, notes?}`.
Validates the JD exists (`404`), the schema (`422 invalid_rule_schema`), and
version uniqueness (`409 version_exists`). Creates a `draft` RuleVersion.
Returns `{id, version, status, notes}`.

### 8.2 Evaluate (What-If / regression)

`POST /api/v1/jds/{code}/rule-versions/{version}/evaluate`. Runs §7's
computation over the JD's golden set, stores the draft's `golden_set_metrics`,
and returns `{draft: <metrics>, baseline: <active metrics | null>,
judge_dimensions_changed, evaluated, indeterminate}`. `404` if the JD or draft
version is unknown; only a `draft` version may be evaluated (`409`
not_a_draft otherwise). No candidate PII.

### 8.3 Publish

`POST /api/v1/jds/{code}/rule-versions/{version}/publish`. Requires the draft's
`golden_set_metrics` to be recorded (else `409 regression_not_recorded`). Sets
`status = 'published'`, `published_at = now`, `published_by_user_id =
current user`; sets `jd.active_rule_version_id` to this version; sets the
previously active version's `status = 'archived'`. `404` unknown JD/version;
`409 not_a_draft` if the version is not a draft. Returns the published version.

### 8.4 List (extend WP4)

`GET /api/v1/jds/{code}/rule-versions` (existing) is extended so each item
includes `status`. No new route; the rule-management page consumes it plus the
existing `rule_version_diff`.

## 9. Architecture

- Backend: a new `backend/app/routers/rule_publication.py` and a
  `backend/app/services/rule_publication.py` (draft creation with schema
  validation, the What-If re-score composing the existing
  `run_hard_filters`/`score_dimensions`/`_grade_from` + WP6b's `metric_stats`,
  and the gated publish that flips `jd.active_rule_version_id` and archives the
  predecessor). Response models in `backend/app/schemas/rule_publication.py`.
  The WP4 `list_rule_versions` service/schema gains a `status` field.
- Frontend (extends WP5, `frontend/`): a `/jds/[code]/rules` rule-management page
  (a server component reads the session role → a client view with the versions
  list + metrics, a draft-creation form that accepts a pasted/uploaded
  `schema_json`, an evaluate action showing the draft-vs-baseline comparison and
  the `judge_dimensions_changed` warning, and a publish action enabled only when
  the draft's metrics are recorded), nav, zod schemas, and TanStack Query
  mutations over the existing `/api/proxy`.

## 10. Errors, Authorization, and Leak Safety

- Create/evaluate/publish require `require_roles("hr_lead", "admin")`; the
  rule-version list requires `require_roles("hr", "hr_lead", "admin")`. No token
  → `401`; insufficient role → `403`.
- Errors: unknown JD/version → `404`; invalid schema → `422 invalid_rule_schema`;
  duplicate version → `409 version_exists`; evaluating/publishing a non-draft →
  `409 not_a_draft`; publishing without recorded metrics → `409
  regression_not_recorded`.
- The evaluate and metrics responses carry no candidate PII, ciphertext, or
  object keys — only aggregate numbers (and, at most, `candidate_id`/`jd_code`
  references). The stored `schema_json` and `notes` are curator-authored rule
  configuration, not candidate data.

## 11. Runtime Configuration

None new.

## 12. Testing

Default CI stays offline and deterministic.

### 12.1 Backend offline unit

- The What-If re-score: hard-filter-reject path, deterministic rule-engine +
  reused-judge-subtotal path, threshold-driven grade, and the AI-vs-golden
  confusion assignment across quadrants — using an in-memory schema + candidate
  fixtures (no DB, no LLM).
- The `indeterminate` edge (stored hard-reject + draft non-reject) and the
  `judge_dimensions_changed` detection.
- Metric composition reuses WP6b's `metric_stats` (null on zero denominator).

### 12.2 Backend integration (real PostgreSQL)

- Create draft (schema validation `422`, duplicate version `409`); the draft is
  `status = 'draft'` with `published_at` NULL.
- Evaluate stores `golden_set_metrics` and returns draft-vs-baseline; a seeded
  golden set + scores yields the expected confusion/rates; a non-draft → `409`.
- Publish gate: publishing before evaluate → `409 regression_not_recorded`;
  after evaluate → `200`, `jd.active_rule_version_id` moves to the new version,
  the old active version becomes `archived`, and a subsequent live score uses the
  new active version.
- Authorization matrix per route (no token → 401; `hr` on a write → 403; allowed
  → 200).
- Alembic upgrade/downgrade round-trip for the `status` CHECK, the uq, and the
  `published_at` nullability change.

### 12.3 Frontend

- Vitest: the rule-management view (versions + status/metrics rendering, the
  draft-creation form, the evaluate comparison including the
  `judge_dimensions_changed` warning, and the publish button gated on recorded
  metrics), role-gated write affordances.
- Playwright e2e (stubbed BFF): the rule-management page renders the versions and
  a draft-vs-baseline comparison; desktop + mobile; axe clean; no PII/token leak
  assertions preserved.

## 13. Rollout and Rollback

WP6c adds additive rule-publication routes, response models, a service, one
schema migration (a `status` column + CHECK, a uq, and a `published_at`
nullability change), and frontend pages. Rollback is the previous image plus an
Alembic downgrade (draft rows removed; published/archived data preserved). No
existing contract or scoring behavior changes; a JD keeps its current active
rule version until a publish explicitly moves it.

## 14. Exit Criteria

WP6c — and thereby WP6 — is complete when:

- A curator creates a draft rule version, evaluates it (What-If metrics recorded
  and compared to the active baseline, with the `judge_dimensions_changed`
  warning when applicable), and publishes it under the gate; publishing without
  recorded metrics is blocked; on publish the JD's active version switches and
  the predecessor is archived.
- The regression comparison is surfaced in the UI and no evaluate/metrics
  response leaks candidate PII, ciphertext, or object keys.
- Backend offline + integration tests, Alembic round-trip, Ruff, mypy, and
  hosted CI (Python 3.10, 3.14, strict integration) pass; the frontend local
  gate (lint, typecheck, Vitest, Playwright e2e desktop+mobile, build) passes.
- Exact commits, test counts, and run URLs are recorded; WP6 is marked Complete
  and WP7 is set Ready for planning only after every gate passes.

## 15. Approval

Approval means implementation may proceed. WP6c completion remains blocked until
the full backend gate (offline + integration + Alembic + Ruff + mypy + hosted
CI) and the frontend local gate pass.
