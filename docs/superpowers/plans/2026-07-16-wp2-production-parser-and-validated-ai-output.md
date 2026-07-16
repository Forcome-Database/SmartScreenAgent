# WP2 Production Parser Contract and Validated AI Output Implementation Plan

**Status:** In progress; specification approved on 2026-07-16.

**Specification:** [`../specs/2026-07-16-wp2-production-parser-and-validated-ai-output-design.md`](../specs/2026-07-16-wp2-production-parser-and-validated-ai-output-design.md)

**Goal:** Replace the assumed MinerU and weak LLM JSON boundaries with versioned external contracts and deterministic validation that prevents unsupported parser artifacts or fabricated scores from being persisted.

**Architecture:** A typed MinerU adapter performs protocol-2 health, task submission, polling, bounded result download, and safe artifact parsing. The LLM gateway exposes typed errors and explicit structured-output configuration. Pydantic extraction models and contextual judge validation run before the existing transactional ingestion/scoring boundary persists anything.

**Tech stack:** Python 3.10-3.14, FastAPI, Pydantic v2, httpx, OpenAI Python SDK, SQLAlchemy async, MinIO, pytest, respx, Ruff, mypy.

## File Map

### Add

- `backend/app/services/parser/contracts.py`
- `backend/app/services/parser/errors.py`
- `backend/app/services/parser/result_archive.py`
- `backend/app/services/llm/errors.py`
- `backend/app/services/llm/structured_output.py`
- `backend/tests/contracts/mineru/3.4.4/`
- `backend/tests/contracts/newapi/`
- `backend/tests/fixtures/resumes/`
- `backend/tests/unit/test_mineru_result_archive.py`
- `backend/tests/unit/test_structured_output.py`
- `backend/tests/external/test_mineru_runtime_contract.py`
- `backend/tests/external/test_newapi_runtime_contract.py`
- `scripts/verify_external_contracts.py`

### Modify

- `.gitignore`
- `.env.example`
- `pyproject.toml`, `uv.lock` if required by the approved implementation
- `backend/app/config.py`
- `backend/app/services/parser/mineru_client.py`
- `backend/app/services/parser/extractor.py`
- `backend/app/services/llm/gateway.py`
- `backend/app/services/llm/schemas.py`
- `backend/app/scoring/llm_judge.py`
- `backend/app/scoring/pipeline.py`
- `backend/app/tasks/ingest.py`
- `backend/app/routers/candidates.py`
- parser, extractor, gateway, judge, pipeline, candidate API, task-ingest, and P2 E2E tests
- `scripts/verify.py`
- `README.md`
- `docs/specs/research/mineru.md`
- `docs/specs/research/newapi.md`
- `docs/superpowers/plans/README.md`
- `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`

## Execution Rules

- [x] Obtain explicit approval of the WP2 specification before implementation.
- Work test-first for every deterministic parser, archive, schema, evidence, error, and rollback behavior.
- Keep default CI offline and deterministic; never call paid models from the default test suite.
- Treat `external_contract` as a separate strict gate that fails rather than skips when explicitly invoked.
- Capture only sanitized synthetic runtime fixtures; never commit candidate PII, API keys, headers, or raw provider bodies.
- Do not start WP3 job-state, retry scheduling, batch, or status API work in WP2.
- Keep the synchronous application workflow until WP3; only the MinerU wire protocol is asynchronous here.
- Use scoped commits and exclude existing `.superpowers/`, `backend.zip`, and `.firecrawl/` artifacts.

## Task 1: Lock official contract evidence and settings

**Files:** research docs, config, environment example, contract fixtures, configuration tests.

- [ ] Add failing settings tests for protocol, backend, effort, language, polling, task deadline, result limits, and structured-output mode.
- [ ] Update MinerU research from 3.1.11 to official release 3.4.4 and API protocol 2.
- [ ] Record official route, multipart, status, and ZIP artifact shapes from `fast_api.py` and `api_client.py`.
- [ ] Add source-derived sanitized fixtures for health, submission, pending, completed, failed, and malformed responses.
- [ ] Keep runtime-captured fixtures separate and clearly labeled with service version/date.
- [ ] Add `.firecrawl/` to `.gitignore` without committing fetched pages.
- [ ] Run focused config/fixture tests, Ruff, and mypy.
- [ ] Commit contract baseline and configuration independently.

## Task 2: Introduce typed MinerU models and errors

**Files:** parser contracts/errors, MinerU client, unit tests.

- [ ] Add failing tests for valid/invalid health, submission, and status payloads with `extra=forbid`.
- [ ] Add failing tests for protocol mismatch, inconsistent task IDs, unknown states, and bounded task IDs.
- [ ] Define typed health, submission, status, parse-result, and error models.
- [ ] Split unavailable, contract-invalid, and task/result failure errors without provider body leakage.
- [ ] Preserve cancellation and exception chaining.
- [ ] Run focused parser contract tests, Ruff, and mypy.
- [ ] Commit typed MinerU boundary.

## Task 3: Implement health, submit, and poll protocol

**Files:** MinerU client/config/tests.

- [ ] Add failing tests for exact `/health` and `/tasks` calls, repeated `files` multipart field, and every configured form field.
- [ ] Add failing tests proving returned status/result URLs cannot redirect the client off the configured origin.
- [ ] Add pending/processing/completed polling tests with deterministic clocks or injected sleep.
- [ ] Add deadline, connection, timeout, 404, 409, 5xx, malformed JSON, and cancellation tests.
- [ ] Implement protocol-2 health negotiation and construct status/result URLs from the validated task ID.
- [ ] Emit structured metadata-only logs with trace ID and elapsed time.
- [ ] Run focused tests, Ruff, and mypy.
- [ ] Commit protocol client independently of archive parsing.

## Task 4: Implement bounded ZIP result handling

**Files:** result archive service, MinerU client, archive tests.

- [ ] Add failing tests for streaming compressed-size limit and ZIP signature/content-type validation.
- [ ] Add malicious fixtures for absolute paths, drive paths, `..`, backslash traversal, symlinks, encrypted members, duplicate names, member count, decompression ratio, and total uncompressed size.
- [ ] Add result-shape tests for missing, empty, and duplicate Markdown plus malformed content-list JSON.
- [ ] Implement member-by-member validation without wholesale extraction.
- [ ] Require one non-empty Markdown result for a one-file task and parse optional content-list data.
- [ ] Prove temporary result files are deleted on success, failure, timeout, and cancellation.
- [ ] Run focused archive/client tests, Ruff, and mypy.
- [ ] Commit result artifact handling.

## Task 5: Make the LLM gateway typed and capability-explicit

**Files:** gateway, LLM errors/schemas, config, gateway tests.

- [ ] Add failing tests for system/user message separation and JSON-encoded untrusted resume payloads.
- [ ] Add failing tests for named strict `json_schema` and configured `json_object` modes.
- [ ] Add typed tests for connection, timeout, rate limit, 5xx, authentication, authorization, invalid request, empty choice, and missing usage.
- [ ] Replace broad `except Exception` fallback with explicit retryable classifications.
- [ ] Enforce at most primary plus fallback attempt; never fall back for auth/configuration errors.
- [ ] Record trusted model, prompt version, token counts, latency, attempt, and outcome without prompt/completion bodies.
- [ ] Run gateway tests, Ruff, and mypy.
- [ ] Commit the gateway boundary.

## Task 6: Validate extracted resumes with Pydantic

**Files:** extractor, structured-output helpers, extraction tests, ingest tests.

- [ ] Add failing tests for valid Chinese resume extraction and correct UTF-8 prompt text.
- [ ] Add failing tests for invalid JSON, wrong top-level type, missing/extra keys, booleans as age, out-of-range age, empty required experience fields, invalid dates, and excessive lists/strings.
- [ ] Convert extraction dataclasses to strict Pydantic models while keeping current attribute access compatible.
- [ ] Normalize optional empty strings to null and preserve canonical dates.
- [ ] Put trusted schema/prompt/model/token metadata under `extracted_json._meta`.
- [ ] Prove two invalid model attempts raise a typed invalid-output error and persist nothing.
- [ ] Run extractor, ingest, candidate API, Ruff, and mypy checks.
- [ ] Commit typed extraction.

## Task 7: Validate judge output against rules and evidence

**Files:** judge, structured-output helpers, pipeline, rule/judge/pipeline tests.

- [ ] Add failing tests for unknown, duplicate, and missing dimension IDs.
- [ ] Add failing tests for invalid tier, tier/score mismatch, boolean/NaN/infinity scores, invalid confidence, empty reasoning, excessive questions, and extra keys.
- [ ] Add failing tests for non-unknown results without evidence, fabricated evidence, Unicode/whitespace-normalized valid evidence, and invalid evidence for unknown tiers.
- [ ] Implement strict raw models plus contextual validation against active `JudgeDimension` objects.
- [ ] Reorder validated output to rule-definition order and recompute subtotal/total only from validated scores.
- [ ] Persist prompt/model/token metadata from the gateway, never from the model body.
- [ ] Prove invalid judge output cannot insert a score or score audit row.
- [ ] Run judge, pipeline, P2 E2E, Ruff, and mypy checks.
- [ ] Commit judge/evidence validation.

## Task 8: Stabilize application errors and transaction outcomes

**Files:** candidate router, ingest/pipeline services, API/integration tests.

- [x] Add upload tests for parser unavailable 503, parser contract invalid 502, parser failure 502, AI unavailable 503, and AI invalid output 502.
- [x] Add re-score tests for the same AI errors with stable bodies and rollback.
- [ ] Prove provider bodies, URLs containing credentials, paths, prompt text, completion text, and PII never appear in responses or structured logs.
- [x] Prove upload-with-JD failures leave no candidate, score, audit, object, or temporary file.
- [x] Prove re-score failures preserve the candidate and prior scores while creating no partial new score/audit.
- [x] Map only typed boundary exceptions; remove broad route exception translation.
- [x] Run strict candidate API, pipeline, task-ingest, and P2 E2E integration tests.
- [x] Commit application error and transaction behavior.

Local strict verification on 2026-07-16 passed 179 offline tests and 42 integration
tests with zero skips, followed by Ruff, mypy, migration, PostgreSQL, Redis,
MinIO, Celery, and temporary-file clean-state checks. The external-contract gate
remains blocked on real MinerU and new-api configuration; WP2 is still In progress.

## Task 9: Add external runtime contract gates

**Files:** external tests, fixtures, verification script, documentation.

- [ ] Add synthetic, non-PII PDF, DOCX, PNG, and JPEG fixtures suitable for the real parser.
- [ ] Add a command that requires external endpoints/credentials and fails on missing configuration or skipped `external_contract` tests.
- [ ] Capture deployed MinerU health, OpenAPI, task/result responses, sanitized artifacts, service version, and protocol version.
- [ ] Verify all four supported input formats produce non-empty validated Markdown/extraction.
- [ ] Capture the deployed new-api model list reduced to configured model IDs.
- [ ] Probe configured primary/fallback extraction and judge models for the selected structured-output mode.
- [ ] Record exact runtime test counts, endpoint environment name, service/model versions, artifact inventory, and run location without secrets.
- [ ] Commit sanitized runtime evidence only after review.

## Task 10: Full verification, rollout documentation, and WP2 exit review

**Files:** README, plan index, roadmap, research docs, this plan.

- [ ] Update quick start and environment documentation for MinerU protocol/configuration and structured-output modes.
- [ ] Document stable parser/AI error codes, runtime verification, rollback, and synthetic fixture policy.
- [ ] Run the complete local matrix:

  ```bash
  uv sync --extra dev --locked
  uv run pytest -m "not integration and not external_contract" -q
  uv run ruff check backend
  uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
  uv run python scripts/verify.py
  uv run python scripts/verify_external_contracts.py
  ```

- [ ] Push scoped commits and confirm hosted Python 3.10, Python 3.14, and strict integration jobs.
- [ ] Record exact commits, test counts, service/model versions, contract artifacts, cleanup evidence, and run URLs.
- [ ] Mark WP2 Complete and WP3 Ready for planning only after every offline and external exit criterion passes.
- [ ] Commit documentation and completion evidence.

## Required Exit Evidence

- Approved WP2 specification.
- Exact scoped commit range.
- MinerU release, service version, and API protocol version.
- Sanitized OpenAPI, health, task, and result artifact inventory.
- Configured new-api model IDs and structured-output capability results.
- Offline unit/integration counts with zero failures and strict integration zero skips.
- External-contract count with zero failures and zero skips.
- Four-format MinerU parse/extraction results using synthetic fixtures.
- Invalid extraction/judge rollback and evidence-provenance results.
- Stable HTTP error matrix.
- Ruff, mypy, migration/clean-state, and hosted Actions evidence.
- Clean tracked working tree excluding pre-existing local artifacts.
