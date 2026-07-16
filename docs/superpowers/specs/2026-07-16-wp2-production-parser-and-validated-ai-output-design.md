# WP2 Production Parser Contract and Validated AI Output Design

**Date:** 2026-07-16

**Status:** Approved on 2026-07-16

**Work package:** WP2

**Depends on:** WP1 complete

> **Official API v4 amendment (2026-07-16):** The user selected the hosted official
> MinerU API. The v4 `file-urls/batch` signed-upload and
> `extract-results/batch/{batch_id}` polling contract supersedes every self-hosted
> MinerU 3.4.4/protocol-2 `/health` and `/tasks` reference below. The archive safety,
> typed-error, validation, transaction, and LLM requirements remain unchanged.

## 1. Purpose

WP2 replaces two trusted-but-unverified boundaries in the current ingestion path:

1. `MinerUClient` previously assumed a synchronous `/file_parse` response; it now uses the official API v4 signed-upload, batch-poll, and result-ZIP flow.
2. Resume extraction and LLM judging accept JSON-shaped dictionaries without enforcing the active rule schema, allowed scores, evidence provenance, or complete dimension coverage.

After WP2, the application can submit a resume through the documented MinerU task protocol, validate the downloaded result artifact, produce a typed extracted resume, and persist a score only after every judge dimension and evidence quote passes deterministic validation.

WP2 keeps the existing synchronous application workflow: the request may wait while the adapter submits and polls MinerU. Moving long-running work behind durable job state and Celery retries belongs to WP3 after these contracts are stable.

## 2. Verified Baseline and Current Gaps

### 2.1 MinerU baseline (superseded historical design)

The implemented baseline is the [official MinerU API v4](https://mineru.net/doc/docs/index_en/):

| Step | Contract |
|---|---|
| Request upload URLs | `POST /api/v4/file-urls/batch` with bearer authentication and file names/data IDs |
| Upload each source | `PUT` raw bytes to the returned signed URL, with no API bearer header |
| Poll batch | `GET /api/v4/extract-results/batch/{batch_id}` until `done` or `failed` |
| Fetch artifacts | Download `full_zip_url` through an isolated client after exact-host and ZIP-path validation |

API calls are locked to `https://mineru.net`; upload and result hosts are independently
allowlisted, redirects and environment proxies are disabled, and signed URLs are never logged.
The following protocol-2 material is retained only as the design history that this amendment replaces.

The design baseline is the official MinerU `3.4.4` release published on 2026-07-10 and API protocol version `2`:

- [official release](https://github.com/opendatalab/MinerU/releases/tag/mineru-3.4.4-released)
- [official FastAPI implementation](https://github.com/opendatalab/MinerU/blob/master/mineru/cli/fast_api.py)
- [official API client](https://github.com/opendatalab/MinerU/blob/master/mineru/cli/api_client.py)
- [official protocol constant](https://github.com/opendatalab/MinerU/blob/master/mineru/cli/api_protocol.py)

The verified routes are:

| Method | Route | Success contract |
|---|---|---|
| `GET` | `/health` | `200` with `status=healthy`, service `version`, and `protocol_version=2` |
| `POST` | `/tasks` | multipart request; `202` with `task_id`, `status_url`, `result_url`, and optional `queued_ahead` |
| `GET` | `/tasks/{task_id}` | `200`; status is `pending`, `processing`, `completed`, or `failed` |
| `GET` | `/tasks/{task_id}/result` | `200 application/zip` when complete, `202` while pending, `409` when failed |
| `POST` | `/file_parse` | synchronous compatibility route; not used by the production adapter |

The repository's previous `3.1.11` research and `/file_parse` assumption are historical context, not the WP2 implementation contract.

### 2.2 new-api baseline

The configured gateway uses the OpenAI-compatible Chat Completions route. Official new-api documentation confirms:

- `POST /v1/chat/completions` with Bearer authentication.
- `response_format.type=json_schema` with a named schema and optional `strict=true`.
- `json_object` is a legacy compatibility mode and still requires local validation.
- Model IDs are deployment-specific and must be discovered from the configured gateway rather than inferred from public model names.

Sources:

- [Chat Completions API](https://doc.newapi.pro/en/api/openai-chat/)
- [available model list](https://doc.newapi.pro/en/api/get-available-models-list/)
- [official repository](https://github.com/QuantumNous/new-api)

### 2.3 Repository gaps

- MinerU calls `/file_parse` and accepts several guessed JSON shapes.
- No health or API protocol compatibility check exists.
- Result downloads have no size, ZIP signature, member-count, path traversal, symlink, or decompression-ratio guard.
- Extraction uses dataclasses and indirect dictionary access rather than strict Pydantic validation.
- LLM fallback catches every exception, including authentication and invalid-request failures.
- Judge output permits unknown, missing, or duplicate dimension IDs and arbitrary tiers/scores.
- Evidence quotes are not checked against normalized source Markdown.
- The pipeline sums untrusted `score` fields before validation.
- Upload and re-score routes do not expose stable AI failure codes.
- Local secrets stay in ignored `.env.local`; sanitized MinerU and new-api runtime evidence is committed without credentials or provider bodies.

## 3. Goals

- Implement official MinerU API v4 signed upload, batch polling, and result-download behavior with typed responses and typed errors.
- Use the asynchronous batch protocol even while the surrounding application workflow remains synchronous.
- Safely consume a bounded result ZIP and require exactly one non-empty Markdown result for a single-file request.
- Capture sanitized MinerU upload-request, batch-result, and four-format runtime evidence before WP2 completion.
- Use strict Pydantic models for extraction and judge responses with `extra=forbid`.
- Verify judge dimension identity, uniqueness, completeness, tier, score, confidence, and evidence provenance against the active rule version and parsed Markdown.
- Ensure invalid AI output cannot create a candidate or score row.
- Use typed gateway failures and retry/fallback only for explicitly retryable conditions.
- Emit stable HTTP error codes without exposing provider bodies, API keys, resume text, or low-level exception messages.
- Add deterministic offline contract tests and explicit runtime smoke gates for the deployed MinerU and new-api services.

## 4. Non-goals

- Durable ingestion job state, worker retry scheduling, batch status, or dead-letter handling; these belong to WP3.
- Candidate read APIs or UI-facing score details; these belong to WP4.
- Cost ledger, budget enforcement, or calibration reports; these belong to WP7.
- Installing MinerU model dependencies inside the FastAPI or Celery image.
- Supporting PPTX or XLSX uploads; WP1's public upload boundary remains PDF, DOCX, PNG, and JPEG.
- Automatically switching response-format modes after a provider rejects configuration. Capability selection is explicit deployment configuration.
- Proving extraction F1 or business scoring accuracy; WP6 and WP7 own labeled quality measurement.

## 5. MinerU Adapter Contract

### 5.1 Health and protocol negotiation

Before submitting a task, the adapter calls `/health` and validates a typed payload. It rejects:

- non-`200` responses;
- non-JSON or structurally invalid payloads;
- any status other than `healthy`;
- any protocol version other than configured `MINERU_EXPECTED_PROTOCOL_VERSION`, default `2`.

The service version is recorded in `ParseResult` and structured logs. A version different from the tested release is allowed only when the protocol matches; runtime contract tests must then be rerun and the evidence updated.

### 5.2 Task submission

The adapter sends one file as repeated multipart field `files` plus explicit form fields matching the official client:

- language list, backend, effort, and parse method;
- formula, table, and image-analysis flags;
- `return_md=true`;
- `return_content_list=true`;
- `return_middle_json=false`;
- `return_model_output=false`;
- `return_images=false`;
- `response_format_zip=true`;
- `return_original_file=false`;
- start and end page IDs.

`202` is the only accepted submission status. The typed response requires a bounded `task_id`. Returned URLs are treated as informational only: the client constructs status and result URLs from the configured base URL plus the validated task ID, preventing a compromised service response from redirecting requests to another origin.

### 5.3 Polling

- Poll only `pending` and `processing` states.
- Stop on `completed`.
- Treat `failed`, an unknown state, malformed JSON, or an inconsistent task ID as a protocol/task failure.
- Enforce configurable poll interval and total task deadline.
- Honor cancellation immediately; do not convert cancellation to a retryable provider error.
- Log task ID, state, queued-ahead count, elapsed time, service version, and trace ID, but never filenames or resume content.

### 5.4 Result download and parsing

The result request must return `200`, `Content-Type: application/zip`, and ZIP magic bytes. The adapter streams to a temporary file while enforcing a configured compressed-byte limit.

ZIP validation rejects:

- absolute paths, drive-qualified paths, `..` traversal, NULs, or backslash traversal;
- symlink or special-file members;
- excessive member count;
- excessive total uncompressed bytes or decompression ratio;
- duplicate normalized member names;
- encrypted members;
- zero or multiple Markdown files for a one-file task;
- empty Markdown;
- malformed content-list JSON when present.

The adapter reads only the required Markdown and optional content-list artifact. It does not extract the archive wholesale and always deletes temporary result files in `finally`.

### 5.5 Parse result

`ParseResult` is a frozen typed object containing:

- `markdown`;
- optional typed `content_list` data;
- `task_id`;
- `backend`;
- MinerU service and protocol versions;
- parse duration and artifact byte counts.

The previous generic `layout` dictionary is removed unless a captured runtime artifact demonstrates a stable field the application actually consumes.

## 6. MinerU Errors and HTTP Mapping

| Typed condition | HTTP status | Stable code |
|---|---:|---|
| connection, health, timeout, or service unavailable | 503 | `resume_parser_unavailable` |
| protocol mismatch or malformed provider contract | 502 | `resume_parser_contract_invalid` |
| terminal task failure or invalid result artifact | 502 | `resume_parser_failed` |

Provider response bodies, result contents, local paths, and authorization headers are never returned or logged. Exception chaining retains the diagnostic cause for controlled logs.

## 7. LLM Gateway Contract

### 7.1 Message and structured-output shape

Trusted instructions are sent as a system message. Resume Markdown and judge dimensions are JSON-encoded as untrusted user data rather than interpolated into pseudo-XML delimiters. The full source Markdown remains unchanged for deterministic evidence validation.

The gateway uses the configured structured-output mode:

- `json_schema`: named schema, `strict=true`, and a Pydantic-generated JSON Schema;
- `json_object`: compatibility mode only, still followed by identical local Pydantic/contextual validation.

Every prompt has a version constant such as `resume_extract_v1` or `resume_judge_v1`. The version, actual provider model, and token counts are stored with the extracted/judge metadata.

### 7.2 Typed failures and fallback

Gateway errors distinguish:

- retryable connection, timeout, rate-limit, and provider 5xx failures;
- non-retryable authentication, authorization, invalid-request, and unsupported-format failures;
- empty completion, missing choice, or malformed SDK response;
- locally invalid structured output.

Fallback is allowed only for retryable provider failure or invalid structured output. It is not used for 400-class configuration/authentication errors. A logical extraction or judge stage makes at most two model attempts: primary, then configured fallback. Logs record logical operation, attempt, model, outcome, latency, token counts when available, and trace ID without prompt or completion bodies.

## 8. Typed Extraction Contract

`Experience` and `ExtractedResume` become Pydantic models with strict types, bounded collections/strings, whitespace normalization, and `extra=forbid`.

Rules:

- top-level object requires all declared fields; nullable fields use explicit `null`;
- `age` is either null or an integer from 0 through 120; booleans are rejected;
- empty optional strings normalize to null;
- experience company, title, and description are non-empty after trimming;
- dates are null or canonical `YYYY`, `YYYY-MM`, or `YYYY-MM-DD` strings; current employment uses a null end date;
- duplicate or excessive experience entries are rejected rather than truncated silently;
- token/model/prompt metadata comes from the gateway and cannot be supplied by the model payload.

The stored `Candidate.extracted_json` keeps the existing top-level business keys for rule-engine compatibility and adds a reserved `_meta` object containing schema version, prompt version, model, and token counts.

## 9. Typed Judge and Contextual Validation

The raw response first passes a strict Pydantic model. A second validator compares it with the active `JudgeDimension` list and normalized source Markdown.

For every request:

- result IDs must exactly equal requested dimension IDs;
- every requested ID appears exactly once; unknown, duplicate, or missing IDs are invalid;
- results are reordered to rule-definition order before persistence;
- tier must exist in the active dimension;
- score must equal that tier's configured score; no clamping or coercion is allowed;
- `unknown` requires `score=null` and no evidence quotes;
- non-unknown tiers require at least one non-empty evidence quote;
- confidence is finite and between 0 and 1;
- reasoning is non-empty and bounded;
- suggested interview questions are bounded strings and a bounded list;
- extra fields are invalid.

Evidence verification applies Unicode NFKC normalization, line-ending normalization, and whitespace collapsing to both quote and source. Every normalized quote must be a literal substring of normalized parsed Markdown. The original validated quote remains stored for display.

Only validated dimension objects are summed. The scoring pipeline recomputes the judge subtotal and total from the active rule version; it never trusts a model-supplied total.

## 10. Persistence and Transaction Behavior

- Extraction failure occurs before candidate insertion, so no candidate or score is committed.
- Judge failure inside upload-with-JD rolls back candidate, score, and audit rows; WP1 compensation deletes the newly stored object.
- Re-score failure rolls back the new score and audit rows without altering the existing candidate or prior scores.
- `judge_dimensions` stores only validated output plus gateway metadata.
- `llm_model_main`, `llm_model_extract`, and token fields are populated from trusted gateway metadata where the current schema supports them.
- No database migration is required unless implementation proves the current JSON/score fields cannot retain required metadata without ambiguity; any such migration requires a design amendment before implementation.

## 11. Runtime Configuration

New or clarified settings:

- `MINERU_BASE_URL`
- `MINERU_EXPECTED_PROTOCOL_VERSION=2`
- `MINERU_BACKEND`
- `MINERU_EFFORT`
- `MINERU_LANGUAGE=ch`
- `MINERU_PARSE_METHOD=auto`
- `MINERU_POLL_INTERVAL_SECONDS`
- `MINERU_TASK_TIMEOUT_SECONDS`
- `MINERU_RESULT_MAX_BYTES`
- `MINERU_RESULT_MAX_UNCOMPRESSED_BYTES`
- `MINERU_RESULT_MAX_MEMBERS`
- `LLM_STRUCTURED_OUTPUT_MODE=json_schema|json_object`
- existing extraction/judge primary and fallback model IDs, verified against the deployed gateway.

Production startup or a dedicated readiness command must reject placeholder URLs, stub MinerU mode, unsupported protocol versions, missing model IDs, and unverified structured-output configuration.

## 12. Testing and Contract Evidence

### 12.1 Offline deterministic tests

- Official-source-derived health, submit, poll, failed-task, and result fixtures.
- Multipart field and URL construction assertions.
- Poll deadline, cancellation, retryable transport, and unknown-state cases.
- ZIP path traversal, symlink, encryption, size, ratio, duplicate member, missing/duplicate Markdown, and malformed JSON cases.
- Pydantic extraction rejection for wrong types, booleans-as-integers, extra keys, empty required strings, invalid dates, and excessive lists.
- Judge rejection for unknown/duplicate/missing IDs, invalid tiers, mismatched scores, NaN/infinity, invalid confidence, fabricated evidence, missing evidence, and extra keys.
- Prompt/message tests proving correct Chinese UTF-8 text, JSON-encoded untrusted resume content, and no resume text in logs.
- Pipeline/API tests proving invalid output creates no candidate/score/audit row and maps to stable 502/503 errors.

### 12.2 Captured runtime evidence

Before completion, use the configured services to capture sanitized, versioned fixtures:

- MinerU `/health` and `/openapi.json`;
- task submission, pending, completed, failed, and result responses;
- bounded result artifacts for synthetic PDF, DOCX, PNG, and JPEG resumes;
- new-api model list with only relevant model IDs retained;
- one valid strict structured-output response per configured primary/fallback model;
- representative rate-limit, authentication, unsupported-format, and malformed-output behavior where safely reproducible.

No real candidate file, PII, API key, authorization header, or full provider error body may enter Git.

Runtime tests use a dedicated `external_contract` marker and fail rather than skip when the WP2 external verification command is invoked. Default CI remains offline and deterministic; completion evidence must include the separate external run location, service versions, model IDs, and exact counts.

## 13. Rollout and Rollback

Rollout order:

1. Configure the official MinerU API v4 endpoint and capture sanitized request/result artifacts.
2. Verify new-api model IDs and structured-output support.
3. Run offline and external contract suites.
4. Enable the new adapter in a non-production environment with synthetic resumes.
5. Promote only after PDF, DOCX, PNG, and JPEG paths and malformed-output rollback pass.

Rollback sets the application back to the previous image. The old synchronous `/file_parse` implementation is not retained as an automatic fallback because it has an unverified response contract. `MINERU_MODE=stub` remains test-only and cannot be used as a production rollback mode.

## 14. Exit Criteria

WP2 is complete only when:

- the specification is approved and has no unresolved implementation ambiguity;
- official MinerU API v4 offline contract tests pass;
- sanitized upload/batch/result artifacts are captured and the four supported input formats pass against the official endpoint;
- configured new-api model IDs and structured-output modes are recorded from the actual gateway;
- invalid extraction or judge output cannot create a candidate or score;
- every persisted judge result has valid dimension/tier/score relationships and source-backed evidence;
- stable parser/AI HTTP error mappings are tested;
- unit, integration, external-contract, Ruff, mypy, migration, cleanup, and hosted CI gates pass as applicable;
- exact commits, test counts, service/model versions, artifact inventory, and run URLs are recorded;
- WP3 is changed from Blocked to Ready for planning only after every gate passes.

## 15. Approval

Approval of this specification means implementation may proceed offline immediately, while WP2 completion remains blocked until real MinerU and new-api endpoints are supplied and their sanitized runtime evidence passes the external-contract gate.
