# WP2 Production Parser Contract and Validated AI Output Implementation Plan

**Status:** In progress; specification approved on 2026-07-16.

**Specification:** [`../specs/2026-07-16-wp2-production-parser-and-validated-ai-output-design.md`](../specs/2026-07-16-wp2-production-parser-and-validated-ai-output-design.md)

**Goal:** Replace the assumed MinerU and weak LLM JSON boundaries with versioned external contracts and deterministic validation that prevents unsupported parser artifacts or fabricated scores from being persisted.

**Architecture:** A typed MinerU adapter uses official API v4 to request signed upload URLs, upload source files with an isolated client, poll the batch by `batch_id`, download a bounded result ZIP from an allowlisted host, and safely parse its artifacts. The LLM gateway exposes typed errors and explicit structured-output configuration. Pydantic extraction models and contextual judge validation run before the existing transactional ingestion/scoring boundary persists anything.

**Tech stack:** Python 3.10-3.14, FastAPI, Pydantic v2, httpx, OpenAI Python SDK, SQLAlchemy async, MinIO, pytest, respx, Ruff, mypy.

## File Map

### Add

- `backend/app/services/parser/contracts.py`
- `backend/app/services/parser/errors.py`
- `backend/app/services/parser/result_archive.py`
- `backend/app/services/llm/errors.py`
- `backend/app/services/llm/structured_output.py`
- `backend/tests/contracts/mineru/official-v4/`
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

- [x] Add settings tests for official mode, protocol/model version, language, polling, task deadline, exact asset hosts, result limits, and structured-output mode.
- [x] Replace the self-hosted protocol-2 baseline with the official MinerU cloud API v4 contract.
- [x] Record sanitized upload-request, pending, completed, failed, and malformed response shapes for `file-urls/batch` and `extract-results/batch/{batch_id}`.
- [x] Keep runtime evidence separate and labeled with API/model version and date.
- [x] Keep `.firecrawl/` ignored and fetched documentation untracked.
- [x] Run focused config/fixture tests, Ruff, and mypy.
- [x] Commit the earlier contract/configuration baseline independently; include the official-v4 replacement in this follow-up commit.

## Task 2: Introduce typed MinerU models and errors

**Files:** parser contracts/errors, MinerU client, unit tests.

- [x] Add tests for valid/invalid upload and batch-result payloads with `extra=forbid`.
- [x] Add tests for inconsistent `batch_id`/`data_id`, unknown states, progress invariants, and bounded identifiers.
- [x] Define typed upload, progress, extract-result, batch-result, parse-result, and error models.
- [x] Split unavailable, contract-invalid, and task/result failure errors without provider body leakage.
- [x] Preserve cancellation and exception chaining.
- [x] Run focused parser contract tests, Ruff, and mypy.
- [x] Include the typed official-v4 boundary in this scoped follow-up commit.

## Task 3: Implement signed upload and batch polling

**Files:** MinerU client/config/tests.

- [x] Add tests for exact `POST /api/v4/file-urls/batch`, signed `PUT`, and `GET /api/v4/extract-results/batch/{batch_id}` calls.
- [x] Prove API and blob traffic use separate clients, redirects stay disabled, bearer tokens never reach asset hosts, and hosts are exact allowlist matches.
- [x] Add waiting/pending/running/converting/done/failed polling tests with injected sleep.
- [x] Add deadline, connection, timeout, 4xx/5xx, malformed JSON, oversized download, and cancellation tests.
- [x] Implement official API v4 signed upload, identity-checked polling, bounded ZIP download, and temporary-file cleanup.
- [x] Emit metadata-only errors without provider bodies, signed queries, bearer tokens, or local paths.
- [x] Run focused tests, Ruff, and mypy.
- [x] Include the official-v4 client in this scoped follow-up commit.

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
- [x] Prove provider bodies, URLs containing credentials, paths, prompt text, completion text, and PII never appear in responses or structured logs.
- [x] Prove upload-with-JD failures leave no candidate, score, audit, object, or temporary file.
- [x] Prove re-score failures preserve the candidate and prior scores while creating no partial new score/audit.
- [x] Map only typed boundary exceptions; remove broad route exception translation.
- [x] Run strict candidate API, pipeline, task-ingest, and P2 E2E integration tests.
- [x] Commit application error and transaction behavior.

Local strict verification on 2026-07-16 passed 179 offline tests and 42 integration
tests with zero skips, followed by Ruff, mypy, migration, PostgreSQL, Redis,
MinIO, Celery, and temporary-file clean-state checks. A later full count is recorded
after the official-v4 replacement gate below; WP2 remains In progress pending hosted CI.

The configured new-api deployment was verified separately on 2026-07-16 using
`json_schema`: model listing plus primary/fallback extraction and judge probes passed
5/5 with zero skips for `gpt-5.6-sol`. The adapter was subsequently changed to the
official cloud API v4 and verified with all four synthetic input formats.

## Task 9: Add external runtime contract gates

**Files:** external tests, fixtures, verification script, documentation.

- [x] Add synthetic, non-PII PDF, DOCX, PNG, and JPEG fixtures suitable for the real parser.
- [x] Add a command that requires external endpoints/credentials and fails on missing configuration or skipped `external_contract` tests.
- [x] Capture sanitized official-v4 flow evidence for signed upload, polling, result download, API version, and model version without retaining signed URLs or provider bodies.
- [x] Verify all four supported input formats produce non-empty validated Markdown.
- [x] Capture the deployed new-api model list reduced to configured model IDs.
- [x] Probe configured primary/fallback extraction and judge models for the selected structured-output mode.
- [x] Record the four-format runtime count, endpoint environment, API/model versions, artifact inventory, and secret-free evidence policy.
- [x] Record the final combined MinerU/new-api count after the last external gate: 9 passed, 0 failed, 0 skipped in 386.38 seconds on Windows/Python 3.14.
- [x] Review and stage only sanitized runtime evidence; no batch IDs, signed URLs, credentials, provider bodies, prompts, completions, or PII are retained.

## Task 10: Full verification, rollout documentation, and WP2 exit review

**Files:** README, plan index, roadmap, research docs, this plan.

- [ ] Update quick start and environment documentation for MinerU protocol/configuration and structured-output modes.
- [ ] Document stable parser/AI error codes, runtime verification, rollback, and synthetic fixture policy.
- [x] Run the complete local matrix:

  ```bash
  uv sync --extra dev --locked
  uv run pytest -m "not integration and not external_contract" -q
  uv run ruff check backend
  uv run mypy --explicit-package-bases backend/app --ignore-missing-imports
  uv run python scripts/verify.py
  uv run python scripts/verify_external_contracts.py
  ```

  Final local evidence on Windows/Python 3.14: 192 offline tests and 42 strict
  integration tests passed with zero failures; the external gate passed 9/9 with
  zero skips in 386.38 seconds. Ruff, mypy, Alembic, PostgreSQL, Redis, MinIO,
  temporary-file cleanup, and clean-state assertions passed.

- [ ] Push scoped commits and confirm hosted Python 3.10, Python 3.14, and strict integration jobs.
- [ ] Record exact commits, test counts, service/model versions, contract artifacts, cleanup evidence, and run URLs.
- [ ] Mark WP2 Complete and WP3 Ready for planning only after every offline and external exit criterion passes.
- [ ] Commit documentation and completion evidence.

## Required Exit Evidence

- Approved WP2 specification.
- Exact scoped commit range.
- MinerU endpoint environment, official API version, and model version.
- Sanitized upload-request, batch-result, archive, and four-format runtime evidence inventory.
- Configured new-api model IDs and structured-output capability results.
- Offline unit/integration counts with zero failures and strict integration zero skips.
- External-contract count with zero failures and zero skips.
- Four-format MinerU parse/extraction results using synthetic fixtures.
- Invalid extraction/judge rollback and evidence-provenance results.
- Stable HTTP error matrix.
- Ruff, mypy, migration/clean-state, and hosted Actions evidence.
- Clean tracked working tree excluding pre-existing local artifacts.
