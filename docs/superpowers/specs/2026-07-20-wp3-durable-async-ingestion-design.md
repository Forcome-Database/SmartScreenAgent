# WP3 Durable Asynchronous Ingestion and Batch Processing Design

**Date:** 2026-07-20

**Status:** Draft (pending approval)

**Work package:** WP3

**Depends on:** WP2 complete

## 1. Purpose

WP3 moves the long-running resume workflow out of the HTTP request and behind a
durable, restartable job. Today `run_parse_and_score` is awaited inline by the
upload endpoint (`backend/app/routers/candidates.py`), so request latency is
bound to two external AI services (MinerU and the LLM gateway), a crashed worker
loses all progress, retries are not durable, and batch progress cannot be
queried.

After WP3, the upload endpoint persists the raw file, records an idempotent
ingestion job, enqueues it, and returns `202` immediately. Celery workers parse,
extract, score, and advance an explicit state machine. A Celery Beat sweeper
re-enqueues retryable failures and reclaims jobs abandoned by a crashed worker.
Clients poll thin job and batch status endpoints. The MinerU and AI contracts
delivered by WP2 are unchanged.

## 2. Verified Baseline and Current Gaps

### 2.1 Baseline

- WP1 persists every accepted upload as an immutable private MinIO object with a
  checksum, size, and content type, and compensates (deletes the object) when
  metadata creation fails.
- WP2 gives typed MinerU and LLM contracts with stable error codes
  (`resume_parser_unavailable`, `resume_parser_contract_invalid`,
  `resume_parser_failed`, `ai_service_unavailable`,
  `ai_service_configuration_invalid`, `ai_invalid_output`) and validated
  extraction/judge output that cannot persist a fabricated score.
- `run_parse_and_score` (`backend/app/tasks/ingest.py`) already inserts a
  candidate with `on_conflict_do_nothing` on `pii_hash`, verifies an existing
  candidate's stored object, and deletes the redundant object on duplicate.
- A Celery app and a `parse_and_score_task` wrapper exist but are unused; the
  HTTP path calls the async function directly.

### 2.2 Gaps

- The upload endpoint runs parse, extract, and score synchronously; the Celery
  task is never enqueued.
- No durable record of ingestion progress exists. The candidate row is only
  inserted after successful extraction, so a job that is still parsing, or that
  failed to parse, has no row anywhere.
- A crashed worker leaves no recoverable state; the submission is lost.
- Retry is neither durable nor policy-bounded; there is no dead-letter or
  terminal-failure record.
- The worker depends on the request-scoped temporary file rather than reading
  the persisted MinIO object, so it cannot run in a separate process reliably.
- There is no batch concept and no status query surface.
- `scores` has no uniqueness invariant, so a retried scoring step can insert a
  duplicate score row for the same candidate, JD, and rule version.

## 3. Goals

- Persist an `ingestion_jobs` row per uploaded file with an explicit state
  machine, attempt count, last stable error code, lease, and trace ID.
- Make the upload endpoint asynchronous: validate, persist the object, create an
  idempotent job, enqueue it, and return `202` with a `job_id`.
- Make the worker read its input from the persisted MinIO object, not a
  request-scoped temporary file.
- Commit every state transition separately from the external call that preceded
  it so a worker restart is idempotent.
- Add a Beat sweeper that re-enqueues retryable failures under a maximum attempt
  count and reclaims jobs whose lease expired.
- Deduplicate uploads by `raw_file_sha256` and enforce one score per
  `(candidate_id, jd_id, rule_version_id)` so retries never duplicate rows.
- Support batch upload where per-file failures do not fail the batch.
- Expose thin job and batch status endpoints sufficient for polling.
- Keep default CI offline and deterministic; test success, authorization,
  validation, dependency failure, crash recovery, and retry behavior.

## 4. Non-goals

- Rich candidate list, candidate detail, and scorecard read APIs with pagination
  and filtering; these belong to WP4.
- Any frontend; that is WP5.
- Cost ledger and budget enforcement; that is WP7.
- DingTalk recruitment synchronization and MCP access; those are WP8.
- Full data-retention, tombstone, and scheduled-purge lifecycle (roadmap §7.6).
  WP3 only guarantees that a failed or rejected ingestion leaves no orphaned
  object or partial row, and implements the `deleted` job state.
- Changing the WP2 MinerU or AI contracts, the WP1 upload boundary
  (PDF/DOCX/PNG/JPEG), or the scoring algorithm.

## 5. Data Model

### 5.1 `ingestion_jobs`

A new table independent of `candidates`, because a job in `parsing` or a job that
failed before extraction has no candidate row.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `batch_id` | UUID NULL | Null for single upload; shared across a batch |
| `state` | TEXT | State machine value (§6); not null |
| `source` | TEXT | Carried from the ingestion input (`upload`, ...) |
| `source_external_id` | TEXT NULL | |
| `jd_code` | TEXT NULL | If present, the job scores after extraction |
| `raw_file_key` | TEXT | Persisted MinIO object key (WP1) |
| `raw_file_sha256` | TEXT | Byte checksum; idempotency key |
| `raw_file_size_bytes` | BIGINT | |
| `raw_file_content_type` | TEXT | |
| `raw_file_original_name_cipher` | TEXT | Encrypted original filename |
| `candidate_id` | BIGINT FK NULL | Backfilled after extraction |
| `score_id` | BIGINT FK NULL | Backfilled after scoring |
| `attempts` | INT | Default 0 |
| `last_error_code` | TEXT NULL | Stable WP2 error code on failure |
| `lease_expires_at` | TIMESTAMPTZ NULL | Set when a worker claims a job |
| `trace_id` | TEXT NULL | |
| `actor` | TEXT | Uploading user id or `system` |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

Indexes:

- `(raw_file_sha256)` for idempotency lookup.
- `(state, lease_expires_at)` for the sweeper.
- `(batch_id)` for batch aggregation.

The raw-file columns duplicate the values WP1 stores on `candidates` so the job
is self-contained before a candidate exists; after extraction the candidate row
owns the authoritative copy and the job references it by `candidate_id`.

### 5.2 `scores` idempotency

Add `unique(candidate_id, jd_id, rule_version_id)`. Scoring becomes an upsert:
the same candidate, JD, and rule version resolves to one row, so a retried
scoring step is idempotent. A legitimate re-score after a rule-version change
uses a new `rule_version_id` and inserts a new row.

**Migration gate:** before adding the constraint, the migration (and the rollout
runbook) must count existing duplicate `(candidate_id, jd_id, rule_version_id)`
rows. A non-zero count must be reconciled or the deployment blocked, matching the
WP1/WP2 legacy-row discipline. The downgrade drops the constraint.

## 6. State Machine

```text
queued -> parsing -> extracting -> ready
                                     | (jd_code present)
                                     v
                                  scoring -> completed

any processing state -> retryable_failed -> queued        (sweeper re-enqueue)
any processing state -> terminal_failed                   (no further retry)
queued / ready / completed / terminal_failed -> deleted
```

- The job is created directly in `queued` (the object is already persisted before
  the row exists), so there is no separate `uploaded` job state.
- Processing states are `parsing`, `extracting`, and `scoring`.
- `ready` is the terminal success state for an upload without a `jd_code`;
  `completed` is the terminal success state for an upload with a `jd_code`.
- Retryable transitions come from WP2 `503`-class conditions:
  `resume_parser_unavailable` and `ai_service_unavailable` (connection, timeout,
  rate limit, provider 5xx).
- Terminal transitions come from WP2 `502`-class and validation conditions:
  `resume_parser_contract_invalid`, `resume_parser_failed`, `ai_invalid_output`,
  and `ai_service_configuration_invalid`.
- `attempts` is incremented when a worker claims a job (§7.2). Failure transitions
  write `last_error_code`; every transition writes `updated_at`. Transitions are
  the only writers of `state`.

## 7. Orchestration

### 7.1 Upload path (synchronous, bounded)

1. Authorize the database user role (`hr`, `hr_lead`, `admin`).
2. Stream, size-bound, hash, and validate the file (WP1).
3. Compute `raw_file_sha256` (already produced by WP1 validation).
4. **Idempotency check:** if an `ingestion_jobs` row exists with the same
   `raw_file_sha256` in a non-terminal state (`state NOT IN (terminal_failed,
   deleted)`), return its `job_id` and do not create a new object or job.
5. Persist the immutable private MinIO object (WP1).
6. Insert `ingestion_jobs(state=queued, ...)`. If the insert fails, compensate by
   deleting the just-stored object.
7. Enqueue the Celery task with the `job_id`.
8. Return `202 {job_id, batch_id, state: "queued"}`.

Object persistence precedes job creation; the object and the row are not one
database transaction, so step 6 uses compensating deletion on failure (WP1
pattern).

### 7.2 Worker path

1. Claim the job: set `state=parsing`, `lease_expires_at = now +
   INGESTION_LEASE_SECONDS`, `attempts += 1`; commit.
2. **Download the raw object from MinIO** to a temporary file (the worker does
   not rely on any request-scoped file).
3. MinerU parse.
4. `state=extracting`; commit. LLM extraction.
5. Insert or resolve the candidate by `pii_hash` (`on_conflict_do_nothing`,
   existing-object verification, redundant-object cleanup — existing
   `run_parse_and_score` behavior); backfill `candidate_id`.
6. If `jd_code` is present: `state=scoring`; commit. Run `ScoringPipeline`; upsert
   the score by `(candidate_id, jd_id, rule_version_id)` — on conflict, reuse the
   existing score row rather than inserting a duplicate — and backfill `score_id`
   from the resulting row; `state=completed`. Otherwise `state=ready`.
7. Delete the temporary file in a `finally` block.

Each state transition is committed separately from the external call that
precedes it. A worker that dies mid-call leaves the job in a processing state
with an expired lease, which the sweeper reclaims. Because candidate insertion
and score upsert are idempotent, re-running a step never duplicates a row.

### 7.3 Beat sweeper

A periodic Celery Beat task, every `INGESTION_SWEEP_INTERVAL_SECONDS`:

1. Reclaim: any job in a processing state with `lease_expires_at < now`
   transitions to `retryable_failed` (worker presumed dead).
2. Re-enqueue: any `retryable_failed` job with `attempts < INGESTION_MAX_ATTEMPTS`
   transitions to `queued` and is enqueued.
3. Terminate: any `retryable_failed` job with `attempts >= INGESTION_MAX_ATTEMPTS`
   transitions to `terminal_failed`.

The sweeper is idempotent and safe to run concurrently with workers because it
selects by state and lease and uses row-level locking (`SELECT ... FOR UPDATE
SKIP LOCKED`).

## 8. Transactions and Idempotency

- Object upload and job insertion are not one transaction; failed job insertion
  compensates by deleting the object.
- Worker steps commit state transitions separately from external calls.
- Upload idempotency key: `raw_file_sha256`. A resubmitted identical file returns
  the existing non-terminal job.
- Candidate idempotency: existing `pii_hash` unique constraint with
  `on_conflict_do_nothing`.
- Score idempotency: `unique(candidate_id, jd_id, rule_version_id)` upsert.
- Duplicate candidate detection must not orphan the newly uploaded object; the
  existing redundant-object cleanup is retained.
- Route and task handlers map only typed boundary exceptions; they do not catch
  broad exceptions except at a stable protocol boundary.

## 9. HTTP Surface and Error Mapping

| Method | Route | Protection | Purpose |
|---|---|---|---|
| POST | `/api/v1/candidates/upload` | JWT `hr`/`hr_lead`/`admin` | Persist, create one job, enqueue; `202 {job_id}` |
| POST | `/api/v1/candidates/batch` | same | Persist N files, create a batch + N jobs; `202 {batch_id, job_ids}` |
| GET | `/api/v1/candidates/jobs/{job_id}` | same | `{state, attempts, last_error_code, candidate_id, score_id, batch_id}` |
| GET | `/api/v1/candidates/batches/{batch_id}` | same | `{total, by_state: {queued, parsing, ..., completed, terminal_failed}}` |

- Upload/batch validation failures keep WP1 codes (`invalid_upload`,
  `file_too_large`, `unsupported_media_type`, `invalid_document`,
  `object_storage_unavailable`).
- Job and batch status endpoints return `404` for unknown ids.
- Status responses expose only the job's own metadata: no provider bodies, no
  PII plaintext, no local paths, no signed URLs.
- Parser/AI failures are not surfaced synchronously by the upload endpoint
  anymore; they are recorded on the job as `last_error_code` and observed through
  the status endpoint.

## 10. Batch Upload

`POST /api/v1/candidates/batch` accepts one multipart request carrying up to
`INGESTION_BATCH_MAX_FILES` files.

- A single `batch_id` (UUID) is generated for the request.
- Each file is streamed, validated, and persisted independently; each becomes one
  `ingestion_jobs` row with the shared `batch_id`.
- A file that fails validation or object persistence is reported synchronously
  in the `202` response body as `{state: "terminal_failed", error_code}` and
  does not abort the batch; it does NOT create a durable `ingestion_jobs` row
  (the file was never stored, so there is nothing to persist a job for).
- The response is `202 {batch_id, jobs: [{job_id, state, error_code?}, ...]}`.
  `job_id` is present only for files that were successfully queued.
- Batch progress is read from `GET /candidates/batches/{batch_id}` as a
  `state -> count` aggregate. Because rejected files never became
  `ingestion_jobs` rows, this aggregate reflects only successfully-queued
  jobs — it does not know about, and cannot report, per-file validation/storage
  failures. A batch in which every file was rejected produced zero durable
  jobs, so `GET /batches/{id}` returns `404` for it exactly as it would for an
  unknown batch id.

## 11. Retention and Cleanup Scope

- WP3 implements the `deleted` job state and guarantees no orphaned object or
  partial row after a failed or rejected ingestion (compensating deletion, WP1
  pattern extended to jobs).
- Full retention windows, tombstones, and scheduled purge (roadmap §7.6) are
  explicitly deferred to a later production-readiness package.

## 12. Runtime Configuration

New settings (validated at startup):

- `INGESTION_MAX_ATTEMPTS` (default 3)
- `INGESTION_LEASE_SECONDS` (worker claim lease; default 900)
- `INGESTION_SWEEP_INTERVAL_SECONDS` (Beat cadence; default 60)
- `INGESTION_BATCH_MAX_FILES` (default 50)

Celery Beat must be added to the deployment (a beat process alongside the worker)
and documented in the quick start and compose files.

## 13. Testing and Contract Evidence

Default CI stays offline and deterministic.

### 13.1 Offline deterministic tests

- State-machine transition legality: allowed transitions succeed, illegal
  transitions raise, `state` is only written through transitions.
- Upload idempotency: identical `raw_file_sha256` returns the existing
  non-terminal job and creates no second object or job.
- Error classification: 503-class -> `retryable_failed`; 502-class and validation
  -> `terminal_failed`.
- Sweeper: expired-lease processing jobs are reclaimed; `retryable_failed` under
  the cap is re-enqueued; at the cap becomes `terminal_failed`; `SKIP LOCKED`
  prevents double processing.
- Score upsert idempotency: re-running scoring for the same candidate/JD/rule
  version does not duplicate a row.
- Status endpoints: shapes, `404` on unknown id, and no secret/PII leakage.

### 13.2 Integration tests (real PostgreSQL, Redis, MinIO, worker, beat)

- Upload -> `202` -> worker completes -> job `completed`, candidate and score
  present, temporary file deleted.
- Worker crash simulated by lease expiry -> sweeper reclaims -> job eventually
  `completed` with no duplicate candidate or score.
- Batch with a mix of valid and invalid files -> valid jobs complete, invalid
  jobs `terminal_failed`, batch aggregate correct.
- Terminal failure does not retry; retryable failure retries up to the cap.
- Migration upgrade/downgrade from the previous released revision, plus the
  duplicate-score reconciliation gate.
- Post-run clean-state assertions find no orphaned objects, rows, Redis keys, or
  temporary files.

## 14. Rollout and Rollback

Rollout order:

1. Apply the migration (job table, score uniqueness) after the duplicate-score
   reconciliation gate passes.
2. Deploy the worker and a Beat process.
3. Switch the upload endpoint to asynchronous behavior.

Rollback sets the application back to the previous image. Because the upload
contract changes from synchronous to `202`, rollback restores the prior
synchronous endpoint; the job table remains and is harmless. No automatic
fallback to the synchronous in-request pipeline is retained in the async image.

## 15. Exit Criteria

WP3 is complete only when:

- Upload responds after durable object storage and job creation, not after
  parsing/scoring.
- Workers can restart (lease expiry + sweeper) without duplicate candidates or
  scores.
- Batch progress is queryable through the batch status endpoint.
- Upload idempotency, score idempotency, retry cap, and terminal-failure behavior
  are tested.
- Migration upgrade/downgrade and the duplicate-score reconciliation gate pass.
- Unit, integration, Ruff, mypy, migration, cleanup, and hosted CI (Python 3.10,
  Python 3.14, strict integration) gates pass.
- Exact commits, test counts, and run URLs are recorded.
- WP4 is changed to Ready for planning only after every gate passes.

## 16. Approval

Approval of this specification means implementation may proceed. WP3 completion
remains blocked until the full offline and integration gate and hosted CI pass,
and until the duplicate-score reconciliation gate is satisfied on the configured
deployment.
