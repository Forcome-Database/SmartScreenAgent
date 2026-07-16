# WP1 Security and Raw-File Integrity Design

**Date:** 2026-07-16

**Status:** Approved on 2026-07-16

**Work package:** WP1

**Depends on:** WP0 complete

**On approval, incorporates and supersedes for implementation:** `2026-07-08-jwt-rbac-p2-api-design.md`

## 1. Purpose

WP1 closes the two risks that currently prevent production deployment:

1. Candidate write APIs are public even though JWT utilities already exist.
2. A successful upload stores a temporary local path in `Candidate.raw_file_key`, then deletes that file.

After WP1, authenticated HR roles can upload and score resumes, every newly accepted candidate references a verified private MinIO object, and failures do not silently leave a new candidate row or an untracked object.

This package deliberately keeps parsing and scoring synchronous. Durable jobs, retries, and batch status belong to WP3 after the real parser and AI contracts are established in WP2.

## 2. Goals

- Require a valid Bearer JWT for candidate upload and re-score routes.
- Authorize `hr`, `hr_lead`, and `admin`; reject all other roles.
- Use the current database user role as authority rather than trusting the JWT role claim.
- Normalize authentication failures without exposing JWT or OAuth library messages.
- Stream uploads to a bounded temporary file while calculating SHA-256.
- Validate extension, declared media type, file signature, empty files, file size, and protected/corrupt PDF or DOCX inputs.
- Persist the original file to a private MinIO bucket before parsing.
- Use immutable, opaque object keys that contain no PII or original filename.
- Store checksum, size, media type, and encrypted original filename with the candidate.
- Define explicit duplicate semantics and compensating cleanup.
- Cover the complete HTTP dependency chain and real MinIO/PostgreSQL behavior in strict integration verification.

## 3. Non-goals

- Candidate or JD ownership/department filtering.
- Read APIs or PII display authorization; these belong to WP4.
- Asynchronous ingestion state, batch upload, durable retry, or dead-letter handling; these belong to WP3.
- A production malware engine. WP1 adds a scanner interface and a disabled-by-default adapter only.
- The real MinerU submission/poll/download contract or typed LLM output validation; these belong to WP2.
- Retention, deletion, or tombstone workflows; those must be complete before production data is loaded but are not implemented in WP1.
- Frontend login or token storage.

## 4. Existing Constraints

- `Candidate.pii_hash` is unique, so the current domain model represents one candidate record per normalized identity.
- `ScoringPipeline.run` commits internally, which prevents an upload from owning one database transaction. WP1 must move the commit boundary to its caller.
- MinIO's Python client is synchronous, while the upload route is async. Storage calls must run off the event loop.
- Existing rows may contain local temporary paths in `raw_file_key`. The migration cannot truthfully convert them into object keys.
- PostgreSQL and MinIO cannot participate in one atomic transaction. Cleanup is compensating, observable work rather than a distributed transaction.

## 5. Authentication and Authorization Contract

### 5.1 Route policy

| Route | Authentication | Allowed roles |
|---|---|---|
| `GET /` | Public | All |
| `GET /healthz` | Public | All |
| `POST /auth/dingtalk/login` | Public | All |
| `POST /api/v1/candidates/upload` | Bearer JWT | `hr`, `hr_lead`, `admin` |
| `POST /api/v1/candidates/{candidate_id}/score` | Bearer JWT | `hr`, `hr_lead`, `admin` |

`dept_head` remains denied because the schema has no department ownership fields.

### 5.2 Authority and error behavior

- JWT `sub` identifies a user; the user must still exist in PostgreSQL.
- The role loaded from `users.role` is authoritative. A stale or forged JWT `role` claim does not grant access.
- Invalid signature, expiry, missing/invalid `sub`, and decoding failures all return `401 Invalid token`.
- Missing or non-Bearer authorization returns `401 Missing Bearer token`.
- Missing users return `401 User not found`.
- Disallowed database roles return `403 Forbidden`.
- Every `401` includes `WWW-Authenticate: Bearer`.
- Internal exception text is retained only through exception chaining and structured logs.

`backend/app/deps.py` exposes a reusable `require_roles(*roles)` dependency returning the authenticated `User`.

### 5.3 DingTalk login boundary

The login route keeps its current request and success response. External HTTP, invalid response, and credential exchange failures return a stable `400 DingTalk OAuth failed`; provider bodies, access tokens, application secrets, and low-level exception messages are never returned to the caller.

## 6. Upload Contract

### 6.1 Supported inputs

WP1 accepts:

| Extension | Canonical media type | Signature/content check |
|---|---|---|
| `.pdf` | `application/pdf` | `%PDF-` plus readable, non-encrypted PDF |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | ZIP container containing `[Content_Types].xml` and `word/document.xml` |
| `.png` | `image/png` | PNG signature |
| `.jpg`, `.jpeg` | `image/jpeg` | JPEG start/end markers |

Legacy `.doc` is not accepted because the current stack cannot safely distinguish supported, corrupt, and encrypted OLE documents. It may be added later with a verified parser contract.

`application/octet-stream` is tolerated only when filename extension and inspected content agree. A specific declared type that conflicts with inspected content is rejected.

### 6.2 Streaming and limits

- `MAX_RESUME_FILE_BYTES` is configurable and defaults to 20 MiB.
- The route reads `UploadFile` in 1 MiB chunks into a named temporary file.
- Size and SHA-256 are calculated during that pass.
- The request is rejected immediately once the configured limit is exceeded.
- The temporary filename uses an application-selected safe suffix, never the caller's full filename.
- The temporary file is deleted in `finally`, including client, validation, storage, parser, extractor, database, and scoring failures.

### 6.3 Validation errors

Candidate upload errors use `{"detail": {"code": "...", "message": "..."}}` for machine-stable handling.

| Condition | HTTP | Code |
|---|---:|---|
| Empty filename or empty body | `400` | `invalid_upload` |
| Size limit exceeded | `413` | `file_too_large` |
| Unsupported extension/type/signature or type mismatch | `415` | `unsupported_media_type` |
| Corrupt or password-protected supported document | `422` | `invalid_document` |
| Existing duplicate has missing/inconsistent raw-file metadata or object | `409` | `candidate_file_conflict` |
| Parser failure | `502` | `resume_parser_failed` |
| Object storage unavailable or write verification fails | `503` | `object_storage_unavailable` |

Errors never include file content, local paths, MinIO credentials, object endpoints, or provider response bodies.

### 6.4 Malware-scanning seam

Validation calls a `MalwareScanner` protocol after the bounded local file is complete and before MinIO persistence. WP1 ships `DisabledMalwareScanner`, selected through `MALWARE_SCAN_MODE=disabled`. Unknown configured modes fail closed at startup/use. A future adapter can return clean, infected, or unavailable without changing route and ingestion contracts.

## 7. Object Storage Contract

### 7.1 Object identity and metadata

- Object key: `resumes/<yyyy>/<mm>/<uuid4-hex>`.
- Keys contain no candidate ID, name, phone, email, original filename, or user-controlled path.
- A generated key is used for exactly one `put_object`; application code never overwrites it.
- MinIO metadata includes SHA-256 and canonical content type but not plaintext original filename.
- The original filename is encrypted with the existing Fernet PII key before database persistence.

The candidate stores:

- `raw_file_key`
- `raw_file_sha256`
- `raw_file_size_bytes`
- `raw_file_content_type`
- `raw_file_original_name_cipher`

The new columns remain nullable at the database level for legacy rows. The WP1 application path requires all five for every newly created upload candidate.

### 7.2 Verified private persistence

`MinIOStorage.put_object` accepts metadata and raises typed storage errors; it does not swallow failures. After upload, the storage service calls `stat_object` and verifies key, length, and SHA-256 metadata before parsing begins.

Integration tests prove:

- The object exists and its bytes/checksum/size match the upload.
- Direct anonymous access is denied.
- A short-lived presigned URL can read the object.
- Delete removes the object and reports failures rather than silently ignoring them.

All synchronous MinIO calls from async application code run through a worker thread.

## 8. Ingestion and Transaction Contract

### 8.1 Success path

```text
authenticate and authorize
  -> stream + hash + validate local file
  -> malware scanner seam
  -> put and verify private MinIO object
  -> MinerU parse local bounded file
  -> LLM extraction
  -> candidate insert or duplicate resolution
  -> optional scoring
  -> one database commit
  -> delete local temporary file
```

The MinIO object is persisted before parsing, as required by the roadmap. Parsing continues from the validated local copy during WP1; WP2 may change the parser adapter without changing object ownership.

### 8.2 Database commit ownership

`ScoringPipeline.run` stops committing and only flushes/refreshes. Its application-service callers own commit/rollback:

- `run_parse_and_score` commits candidate plus optional score atomically.
- The re-score application path commits one new score atomically.
- Tests that call the pipeline directly explicitly commit when persistence outside their session is being asserted.

This prevents a new candidate from being committed before optional scoring completes.

### 8.3 Duplicate semantics

Duplicate identity is still determined by the existing `pii_hash` contract.

- The insert uses `ON CONFLICT DO NOTHING ... RETURNING id` to distinguish a new row from a duplicate.
- For a duplicate, the new MinIO object is deleted and the existing candidate is returned.
- The response uses `status: "duplicate"` rather than claiming that a new candidate was parsed.
- If `jd_code` is present, re-scoring the existing candidate remains allowed and commits independently with the request.
- The existing candidate's file reference is not overwritten. WP1 does not silently discard the earlier accepted source document.
- Before returning duplicate success, the existing candidate's key and metadata are stat-verified against MinIO. Missing legacy metadata, a missing object, or a mismatch causes `409 candidate_file_conflict`; the new object is deleted and neither source reference is silently replaced.
- Cleanup failure is a typed storage failure and is logged with trace ID and object key; success is not returned while the new object is unaccounted for.

This contract avoids both silent candidate reuse and orphaned duplicate objects. Supporting multiple resume versions per person requires a future candidate-file entity and is not introduced in WP1.

### 8.4 Celery entry-point boundary

The existing Celery entry point must no longer accept an arbitrary local `file_path`. It accepts a serialized verified raw-file reference containing object key, SHA-256, size, canonical content type, and encrypted original filename. The worker downloads the private object to a safe temporary file, verifies size and SHA-256 again, calls the same application service, and removes the local copy in `finally`.

WP1 does not enqueue this task from the upload route. That switch requires the durable job record and state machine in WP3. This change only prevents the existing internal entry point from bypassing WP1 file ownership.

### 8.5 Failure and compensation

| Failure point | Database action | Object action |
|---|---|---|
| Validation/scanner | No candidate transaction | No object exists |
| MinIO put/verification | No candidate transaction | Best-effort removal of partial generated key |
| Parser/extractor | Roll back | Delete new object |
| Candidate insert/optional score | Roll back | Delete new object |
| Duplicate resolution | Preserve existing candidate; roll back new work as needed | Delete new object |

Compensation uses bounded retries. If MinIO is unavailable during deletion, the request fails, a critical structured event records the object key/checksum/trace ID, and operators must reconcile it. WP3 will introduce durable reconciliation; WP1 guarantees that cleanup failure is never hidden as success.

## 9. API Compatibility

Changes visible to clients:

- Candidate write routes now require Bearer authorization.
- Upload validation introduces `400`, `413`, `415`, `422`, and `503` responses.
- Parser error detail changes from a string to the structured error shape with code `resume_parser_failed`.
- Upload success `status` is `parsed` for a newly created candidate and `duplicate` for an existing identity.
- A duplicate whose prior raw-file reference is not verifiable returns `409 candidate_file_conflict`.

The successful response fields and re-score success response remain otherwise unchanged.

## 10. Migration and Legacy Data

A new Alembic revision adds the four metadata columns alongside the existing `raw_file_key`. Upgrade and downgrade are tested both from an empty database and from revision `3884ec28fea9` containing a representative legacy candidate.

Legacy rows retain nullable metadata and their current `raw_file_key`; WP1 does not claim those local paths are valid MinIO objects. Before a deployment containing existing real candidates is promoted, operators must either:

1. backfill the source files and metadata, or
2. mark the legacy candidates unavailable for raw-file access.

The rollout checklist must count legacy rows with incomplete raw-file metadata. A non-zero count blocks a claim that all historical files are durable.

## 11. Testing Strategy

### Unit tests

- Token failures including malformed/missing `sub` and database-role authority.
- Role dependency allow/deny behavior.
- Chunked size enforcement and SHA-256 calculation.
- Extension/MIME/signature matrix.
- Empty, corrupt, and protected document behavior.
- Opaque object-key format and absence of user-controlled text.
- Duplicate result and compensation decisions with fake storage.
- Typed storage errors and stable HTTP error mapping.

### Integration tests

- Full candidate routes with real JWT decoding and PostgreSQL user lookup.
- All allowed roles and at least one denied/unknown role.
- Unauthorized requests prove parser, storage, and scoring are not invoked.
- Real MinIO put/stat/get/private/presign/delete behavior.
- Successful upload proves database metadata matches the existing object.
- Parser/extractor/database failure proves no new candidate and no object remain.
- Duplicate upload returns `duplicate` and leaves only the original candidate object.
- Re-score route remains functional for allowed roles.
- Migration upgrade/downgrade from empty and previous revision.

Paid LLM and production MinerU calls remain controlled fakes, while PostgreSQL and MinIO are real disposable services.

## 12. Observability and Data Protection

- Log validation/storage outcomes, size, canonical media type, checksum prefix, object key, actor user ID, and trace ID.
- Never log file bytes, parsed resume text, plaintext original filename, PII, JWT, OAuth token, or MinIO credentials.
- Audit successful upload and duplicate resolution with actor `user:<id>` and target candidate ID.
- Authentication failures are access-log events; they do not create database audit rows.
- Object keys are sensitive operational identifiers even though they contain no PII.

## 13. Rollout and Rollback

1. Apply the additive migration.
2. Verify the private bucket and service credentials.
3. Run the strict verification suite.
4. Count legacy candidates missing raw metadata and resolve or explicitly quarantine them.
5. Deploy application code with candidate write routes protected.
6. Smoke-test login, authorized upload, denied anonymous upload, object persistence, and re-score.

Rollback application code is safe while the additive columns remain. Downgrading the migration is allowed only after confirming no new candidate depends on the metadata columns. Objects created by a rolled-back deployment must be reconciled before database metadata is removed.

## 14. Acceptance Criteria

WP1 is complete only when:

- Anonymous or unauthorized candidate writes fail before business logic runs.
- `hr`, `hr_lead`, and `admin` pass through the real dependency chain.
- Every successful new upload references an existing private object with matching checksum and size.
- Validation and parser/extractor/database failures create neither a new candidate nor an unreported object.
- Duplicate uploads are explicit and leave no second object.
- Duplicate success is returned only after the existing candidate object is verified; legacy conflicts fail explicitly.
- Authentication and storage errors have stable, non-sensitive responses.
- Migration upgrade/downgrade, 102+ non-integration tests, all integration tests with zero skips, Ruff, and mypy pass.
- README, OpenAPI behavior, roadmap traceability, and completion evidence agree with the implementation.

## 15. Open Approval Decisions

Approval of this specification confirms these deliberate compatibility choices:

1. Maximum upload size defaults to 20 MiB.
2. WP1 supports PDF, DOCX, PNG, and JPEG; legacy DOC is rejected.
3. Duplicate identity returns the existing candidate with `status: duplicate` and deletes the newly uploaded object.
4. Original filenames are encrypted in PostgreSQL and omitted from MinIO metadata.
5. Upload error details use a structured `{code, message}` object.
