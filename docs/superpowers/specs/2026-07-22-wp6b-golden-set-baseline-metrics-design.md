# WP6b Golden Set and Baseline Metrics Design

**Date:** 2026-07-22

**Status:** Draft (pending approval)

**Work package:** WP6b (second sub-project of WP6: Review, golden set, and rule simulation)

**Depends on:** WP3 scoring (`scores.grade`), WP4 read APIs / pagination, WP5 HR web workspace (UI shell, BFF)

## 1. Purpose

WP6b establishes a curated **ground-truth golden set** — one authoritative
label per `(candidate, JD)` — and a **baseline metrics** report that measures
the current AI's advance/reject decision against that ground truth
(precision, recall, F1, accuracy, confusion matrix; overall and per-JD).

This is the objective quality baseline the roadmap requires: WP6c's regression
gates compare a candidate rule change against it, and WP7's release quality
reports consume it (the roadmap's "measured F1 using a versioned golden set").

WP6b is deliberately distinct from WP6a. WP6a captures **each reviewer's
subjective feedback** and whether the AI *agreed with that reviewer*; a
reviewer can be wrong. WP6b captures a **single curated authoritative truth**
and measures whether the AI is *actually correct*. The two share a similar
shape (both hang a label on a `(candidate, JD)` pair) but sit at different
levels of authority, which is why WP6 was split into WP6a and WP6b.

WP6b changes no scoring behavior and does not mutate scores; the golden set is
a separate record.

## 2. Baseline and Gaps

### 2.1 Baseline

- The `GoldenSet` model already exists as a schema stub:
  `golden_set(id, candidate_id → candidates.id, jd_id → jds.id,
  label: str(32), imported_at, imported_by_user_id → users.id)` with
  `UniqueConstraint(candidate_id, jd_id)` named `uq_golden_set_cand_jd`. It has
  no label CHECK, no API, and no UI.
- The `Score` model holds `grade: str(16)` (the scoring pipeline sets
  `grade == "rejected"` for a hard-filter rejection and a tier grade otherwise),
  `total_score`, JD/rule linkage, and `created_at` (via `TimestampMixin`). A
  candidate may be scored against a JD under multiple rule versions
  (`uq_scores_candidate_jd_rule` over `candidate_id, jd_id, rule_version_id`).
- WP5 ships the app shell, nav, the BFF proxy, `require_roles`, and (from WP5's
  upload feature) a server-side FormData-forwarding BFF route pattern.

### 2.2 Gaps

- There is no way to import or manage ground-truth labels, no golden-set API,
  and no label uniqueness/validity guarantee beyond the existing
  `(candidate_id, jd_id)` uniqueness.
- There is no measurement of AI quality against ground truth — no
  confusion matrix, precision, recall, or F1.

## 3. Goals

- Let a curator (`hr_lead`/`admin`) **import** a CSV of
  `(candidate_id, jd_code, label)` rows, upserting one authoritative label per
  `(candidate, JD)`, with per-row validation and a partial-success summary.
- Constrain the label to `advance | reject | borderline` (a DB CHECK plus API
  validation).
- List golden-set entries (paginated, PII-free) for review.
- Compute, **live**, a baseline metrics report comparing the AI's
  advance/reject decision (derived from `score.grade`) to the golden label:
  confusion matrix, precision, recall, F1, accuracy — overall and per-JD —
  with `borderline` excluded from the binary metrics and uncovered entries
  reported separately.
- Surface it in the WP5 UI: a golden-set import + list page and a baseline
  metrics report page.

## 4. Non-goals

- Storing a **versioned baseline snapshot** — metrics are computed live on each
  request. WP6c persists whatever baseline its regression gates need; WP7 owns
  golden-set versioning for release quality reports.
- Rule draft/publication states, controlled publication, What-If simulation, and
  regression gates (WP6c).
- Cost/usage ledger, confidence calibration, cross-engine scoring, and the
  richer AI-HR consistency/trend reports (WP7).
- Auto-seeding the golden set from WP6a feedback, or in-UI single-entry golden
  labeling — import is the sole population mechanism in WP6b (a deliberate
  scope boundary; either could be added later).
- Mutating a score/grade, or any change to WP1–WP6a contracts beyond the
  additive golden-set routes.

## 5. Data Model and Migration

`GoldenSet` is reused. One small Alembic migration (new head on top of WP6a's
`1e9b39dbf340`) adds:

- A `CHECK` constraint `ck_golden_set_label` restricting `label` to
  `('advance', 'reject', 'borderline')`.

`uq_golden_set_cand_jd` already exists and is the upsert conflict target. On a
re-import of an existing `(candidate_id, jd_id)`, the row's `label`,
`imported_at`, and `imported_by_user_id` are overwritten (last import wins). No
other column changes. The migration has a working downgrade (drop the CHECK).

## 6. Golden Vocabulary and AI-prediction Mapping

- The golden label is the authoritative truth: `advance` (should be advanced),
  `reject` (should be rejected), or `borderline` (genuinely ambiguous).
- The **AI prediction** is derived from the score's grade, using the same
  convention as WP6a: **AI reject** ⟺ `score.grade == "rejected"`; otherwise
  **AI advance**.
- Binary metrics treat `advance` as the positive class:

  | Golden label | AI prediction | Cell |
  |---|---|---|
  | `advance` | advance | TP |
  | `advance` | reject | FN |
  | `reject` | advance | FP |
  | `reject` | reject | TN |
  | `borderline` | (either) | excluded (counted as `borderline_excluded`) |

- **Which score:** a `(candidate, JD)` may have several scores (different rule
  versions). The baseline uses the **most recent** score for that pair (by
  `created_at` descending, tie-broken by `id` descending) — it reflects the
  current AI. A golden entry with **no** score for its `(candidate, JD)` is
  `uncovered`: reported, excluded from all metrics.

## 7. Backend API

All routes mirror the WP4/WP5/WP6a conventions: `{code, message}` errors; offset
pagination `{items, page, page_size, total}` reusing
`backend/app/services/read/pagination.py`. They change no existing route.

### 7.1 Import golden set

`POST /api/v1/golden-set/import` — role `hr_lead`/`admin` only.

Accepts a multipart CSV upload (a `file` field). The CSV has a header row and
columns `candidate_id, jd_code, label`. Parsing and validation happen
server-side, per row:

- resolve `jd_code` → `jd_id` (unknown `jd_code` → row error);
- the referenced `candidate_id` must exist (unknown → row error);
- `label` must be one of `advance | reject | borderline` (else row error);
- a malformed row (missing/extra columns, non-integer `candidate_id`) → row
  error.

Valid rows upsert on `uq_golden_set_cand_jd` (create or update), setting
`imported_by_user_id = current user` and `imported_at = now`. Within one file,
a duplicate `(candidate_id, jd_code)` is applied in order (last wins). A cap of
`GOLDEN_IMPORT_MAX_ROWS` (default 5000) bounds a single import; exceeding it →
`422 golden_import_too_large`. A file that cannot be parsed as CSV or lacks the
required header → `422 invalid_csv`.

Response (`200`): `{total, created, updated, errors: [{row, candidate_id,
jd_code, reason}]}` — `total` is the data-row count; `errors` lists the rejected
rows by 1-based row number with a machine reason code. No candidate PII.

### 7.2 List golden set

`GET /api/v1/golden-set?jd_code=&page=&page_size=` — role `hr`/`hr_lead`/`admin`.

Returns `{items: [{id, candidate_id, jd_code, label, imported_at,
imported_by_display_name}], page, page_size, total}`, ordered by `imported_at`
descending. Optional `jd_code` filter. No candidate PII.

### 7.3 Baseline metrics

`GET /api/v1/golden-set/metrics?jd_code=` — role `hr`/`hr_lead`/`admin`.

Joins golden-set entries to each pair's most-recent score and computes, treating
`advance` as positive:

- `overall`: `{labeled_total, scored, uncovered, borderline_excluded,
  confusion: {tp, fp, tn, fn}, precision, recall, f1, accuracy}` where
  - `precision = tp / (tp + fp)` (null when `tp + fp == 0`),
  - `recall = tp / (tp + fn)` (null when `tp + fn == 0`),
  - `f1 = 2·tp / (2·tp + fp + fn)` (null when the denominator is 0),
  - `accuracy = (tp + tn) / (tp + tn + fp + fn)` (null when the denominator is 0),
  - `scored = tp + fp + tn + fn + borderline_excluded`,
  - `uncovered = labeled_total − scored`.
- `by_jd`: a list of `{jd_code, ...same fields...}`.

An optional `jd_code` filter scopes both sections. No candidate PII —
references only `candidate_id`/`jd_code` counts and aggregate numbers.

## 8. Architecture

- Backend: a new `backend/app/routers/golden_set.py` and
  `backend/app/services/golden_set.py` (CSV parse/validate, upsert-import, list,
  and the metrics aggregation). Response models in
  `backend/app/schemas/golden_set.py`. The service depends on the `GoldenSet`,
  `Score`, `JD`, `Candidate`, and `User` models and reuses the read
  pagination helpers. This follows the WP4/WP6a repository/service convention.
- Frontend (extends WP5, `frontend/`): a `/golden-set` page with a CSV import
  control (visible only to `hr_lead`/`admin`), an import-summary view, and a
  paginated list of entries; a `/reports/baseline` metrics page (confusion
  matrix + precision/recall/F1 overall and per-JD + coverage). Nav entries (the
  import affordance gated by role), zod schemas, and TanStack Query
  queries/mutations. The CSV import posts multipart through a dedicated BFF
  route reusing WP5's upload FormData-forwarding pattern (the generic
  `/api/proxy` handles the JSON list/metrics reads).

## 9. Errors, Authorization, and Leak Safety

- Import requires `require_roles("hr_lead", "admin")`; list and metrics require
  `require_roles("hr", "hr_lead", "admin")`. No token → `401`; insufficient
  role → `403`.
- Import errors: whole-file unparseable → `422 invalid_csv`; over the row cap →
  `422 golden_import_too_large`; individual bad rows are reported in the
  `errors` list, never failing the whole import.
- Golden-set and metrics responses carry no candidate PII, no ciphertext, no
  object keys — only `candidate_id`/`jd_code`/`label` references,
  `imported_by_display_name` (the curator's own name), and aggregate numbers.

## 10. Runtime Configuration

One new setting: `GOLDEN_IMPORT_MAX_ROWS` (default 5000) bounding a single
import. Reuses the read pagination settings (`READ_PAGE_SIZE_DEFAULT`,
`READ_PAGE_SIZE_MAX`).

## 11. Testing

Default CI stays offline and deterministic.

### 11.1 Backend offline unit

- Metrics math: confusion-matrix assignment across all four advance/reject
  quadrants; precision/recall/F1/accuracy including zero-denominator → null;
  `borderline` excluded from the confusion matrix and counted in
  `borderline_excluded`; `uncovered` accounting.
- CSV parsing/validation: header handling, per-row errors (bad label,
  non-integer `candidate_id`, missing column), the row cap, and an unparseable
  file.

### 11.2 Backend integration (real PostgreSQL)

- Import: a valid CSV creates rows; re-importing the same `(candidate_id,
  jd_code)` updates the single row (`uq_golden_set_cand_jd`); rows with an
  unknown `jd_code`/`candidate_id`/label are reported in `errors` without
  failing the import.
- Authorization matrix per route (no token → 401; `hr` on import → 403; allowed
  role → 200); list/metrics readable by `hr`.
- Metrics reflect seeded golden labels + scores correctly (confusion matrix,
  precision/recall/F1, per-JD grouping, `borderline` exclusion, `uncovered`
  when a golden entry has no score, and "most recent score" selection when a
  pair has multiple scores), scoped by `jd_code`.
- Alembic upgrade/downgrade round-trip for the new CHECK.

### 11.3 Frontend

- Vitest: the import-summary rendering (created/updated/error rows), the metrics
  page (confusion matrix + precision/recall/F1 rendering, null-rate display),
  and role-gated visibility of the import control.
- Playwright e2e (stubbed BFF): import a CSV → the summary renders; the baseline
  report shows the metrics; desktop + mobile; axe clean; no PII/token leak
  assertions preserved.

## 12. Rollout and Rollback

WP6b adds additive golden-set routes, response models, a service, a small schema
migration (one CHECK), one runtime setting, and frontend pages. Rollback is the
previous image plus an Alembic downgrade dropping the CHECK (no data loss —
existing golden-set rows remain). No existing contract or scoring behavior
changes.

## 13. Exit Criteria

WP6b is complete when:

- A curator imports a CSV and gets a per-row summary (created/updated/errors);
  re-import upserts the single row per `(candidate, JD)`; the label CHECK holds.
- The baseline metrics report returns a correct confusion matrix and
  precision/recall/F1/accuracy overall and per-JD, with `borderline` excluded,
  `uncovered` reported, and "most recent score" selection applied.
- No golden-set or metrics response leaks candidate PII, ciphertext, or object
  keys.
- Backend offline + integration tests, Alembic round-trip, Ruff, mypy, and
  hosted CI (Python 3.10, 3.14, strict integration) pass; the frontend local
  gate (lint, typecheck, Vitest, Playwright e2e desktop+mobile, build) passes.
- Exact commits, test counts, and run URLs are recorded; WP6c is changed to
  Ready for planning only after every gate passes.

## 14. Approval

Approval means implementation may proceed. WP6b completion remains blocked until
the full backend gate (offline + integration + Alembic + Ruff + mypy + hosted
CI) and the frontend local gate pass.
