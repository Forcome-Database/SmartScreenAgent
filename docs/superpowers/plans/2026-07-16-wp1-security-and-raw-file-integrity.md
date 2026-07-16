# WP1 Security and Raw-File Integrity Implementation Plan

**Status:** In progress; specification approved on 2026-07-16.

**Specification:** [`../specs/2026-07-16-wp1-security-and-raw-file-integrity-design.md`](../specs/2026-07-16-wp1-security-and-raw-file-integrity-design.md)

**Goal:** Protect candidate writes and make every newly accepted raw resume a verified private object with explicit cleanup behavior.

**Architecture:** FastAPI dependencies enforce authentication and roles at the HTTP boundary. A bounded upload service validates and hashes a local temporary copy. A storage service persists and verifies an opaque MinIO object before the existing parser/extractor runs. Application services own database commit/rollback and compensating object deletion; the scoring pipeline only flushes.

**Tech stack:** Python 3.10-3.14, FastAPI, Pydantic, SQLAlchemy async, Alembic, MinIO, PyJWT, cryptography, pypdf, pytest, Ruff, mypy.

## File Map

### Add

- `backend/app/services/upload/__init__.py`
- `backend/app/services/upload/errors.py`
- `backend/app/services/upload/validation.py`
- `backend/app/services/upload/malware.py`
- `backend/app/services/storage/resume_storage.py`
- `backend/tests/unit/test_auth_dependencies.py`
- `backend/tests/unit/test_upload_validation.py`
- `backend/tests/unit/test_resume_storage.py`
- `migrations/versions/<revision>_wp1_raw_file_metadata.py`

### Modify

- `pyproject.toml`, `uv.lock`
- `.env.example`
- `backend/app/config.py`
- `backend/app/deps.py`
- `backend/app/models/candidate.py`
- `backend/app/routers/auth.py`
- `backend/app/routers/candidates.py`
- `backend/app/scoring/pipeline.py`
- `backend/app/services/dingtalk/oauth.py`
- `backend/app/services/storage/minio_client.py`
- `backend/app/tasks/ingest.py`
- `backend/tests/integration/conftest.py`
- `backend/tests/integration/test_candidates_api.py`
- `backend/tests/integration/test_db_migrations.py`
- `backend/tests/integration/test_minio_client.py`
- `backend/tests/integration/test_p2_e2e.py`
- `backend/tests/integration/test_pipeline.py`
- `backend/tests/integration/test_tasks_ingest.py`
- `README.md`
- `docs/superpowers/plans/README.md`
- `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`

## Execution Rules

- Work test-first for each deterministic behavior.
- Keep paid LLM and production MinerU calls faked; use real disposable PostgreSQL and MinIO for integration gates.
- Do not start WP2 parser-contract work or WP3 job-state work inside this package.
- Maintain the checkboxes and record exact verification evidence before marking WP1 complete.
- Use scoped commits; do not include existing `.superpowers/` or `backend.zip` files.

## Task 1: Add raw-file metadata migration

**Files:** candidate model, new Alembic revision, migration tests.

- [x] Add a failing model test for `raw_file_sha256`, `raw_file_size_bytes`, `raw_file_content_type`, and `raw_file_original_name_cipher`.
- [x] Add a failing migration test that upgrades revision `3884ec28fea9` containing a legacy candidate to head, verifies nullable legacy metadata, then downgrades to the previous revision.
- [x] Add the four nullable columns and appropriate length/non-negative check constraints in a new revision.
- [x] Update `Candidate` mappings. Keep `raw_file_key` nullable for legacy compatibility.
- [x] Update the migration round-trip assertion to expect the new head revision.
- [x] Run:

  ```bash
  uv run pytest backend/tests/unit/test_models.py -q
  uv run pytest backend/tests/integration/test_db_migrations.py -m integration -q
  uv run ruff check backend/app/models backend/tests migrations
  uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
  ```

- [x] Commit the migration and model change (`218079e`).

## Task 2: Normalize authentication and add reusable RBAC

**Files:** `deps.py`, JWT/OAuth boundary, candidates router, auth tests and candidate API tests.

- [x] Add failing tests for missing Bearer, invalid token, expired token, missing/invalid `sub`, missing user, allowed database roles, denied role, and JWT-role/database-role mismatch.
- [x] Add failing tests proving `/` and `/healthz` remain public and candidate writes do not invoke their handlers when authorization fails.
- [x] Normalize all token decoding and subject-conversion failures to `401 Invalid token` with `WWW-Authenticate: Bearer`.
- [x] Implement `require_roles(*roles)` using the database-loaded `User`.
- [x] Attach the dependency to upload and re-score routes for `hr`, `hr_lead`, and `admin`.
- [x] Introduce a typed DingTalk OAuth boundary and change the route to return stable `400 DingTalk OAuth failed` without exception details.
- [x] Add integration helpers that insert a user and issue a real JWT; do not override `get_current_user` in authorization integration tests.
- [x] Run focused unit and strict integration tests.
- [x] Commit authentication/RBAC independently of storage work (`58ab496`).

## Task 3: Implement bounded upload validation

**Files:** upload package, configuration, dependency lock, validation unit tests.

- [x] Add failing table-driven tests for supported extensions, canonical media types, signatures, and allowed `application/octet-stream` behavior.
- [x] Add failing tests for empty upload, size boundary, one byte over the limit, MIME mismatch, unsupported legacy DOC, corrupt DOCX, encrypted DOCX container, corrupt PDF, encrypted PDF, invalid JPEG end marker, and cleanup after every failure.
- [x] Add `MAX_RESUME_FILE_BYTES=20971520`, `UPLOAD_CHUNK_BYTES=1048576`, and `MALWARE_SCAN_MODE=disabled` settings and documented examples.
- [x] Add `pypdf` as a bounded runtime dependency and update `uv.lock`.
- [x] Implement chunked copy, SHA-256 calculation, canonical media detection, and safe temporary suffix selection.
- [x] Implement `UploadArtifact` containing local path, original filename, canonical media type, size, and checksum.
- [x] Implement `UploadValidationError` with stable HTTP status/code mapping at the router boundary.
- [x] Add `MalwareScanner` protocol and `DisabledMalwareScanner`; reject unknown configured modes.
- [x] Verify temporary files are always removed by the route-level `finally` block rather than relying only on background tasks.
- [x] Run unit tests, Ruff, and mypy; commit the validation boundary locally (`991fe5d`).

## Task 4: Strengthen MinIO primitives and private-object verification

**Files:** MinIO client, resume storage service, storage unit/integration tests.

- [x] Add failing unit tests for metadata forwarding, typed put/stat/delete errors, delete retry, opaque key format, and absence of filename/PII in keys.
- [x] Extend `put_object` with metadata and add `stat_object`/`object_exists` primitives.
- [x] Stop swallowing `S3Error` in `delete_object`; translate MinIO failures to typed storage errors while retaining exception chaining.
- [x] Add `ResumeStorageService.store(UploadArtifact)` that generates one key, uploads it, stats it, verifies length and SHA-256 metadata, and returns `StoredResume`.
- [x] Run blocking MinIO calls through `anyio.to_thread.run_sync` from async services.
- [x] Add real MinIO tests for put/stat/get, anonymous denial, presigned access, deletion, checksum/size mismatch, and cleanup.
- [x] Ensure test object prefixes remain isolated and strict verification cleanup detects leaked WP1 test objects.
- [x] Run focused unit and integration tests and commit the storage boundary (`991fe5d`).

## Task 5: Make upload ingestion atomic at the database boundary

**Files:** scoring pipeline, ingestion service/task, tests.

- [x] Add failing tests proving `ScoringPipeline.run` flushes but does not commit and its caller owns commit/rollback.
- [x] Add an ingestion result type containing `candidate_id` and `status` (`parsed` or `duplicate`).
- [x] Change `ScoringPipeline.run` to avoid internal commits in both hard-filter and normal paths; flush and refresh the new score instead.
- [x] Change the re-score application path to commit after a successful pipeline run and roll back on failure.
- [x] Change `run_parse_and_score` to accept verified raw-file metadata while still parsing the validated local copy.
- [x] Insert with `ON CONFLICT DO NOTHING ... RETURNING id` to distinguish newly created and duplicate candidates.
- [x] Persist encrypted original filename plus key/checksum/size/media type on new candidates.
- [x] Commit candidate and optional score once. Roll back on any parser, extractor, database, or scoring exception.
- [x] For duplicates, delete the new object, return the existing candidate with `status=duplicate`, and do not overwrite the original file reference.
- [x] Replace the Celery task's arbitrary `file_path` input with a serialized verified raw-file reference: object key, SHA-256, size, canonical content type, and encrypted original filename.
- [x] In the worker, download the object to a safe temporary suffix, verify size and SHA-256, invoke the shared application service, and delete the local copy in `finally`. Do not enqueue this task from HTTP until WP3 provides a durable job record.
- [x] Run pipeline, task-ingest, and P2 integration tests and commit the ingestion boundary (`991fe5d`).

## Task 6: Wire secure upload, persistence, and compensation into HTTP

**Files:** candidates router, upload/storage services, candidate API/E2E tests.

- [x] Add failing integration tests for every upload status/error in the approved specification.
- [x] Add a successful authorized upload test using real PostgreSQL and MinIO with controlled parser/extractor; assert the candidate metadata matches `stat_object` and object bytes.
- [x] Add failure tests for storage, parser, extractor, database insert, and optional scoring; assert no new candidate and no new object remain.
- [x] Add duplicate tests proving explicit `duplicate` response, one candidate row, one retained object, and verification of the existing object before success.
- [x] Add a legacy/missing-object duplicate test proving `409 candidate_file_conflict`, deletion of the new object, and no silent replacement of the old reference.
- [x] Replace `await file.read()` and `BackgroundTasks` cleanup with the bounded validation service and `finally` cleanup.
- [x] Persist and verify the MinIO object before calling parse/extract.
- [x] Map typed upload/storage/parser failures to the stable response shape without leaking paths or provider text.
- [x] Add upload and duplicate audit rows with `actor=user:<id>` and trace ID when available.
- [x] Confirm unauthorized requests produce no object, candidate, score, or audit row.
- [x] Run candidate API and full P2 E2E integration tests and commit the storage/ingestion work (`991fe5d`).

## Task 7: Verify compensation and clean-state gates

**Files:** verification script/tests, integration isolation helpers as required.

- [x] Add a strict probe that fails if WP1 integration tests skip PostgreSQL or MinIO.
- [x] Add post-run checks for leaked candidate rows, scores/audits, MinIO test objects, and temporary upload files.
- [x] Test delete failure behavior: the API must not return success, and the structured critical log must include trace ID, object key, and checksum without PII.
- [x] Confirm repeated compensation calls are idempotent when the object is already absent.
- [x] Run `uv run python scripts/verify.py` with Docker services and record zero skips and clean-state results.
- [x] Commit verification changes (`b1e0fb4`).

## Task 8: Documentation, rollout, and WP1 exit review

**Files:** README, plan index, roadmap, this plan.

- [x] Update quick-start examples to authenticate and send `Authorization: Bearer`.
- [x] Document supported file types, 20 MiB default limit, structured error codes, duplicate behavior, and private storage.
- [x] Document the legacy raw-file metadata query and rollout gate.
- [x] Mark the older JWT/RBAC design as incorporated/superseded only after implementation matches this specification.
- [x] Run the complete local verification matrix:

  ```bash
  uv sync --extra dev --locked
  uv run pytest -m "not integration" -q
  uv run ruff check backend
  uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
  uv run python scripts/verify.py
  ```

- [x] Confirm hosted [GitHub Actions run 29474031067](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29474031067) passes Python 3.10, Python 3.14, and strict integration jobs at the latest evidence SHA.
- [x] Record exact local and hosted test counts, migration revisions, cleanup evidence, and measured coverage below.
- [x] Update WP1 to Complete and WP2 to Ready for planning in the plan index and authoritative roadmap after every exit criterion passes.
- [x] Commit documentation and completion evidence (`d52aac2`).

## Required Exit Evidence

- Exact commit range for WP1.
- Non-integration test count and zero failures.
- Integration test count with zero skips under strict verification.
- Ruff and mypy success.
- Empty-database and previous-revision migration upgrade/downgrade success.
- Real MinIO privacy, checksum, persistence, duplicate cleanup, and failure compensation results.
- HTTP authorization matrix for all roles and error classes.
- Hosted Actions run URL.
- Legacy candidate metadata count and disposition for the target deployment.
- Clean tracked working tree, excluding the repository's pre-existing untracked local artifacts.

## Completion Evidence

Local implementation gate passed on 2026-07-16:

- Scoped implementation commit range: `218079e^..d52aac2` on `codex/wp1-security-raw-file-integrity`.
- Commit series: migration/model `218079e`, JWT/RBAC `58ab496`, verified raw-resume ingestion `991fe5d`, strict verification `b1e0fb4`, and rollout documentation `d52aac2`.
- `uv run pytest -m "not integration" -q`: 142 passed, 36 deselected.
- Strict `uv run pytest -m integration -q -rs`: 36 passed, 142 deselected, zero skips.
- Alembic upgraded from base through `3884ec28fea9` to head `b57c2f9e1a6d`; the previous-revision legacy-row upgrade/downgrade test passed.
- Real MinIO tests proved private anonymous denial, presigned access, checksum/size metadata, immutable-key persistence, duplicate cleanup, and failure compensation.
- A real Celery worker downloaded a verified object reference, processed it, committed the candidate, and released its event-loop-bound database pool.
- Ruff passed; mypy passed for 59 application source files.
- Non-integration coverage remained 76% overall; `backend/app/deps.py` increased from 0% to 97%, upload validation reached 95%, and resume storage reached 86%.
- Clean-state checks passed for migration databases, application tables, Redis/Celery keys, MinIO objects, and temporary resume files.
- Windows reserved port ranges blocked the historical MinIO test port `59000`; the isolated compose port is now configurable and defaults to `61000`/`61001`.
- Hosted [GitHub Actions run 29474031067](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29474031067) passed Python 3.10, Python 3.14, and strict integration at commit `662b76b` with zero failed or skipped jobs.
- The configured deployment database `localhost:5432/smartscreen` contained zero candidates at revision `3884ec28fea9`. It was upgraded to `b57c2f9e1a6d`, after which the exact legacy metadata query returned zero candidate rows and zero legacy rows.
- Legacy disposition for this configured deployment: no backfill or quarantine is required. A different future deployment database must repeat the same rollout query before promotion.

## Exit Decision

WP1 is **Complete** on 2026-07-16 for the repository's configured deployment scope. WP2 is **Ready for planning**.
