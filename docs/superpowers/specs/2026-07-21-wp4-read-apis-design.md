# WP4 Read APIs and Rule Visibility Design

**Date:** 2026-07-21

**Status:** Draft (pending approval)

**Work package:** WP4

**Depends on:** WP1 authorization, WP3 durable job/candidate states

## 1. Purpose

WP4 exposes the stable read surface an HR API client needs to complete the
screening workflow without direct database access: browse uploaded candidates
and their ingestion state, rank scored candidates per JD, inspect a scorecard
with evidence, decrypt a candidate's PII under audit, and view JD and rule-version
history including a structured version diff. Combined with the WP3 async upload
and status endpoints and the existing re-score endpoint, this closes the
"upload → monitor → list → inspect → re-score" loop over HTTP.

WP4 is read-only. Controlled rule publication (a write workflow) and
ownership/department query scoping (which need a new data model) are explicitly
out of scope and deferred.

## 2. Baseline and Gaps

### 2.1 Baseline

- WP1 gives JWT authentication and `require_roles` RBAC; candidate PII
  (`name_cipher`, `phone_cipher`, `email_cipher`) is Fernet-encrypted, and raw
  resumes are private MinIO objects with presigned-URL support.
- WP3 gives durable `ingestion_jobs` state, async upload, and `GET /jobs/{id}` /
  `GET /batches/{id}` status endpoints; the re-score endpoint
  `POST /candidates/{id}/score` exists and is idempotent.
- `Candidate` holds `extracted_json`, `parsed_markdown`, `pii_hash`, raw-file
  metadata; `Score` holds `total_score`, `grade`, `hard_filter_result`,
  `rule_dimensions`, `judge_dimensions` (with evidence quotes), and rule-version
  linkage; `JD` holds `code/name/status/active_rule_version_id`; `RuleVersion`
  holds `version/schema_json/published_at/published_by_user_id/notes/golden_set_metrics`.

### 2.2 Gaps

- There is no candidate list, candidate detail, score detail, JD list/detail, or
  rule-version list/diff endpoint; the only reads are the two WP3 job/batch
  status routes.
- PII cannot be viewed through the API, and there is no `pii_decrypt` audit path.
- There is no pagination, filtering, or query-composition layer; routers own no
  read services.
- There is no OpenAPI contract test asserting response schemas are stable.

## 3. Goals

- Two candidate list views: a JD-scoped ranked list and a flat candidate list,
  both paginated and filtered, neither exposing PII.
- A candidate detail endpoint that decrypts PII and writes exactly one
  `pii_decrypt` audit row per fetch.
- A score detail (scorecard) endpoint returning hard-filter, rule, and judge
  dimensions with evidence quotes.
- JD list/detail, rule-version list, and a structured rule-version diff.
- A raw-resume download endpoint returning a short-lived presigned URL under
  audit.
- A thin read-service layer isolating pagination, filtering, and the
  decrypt-plus-audit orchestration from routers.
- Stable, role-protected responses with OpenAPI contract tests; no schema change
  and no migration.

## 4. Non-goals

- Controlled rule publication or any rule write workflow (WP6).
- Ownership/department query scoping and the data model it requires; WP4 uses
  role-level access only (`hr`, `hr_lead`, `admin` see all candidates).
- Any frontend (WP5).
- Feedback capture, golden-set calibration, What-If simulation (WP6), and cost
  reporting (WP7).
- Changing WP1/WP2/WP3 contracts, the schema, or the scoring algorithm.

## 5. HTTP Read Surface

All routes require Bearer JWT with role in `("hr", "hr_lead", "admin")` via the
existing `require_roles` dependency. Unknown resources return `404` with a stable
`{code, message}` body. Pagination is offset-based: `?page=` (1-based, default 1)
and `?page_size=` (default 20, max 100); list responses wrap items in
`{items: [...], page, page_size, total}`.

### 5.1 JD-scoped ranked candidate list

`GET /api/v1/jds/{code}/candidates?grade=&page=&page_size=`

Returns candidates scored for the JD **under its active rule version** (one row
per candidate — a consistent ranking under the current ruleset; a candidate
scored only under a superseded version does not appear until re-scored), ordered
by `Score.total_score` descending then `Score.id` for a stable tiebreak. Optional
`grade` filter. Each item: `{candidate_id, score_id, total_score, grade,
rule_version, scored_at}`. No PII. `404` if the JD code does not exist; an empty
`items` list if the JD has no active rule version or no scored candidates.

### 5.2 Flat candidate list

`GET /api/v1/candidates?state=&page=&page_size=`

Returns all candidates ordered by `created_at` descending. `latest_state` is the
state of the candidate's most recent `ingestion_jobs` row by `created_at`, or
`null` for a candidate with no ingestion job (e.g. a legacy pre-WP3 row). Optional
`state` filter keeps only candidates whose `latest_state` equals it. Each item:
`{candidate_id, created_at, latest_state, scored_jd_codes: [...]}`. No PII.

### 5.3 Candidate detail (PII, audited)

`GET /api/v1/candidates/{id}`

Decrypts `name/phone/email` and returns
`{candidate_id, name, phone, email, age, education, experiences, source,
created_at, scores: [{score_id, jd_code, total_score, grade, rule_version}]}`.
Writes exactly one `audit_logs` row with `event_type="pii_decrypt"` and payload
`{actor, candidate_id, purpose: "candidate_detail", trace_id}` — never plaintext.
`404` if the candidate does not exist. The audit row is committed in the same
transaction as the (read-only) request completion.

### 5.4 Score detail (scorecard)

`GET /api/v1/candidates/{id}/scores/{score_id}`

Returns `{score_id, candidate_id, jd_code, rule_version, total_score, grade,
hard_filter_result, rule_dimensions, judge_dimensions}` where `judge_dimensions`
includes each dimension's tier, score, `evidence_quotes`, reasoning, confidence,
and suggested interview questions. No PII (a scorecard is not a PII view). `404`
if the score does not exist or does not belong to the candidate.

### 5.5 Raw-resume download (audited)

`GET /api/v1/candidates/{id}/raw-file`

Returns `{url, expires_in_seconds}` where `url` is a MinIO presigned GET URL for
the candidate's `raw_file_key`, valid for a configurable short TTL (default 300
seconds). Writes one `audit_logs` row with `event_type="raw_file_access"` and the
same actor/candidate/trace payload. The presigned URL is never logged. `404` if
the candidate or object does not exist; `503` `object_storage_unavailable` if
MinIO is unreachable.

### 5.6 JD list and detail

- `GET /api/v1/jds?status=&page=&page_size=` → items
  `{code, name, status, active_rule_version}`.
- `GET /api/v1/jds/{code}` → `{code, name, description, status,
  active_rule_version: {id, version, published_at}}`. `404` if unknown.

### 5.7 Rule-version list and diff

- `GET /api/v1/jds/{code}/rule-versions?page=&page_size=` → items
  `{id, version, published_at, published_by_user_id, notes, golden_set_metrics,
  is_active}` ordered by `published_at` descending.
- `GET /api/v1/jds/{code}/rule-versions/{from_version}/diff/{to_version}` →
  structured diff (§6). `404` if the JD or either version does not exist.

## 6. Rule-version Diff

The diff compares the two versions' `schema_json` and returns
`{jd_code, from_version, to_version, changes: [...]}` where each change is
`{path, kind, before, after}` with `kind` in `added | removed | changed`. Covered
paths:

- `passing_threshold`, `total_score`.
- `hard_filters[id]` — added, removed, or changed rule/action/audit_tag.
- `rule_dimensions[id]` — added, removed, or changed name/weight/method and
  per-tier score/keywords/thresholds.
- `judge_dimensions[id]` — added, removed, or changed name/weight/prompt_hint and
  tiers.
- `grade_thresholds[grade]` — added, removed, or changed min.

Diff computation is deterministic and pure (no I/O); it is unit-tested against
fixture schema pairs. Dimensions are matched by `id`, grade thresholds by
`grade`, so reordering alone produces no change.

## 7. Architecture: Read-service Layer

Introduce `backend/app/services/read/` to isolate query composition from routers:

- `candidates.py` — `list_ranked_for_jd(...)`, `list_candidates(...)`,
  `get_candidate_detail(...)` (orchestrates decrypt + audit), `get_score_detail(...)`.
- `jds.py` — `list_jds(...)`, `get_jd_detail(...)`, `list_rule_versions(...)`.
- `rule_diff.py` — the pure `diff_schemas(from_schema, to_schema)` function.
- A shared `pagination.py` — `Page` params validation and the
  `{items, page, page_size, total}` envelope.

Routers translate HTTP to these services and back; they own no query logic and no
decrypt logic. The read services depend on the models, the existing PII crypto
(`backend/app/security/crypto.py`), the storage service (for presigned URLs), and
`AuditLog`. This follows the roadmap §8 rule: repositories isolate query
composition once authorization, pagination, and filtering are non-trivial.

New router modules `backend/app/routers/candidates_read.py` and
`backend/app/routers/jds.py` keep the read surface separate from the WP3 write
router (`candidates.py`), which has already grown large.

## 8. Errors, Authorization, and Leak Safety

- All read routes use `require_roles("hr", "hr_lead", "admin")`; unauthorized →
  stable `401`/`403` (WP1 behavior).
- Unknown resource → `404 {code, message}`; invalid pagination → `422`.
- MinIO unavailable on raw-file download → `503 object_storage_unavailable`.
- Responses and logs never contain ciphertext, PII beyond the authorized detail
  body, object keys, presigned URLs, or provider bodies.
- PII decryption occurs only in candidate detail and raw-file download, each
  audited.

## 9. Runtime Configuration

- `RAW_FILE_PRESIGN_TTL_SECONDS` (default 300).
- `READ_PAGE_SIZE_DEFAULT` (default 20) and `READ_PAGE_SIZE_MAX` (default 100).

No new secrets. No schema change; no migration.

## 10. Testing and Contract Evidence

Default CI stays offline and deterministic.

### 10.1 Offline unit tests

- `diff_schemas` for added/removed/changed dimensions, tiers, hard filters, grade
  thresholds, and reorder-is-no-change.
- Pagination envelope: page/size clamping, `total` correctness, out-of-range page.
- Read-service serialization shapes (no PII in list serializers).
- Response-model schema stability (Pydantic models for every response).

### 10.2 Integration tests (real PostgreSQL, MinIO)

- Authorization matrix per route: no token → 401; wrong role → 403; allowed roles
  → 200.
- JD-scoped ranked list ordering by `total_score`, `grade` filter, pagination
  boundaries, and 404 for unknown JD.
- Flat candidate list ordering, `state` filter, `scored_jd_codes` correctness.
- Candidate detail decrypts PII and writes exactly one `pii_decrypt` audit row;
  list endpoints write no audit and perform no decrypt (assert audit count
  unchanged).
- Score detail returns evidence quotes; 404 for a score not owned by the
  candidate.
- Raw-file download returns a working presigned URL and writes one
  `raw_file_access` audit row; the URL is absent from logs.
- Rule-version list ordering and `is_active` flag; diff against a real
  two-version JD.

### 10.3 OpenAPI contract tests

- Assert the generated OpenAPI schema contains each route with its documented
  response model, and that response bodies validate against those models.

## 11. Rollout and Rollback

WP4 adds only read routes, response models, read services, and two config knobs;
it changes no existing contract and no schema. Rollback is the previous image
with no data migration. The new routers are additive and can be disabled by not
registering them if a regression appears.

## 12. Exit Criteria

WP4 is complete when:

- All §5 endpoints exist with role protection and stable response models.
- Candidate detail and raw-file download decrypt PII only under a written audit
  row; list endpoints never decrypt or audit.
- The rule-version diff is correct for added/removed/changed dimensions, filters,
  and thresholds, and treats reordering as no change.
- An API client can complete upload → poll status → list (ranked and flat) →
  inspect candidate/scorecard/raw file → re-score without direct database access.
- Unit, integration, OpenAPI-contract, Ruff, mypy, and hosted CI (Python 3.10,
  Python 3.14, strict integration) gates pass.
- Exact commits, test counts, and run URLs are recorded; WP5 is changed to Ready
  for planning only after every gate passes.

## 13. Approval

Approval means implementation may proceed. WP4 completion remains blocked until
the full offline and integration gate and hosted CI pass.
