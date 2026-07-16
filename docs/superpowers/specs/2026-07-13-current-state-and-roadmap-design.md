# SmartScreenAgent Current State and Delivery Roadmap

**Date:** 2026-07-13  
**Status:** Authoritative current-state specification  
**Supersedes for status and sequencing:** the phase/status sections of the 2026-05-12 product design, the P1/P2 implementation plans, and the P2 hardening plan  
**Does not supersede:** historical rationale, research notes, or already accepted feature contracts unless this document explicitly records a change

## 1. Purpose

This document reconciles the implemented repository with the historical product design and plans. It has four responsibilities:

1. Describe what the code actually does today.
2. Record where specifications, plans, implementation, and tests have drifted.
3. Define the production-quality target architecture and missing contracts.
4. Order the remaining work by technical dependency and delivery risk.

The repository must use this document as the source of truth for current status and delivery order. Historical documents remain decision records; unchecked boxes in those plans are not evidence that work is incomplete.

## 2. Document Authority

When documents disagree, use this order:

1. Current code and executable tests define observed behavior.
2. This document defines current scope, target behavior, and delivery order.
3. Feature-specific specifications approved after this document define their feature contracts.
4. Historical product designs and implementation plans explain intent and prior decisions.
5. Research notes provide background but do not define an implemented contract.

Every new feature specification must state which roadmap work package it belongs to and which existing contract it changes.

## 3. Evidence Baseline

The review used the `main` branch at commit `639ef90` and the repository state observed on 2026-07-13.

Verification results:

- `uv run pytest -m "not integration" -q`: 66 passed, 16 deselected.
- `uv run ruff check backend`: passed.
- `uv run mypy --explicit-package-bases backend/app --ignore-missing-imports`: passed for 54 source files.
- Non-integration coverage: 76% overall.
- `backend/app/scoring/pipeline.py`: 37% non-integration coverage.
- `backend/app/deps.py`: 0% non-integration coverage.
- `uv run pytest -m integration -q`: 16 skipped because PostgreSQL, Redis, and MinIO were not running.

These results establish a healthy unit-level baseline. They do not prove that database migrations, real object storage, the real MinerU service, the real LLM gateway, or the full HTTP workflow operate correctly in a deployed environment.

### 3.1 WP0 verification evidence (2026-07-13)

On branch `codex/wp0-integration-baseline`, the locked local verification path produced:

- `uv sync --extra dev --locked` and `uv run python scripts/verify.py` passed: 102 non-integration tests and 16 integration tests completed with zero skips.
- Ruff, mypy, the real migration round trip, MinIO, Redis/Celery, and post-run clean-state checks passed.

Exact local environment, timing, cleanup, and commit-range evidence is recorded in the [WP0 plan](../plans/2026-07-13-wp0-integration-baseline.md#completion-evidence). Hosted [GitHub Actions run 29237545679](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29237545679) passed for Python 3.10, Python 3.14, and the full integration job at commit `b3447ec`. WP0 is complete; WP1 is ready for specification and implementation planning.

### 3.2 WP1 local verification evidence (2026-07-16)

The approved WP1 implementation passed the locked local verification path:

- 142 non-integration tests and 36 integration tests completed with zero skips.
- Alembic upgraded through revision `b57c2f9e1a6d`; empty-database and previous-revision round trips passed.
- Real PostgreSQL, Redis/Celery worker, MinIO privacy/presigned access, upload persistence, duplicate cleanup, and failure compensation tests passed.
- Ruff passed; mypy passed for 59 application source files.
- Post-run checks found no migration databases, application rows, Redis/Celery keys, MinIO objects, or temporary resume files.

WP1 is **Complete**. The implementation is committed, hosted [GitHub Actions run 29474031067](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29474031067) passed Python 3.10, Python 3.14, and strict integration, and the configured deployment database upgraded to `b57c2f9e1a6d` with zero candidate and legacy rows requiring disposition. WP2 is **Ready for planning**.

## 4. Implemented System

### 4.1 Runtime components

The repository currently contains:

- A FastAPI application with access logging, trace IDs, CORS, and health checks.
- PostgreSQL access through async SQLAlchemy and Alembic.
- Redis/Celery configuration and a `parse_and_score` task entry point.
- A MinIO client and MinIO health check.
- DingTalk OAuth exchange and local user upsert.
- JWT creation and current-user lookup.
- An OpenAI-compatible LLM gateway with retry and model fallback.
- MinerU stub and HTTP parsing modes.
- Resume extraction, PII encryption, and PII deduplication hashing.
- Excel import for six job families.
- Hard filters, deterministic rule methods, LLM judging, grading, score persistence, and audit rows.
- Candidate upload and candidate re-score HTTP endpoints.
- JWT/RBAC enforcement for candidate writes.
- Streamed upload validation and verified private MinIO resume persistence.
- A Typer CLI for importing rules.

### 4.2 Exposed HTTP surface

The implemented application exposes only:

| Method | Route | Current protection | Purpose |
|---|---|---|---|
| GET | `/` | Public | Service identity |
| GET | `/healthz` | Public | Dependency health |
| POST | `/auth/dingtalk/login` | Public | Exchange DingTalk auth code for JWT |
| POST | `/api/v1/candidates/upload` | Bearer JWT: `hr`, `hr_lead`, `admin` | Persist, parse, extract, and optionally score |
| POST | `/api/v1/candidates/{candidate_id}/score` | Bearer JWT: `hr`, `hr_lead`, `admin` | Re-score a candidate for a JD |

There are no list, detail, rule-management, feedback, report, audit-export, settings, batch-job, or MCP endpoints.

### 4.3 Implemented scoring flow

The current synchronous flow is:

```text
HTTP upload
  -> authorize database user role
  -> bounded stream, hash, and file validation
  -> verified private MinIO object
  -> MinerU parse
  -> LLM structured extraction
  -> Candidate insert or explicit duplicate resolution by pii_hash
  -> optional ScoringPipeline
       -> hard filters
       -> deterministic rules
       -> LLM judge
       -> Score and AuditLog flush
  -> one application-service database commit
  -> temporary file deletion
```

The Celery task calls the same orchestration function, but the HTTP upload endpoint does not enqueue it.

## 5. Traceability Matrix

Status values:

- **Implemented:** behavior exists and has relevant tests.
- **Partial:** a model, client, or happy path exists, but the end-to-end contract is incomplete.
- **Designed:** an approved design exists, but code does not implement it.
- **Absent:** no meaningful implementation exists.
- **Defective:** implementation contradicts an accepted requirement or loses required data.

| Capability | Historical intent | Code | Test evidence | Status |
|---|---|---|---|---|
| FastAPI service and health | P1 foundation | Implemented | Unit/integration tests exist | Implemented |
| PostgreSQL schema and migrations | P1 foundation | Nine business tables, baseline migration, and WP1 raw-file metadata revision | Strict WP1 verification passed empty and previous-revision round trips | Implemented |
| PII field encryption and dedupe | Product design section 11 | Fernet fields and SHA-256 dedupe | Unit tests pass | Implemented |
| MinIO private resume storage | Product design section 11.1 | New uploads persist verified immutable private objects with checksum/size metadata | Real MinIO privacy, persistence, duplicate, and compensation tests pass; legacy rows require rollout reconciliation | Implemented |
| DingTalk login | P1 foundation | OAuth exchange, user upsert, JWT issue | OAuth/JWT unit tests | Partial |
| JWT/RBAC on write APIs | Product design section 11.3 and WP1 design | Candidate routes require database-authoritative `hr`, `hr_lead`, or `admin` | Unit and real HTTP/PostgreSQL authorization matrix passes | Implemented |
| Excel rule import | P2 scoring plan | Six sheets, two layouts, CLI persistence | Unit and integration tests | Implemented |
| MinerU integration | P2 scoring plan | Stub and assumed synchronous `/file_parse` HTTP contract | HTTP mocks only | Partial |
| Resume extraction | P2 scoring plan | LLM JSON extraction with one retry | Unit tests with mock gateway | Partial |
| Three-stage scoring | P2 scoring plan | Hard filter, deterministic rules, LLM judge | Unit and DB integration tests | Implemented |
| Evidence-backed scoring | Product design core value | Evidence fields are requested but not validated against source text | Mocked judge tests | Partial |
| Candidate upload API | P2 scoring plan | Authenticated synchronous endpoint with validation, private storage, compensation, and explicit duplicates | Strict DB/MinIO-backed integration passes; production parser and AI contracts remain incomplete | Partial |
| Candidate query API | P2 Task 14 title promised upload, score, and query | No query route | None | Absent |
| Batch upload and async status | Original W5 | Celery entry point only | Task orchestration test exists | Partial |
| Candidate list and scorecard | Original W3-W5 | No API or UI | None | Absent |
| Rule editor and version workflow | Original W3-W4 | Import-only CLI; active version pointer exists | Import tests | Partial |
| Feedback review loop | Product design section 7 | Table only | Model test only | Absent |
| Golden-set calibration | Original W7 | Tables/columns only | Model test only | Absent |
| What-If simulation | Original W7 | No implementation | None | Absent |
| Cross-engine scoring | Original W7 | Columns exist; always `None`/`False` | None | Absent |
| Cost budgets and enforcement | Product design section 9.3 | Settings exist; no accounting or guard | None | Absent |
| DingTalk recruitment sync | Original W6 | No implementation | None | Absent |
| MCP/Hermes integration | Original W6 | No implementation | None | Absent |
| Batch and consistency reports | Original W5/W7 | No implementation | None | Absent |
| JD health and rule drafting | Original W8 | No implementation | None | Absent |
| Web application | Original product design section 10 | No frontend project | None | Absent |

## 6. Confirmed Drift

### D-01: Phase names no longer describe the same scope

The original eight-week MVP uses weeks and product outcomes. Later documents redefine P1 as backend foundation, P2 as scoring, and P3 as nearly all remaining product work. This hides missing user-facing outcomes behind completed engineering phases.

**Resolution:** stop using P1/P2/P3 as current delivery status. Use the work packages in section 10.

### D-02: Historical plan checkboxes are not execution records

The P1 plan contains 134 unchecked items, the P2 plan 73, and the hardening plan 33, although Git history shows most planned code was committed. Marking hundreds of historical steps after the fact would fabricate execution detail.

**Resolution:** freeze historical plans and add status banners. Track new plans through checkboxes while executing them.

### D-03: MinIO architecture is not connected to ingestion

`run_parse_and_score` stores the temporary local path as `Candidate.raw_file_key`. The upload endpoint schedules that file for deletion. The resulting database value does not identify a persistent object.

**Resolution:** object persistence must precede parsing. Store an immutable object key, not a local path. Define cleanup behavior for failed ingestion and rejected uploads.

**WP1 update:** resolved for every new upload. Legacy rows remain an explicit rollout reconciliation gate.

### D-04: Security design is approved but not enforced

Candidate write endpoints are public. The current-user dependency exposes token decode exception text. There is no role dependency or route-level authorization.

**Resolution:** implement the 2026-07-08 JWT/RBAC design before adding more business APIs. Normalize authentication errors and test the real dependency chain.

**WP1 update:** resolved for candidate upload and re-score routes with database-authoritative roles and stable 401/403 responses.

### D-05: MinerU research and implementation assume different wire contracts

Research describes task submission, polling, and result artifacts, while code assumes a synchronous `/file_parse` response containing Markdown. The mock contract is useful for development but is not a production integration contract.

**Resolution:** capture the deployed MinerU OpenAPI document and sample result artifacts. Implement an adapter against that evidence before batch ingestion.

### D-06: The asynchronous architecture is not the active path

The product design assigns parsing and scoring to workers, but the upload API performs both synchronously. This couples request latency to two external AI services and prevents durable retries and progress reporting.

**Resolution:** introduce an ingestion job state machine after the parser contract is fixed. The API stores the file and creates a job; workers parse, extract, score, and update state.

### D-07: API scope in the P2 plan overstates implementation

P2 Task 14 is titled "upload / score / query", but only upload and score were implemented.

**Resolution:** treat candidate read APIs as a separate work package with pagination, filtering, authorization, PII audit, and stable response schemas.

### D-08: LLM outputs are trusted too early

Extraction checks JSON parsing and required dictionary keys indirectly. Judge output is parsed as JSON but is not validated against allowed dimension IDs, tier values, score bounds, evidence presence, or duplicate dimensions. Evidence quotes are not verified against the resume text.

**Resolution:** add typed response models and deterministic validation before persistence. Invalid responses must be retryable failures, not accepted scores.

### D-09: Cost and observability requirements are not implemented

Budget settings exist, but cost calculation and enforcement do not. Extraction tokens/model are not persisted in the candidate workflow, score cost remains at its default, and external call latency is not recorded.

**Resolution:** centralize LLM usage records and enforce budgets at the gateway boundary after core ingestion is durable.

### D-10: Acceptance criteria have not been measured

The original design claims 1000 resumes/month, F1 >= 0.75, evidence traceability, DingTalk mobile login, MCP access, and a monthly budget. No benchmark or acceptance report proves these outcomes.

**Resolution:** classify them as release gates, not completed capabilities. Add measurable fixtures and reports in the calibration and production-readiness packages.

## 7. Missing Contracts and Best-Practice Corrections

### 7.1 Upload boundary

The upload contract must define:

- Allowed extensions and MIME types based on both declared metadata and file signature.
- A configurable maximum file size enforced while streaming, not after reading the whole file.
- Empty-file and encrypted/password-protected document behavior.
- Object key format, content type, checksum, size, and original filename metadata.
- Duplicate semantics independent of missing name or phone fields.
- Malware scanning integration point, even if the initial deployment uses a disabled adapter.
- Stable `400`, `413`, `415`, `422`, `502`, and `503` error mappings.

### 7.2 Ingestion state machine

Use explicit states:

```text
uploaded -> queued -> parsing -> extracting -> ready
                                  -> scoring -> completed

Any processing state -> retryable_failed -> queued
Any processing state -> terminal_failed
uploaded/ready/completed -> deleted
```

Every transition must record timestamp, attempt count, last stable error code, and trace ID. A worker retry must be idempotent.

### 7.3 Transaction and idempotency boundaries

- Uploading an object and inserting database metadata cannot be one database transaction. Use compensating deletion if metadata creation fails.
- Worker steps must commit state transitions separately from external calls.
- Scoring must accept an idempotency key based on candidate, JD, rule version, and scoring request identity.
- Duplicate candidate detection must not silently reuse a prior candidate while leaving the new uploaded object orphaned.
- Route handlers must not catch broad exceptions except at a stable protocol boundary.

### 7.4 Authentication and authorization

- Authentication returns stable `401` responses without library exception text.
- Authorization returns `403` and is expressed through reusable FastAPI dependencies.
- Role checks remain at the HTTP boundary; ownership and department filters belong in query services.
- JWT claims do not replace the database role as the current authority.
- PII reads require an audit row containing actor, candidate, purpose, and trace ID.

### 7.5 LLM safety and correctness

- Validate extraction and judgment with Pydantic response models.
- Reject unknown or duplicated dimension IDs.
- Clamp nothing silently; out-of-range scores are invalid responses.
- Require judge tiers to exist in the active rule version.
- Verify each evidence quote is present in normalized source text.
- Store prompt template version, provider model, token counts, latency, and estimated cost.
- Retry only transport failures and explicitly retryable invalid responses.
- Keep resume text delimited and sanitized, but do not treat regex sanitization as the sole prompt-injection defense.

### 7.6 Data protection and lifecycle

- Persist raw files only in private object storage.
- Define whether parsed Markdown is PII-sensitive; current policy treats it as sensitive candidate data.
- Return PII only through authorized detail endpoints.
- Implement retention, deletion, and tombstone workflows before production data is loaded.
- Never log file contents, raw OAuth tokens, JWTs, PII plaintext, or external provider response bodies containing resume text.

### 7.7 Testing and delivery gates

- Unit tests remain offline and deterministic.
- Integration tests must run against disposable PostgreSQL, Redis, and MinIO in CI.
- Contract tests cover MinerU and the LLM gateway using captured schemas and sanitized fixtures.
- End-to-end tests may mock paid external calls but must use real database, object storage, broker, and worker processes.
- A skipped integration suite is not a passing release gate.
- Migrations must be tested both from an empty database and from the previous released revision.

## 8. Target Component Boundaries

The target system keeps framework code thin and separates durable workflow from external adapters.

```text
FastAPI routers
  -> authorization dependencies
  -> application services
       -> candidate query service
       -> ingestion service
       -> scoring service
       -> rule publication service
       -> review/calibration service
  -> repositories / SQLAlchemy

Celery workers
  -> the same application services

External adapters
  -> object storage
  -> MinerU
  -> LLM gateway
  -> DingTalk
  -> MCP transport
```

Rules:

- Routers translate HTTP input/output; they do not own workflow transactions.
- Celery tasks deserialize inputs and call application services; they do not duplicate business logic.
- External adapters expose typed errors that application services map to stable workflow error codes.
- Repositories isolate query composition when authorization, pagination, and filtering become non-trivial.
- Do not create abstractions solely for symmetry; introduce each boundary when its work package needs it.

## 9. Product Scope Corrections

The next usable milestone is an internal HR screening workflow, not the entire original eight-week vision.

The first production candidate must support:

1. Authenticated HR login.
2. Safe single and batch upload.
3. Durable parse/extract/score jobs with status and retry.
4. Candidate list and score detail with evidence.
5. Human review and feedback capture.
6. Rule version visibility and controlled publication.
7. Auditability and basic cost reporting.

The following remain later extensions:

- DingTalk recruitment-document synchronization.
- MCP/Hermes conversational access.
- Cross-engine scoring.
- Advanced batch analytics and consistency reports.
- JD health diagnosis and automatic rule drafting.
- Cross-position recommendations.

## 10. Ordered Work Packages

### WP0: Reproducible integration baseline

**Status:** Complete - local strict verification and hosted GitHub Actions run `29237545679` passed.

**Goal:** make the existing backend verifiable with disposable dependencies.

**Includes:** CI service containers, migration verification, real Redis/MinIO/PostgreSQL tests, deterministic fixtures, and a documented local verification command.

**Depends on:** current repository only.

**Exit gate:** all existing integration tests execute rather than skip in CI; unit, integration, Ruff, and mypy gates pass.

### WP1: Security and raw-file integrity

**Status:** Complete - approved implementation passed the full local gate and hosted [GitHub Actions run 29474031067](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29474031067) on 2026-07-16; the configured deployment database upgraded with zero legacy rows requiring disposition.

**Goal:** close public-write and data-loss risks before expanding the API.

**Includes:** JWT/RBAC, normalized auth errors, streamed file validation, MinIO persistence, immutable object keys, checksums, compensating cleanup, and security tests.

**Depends on:** WP0.

**Exit gate:** unauthorized writes fail; authorized roles pass; every successful upload references an existing private object; failed uploads leave no orphaned object or candidate row.

### WP2: Production parser contract and validated AI output

**Status:** Ready for planning.

**Goal:** replace assumed external contracts with verified adapters and typed results.

**Includes:** captured MinerU OpenAPI/artifacts, submission/poll/download adapter as required by the real service, parser contract tests, typed extraction/judge responses, score/evidence validation, and stable error codes.

**Depends on:** WP1 object persistence.

**Exit gate:** representative PDF, DOCX, and image fixtures produce validated Markdown and extraction results through the deployed parser contract; malformed AI output cannot create a score.

### WP3: Durable asynchronous ingestion and batch processing

**Goal:** move long-running work out of HTTP requests and support retries and progress.

**Includes:** ingestion job model and migration, explicit state machine, Celery orchestration, idempotency keys, retry policy, dead-letter/terminal failure handling, batch creation, and status APIs.

**Depends on:** WP2 stable parser and AI contracts.

**Exit gate:** upload responds after durable storage and job creation; workers can restart without duplicate candidates or scores; batch progress is queryable.

### WP4: Read APIs and rule lifecycle

**Goal:** expose the stable backend surface required by an HR client.

**Includes:** paginated candidate list, candidate detail, score detail, evidence payloads, JD list/detail, rule-version list/diff, controlled rule publication, ownership filters, PII audit, and OpenAPI contract tests.

**Depends on:** WP1 authorization and WP3 stable job/candidate states.

**Exit gate:** an API client can complete upload, monitor, list, inspect, and re-score workflows without direct database access.

### WP5: HR web workspace

**Goal:** deliver the first usable internal HR workflow.

**Includes:** DingTalk login handoff, candidate list, filters, batch status, scorecard, evidence display, error/loading states, and authorized PII display.

**Depends on:** WP4 APIs.

**Exit gate:** an HR user can complete the golden path in supported desktop and mobile-width browsers; accessibility and responsive checks pass.

### WP6: Review, golden set, and rule simulation

**Goal:** close the human feedback and rule-governance loop.

**Includes:** review decisions, reasons, golden-set import, three-label workflow, baseline metrics, rule draft/publication states, What-If execution, version diffs, and regression gates.

**Depends on:** WP4 rule APIs and WP5 review UI.

**Exit gate:** a rule cannot be published without recorded regression results; HR feedback is attributable and reportable.

### WP7: Cost, calibration, and operational reporting

**Goal:** make model quality and operating cost measurable and enforceable.

**Includes:** centralized usage ledger, estimated CNY cost, daily/monthly budget guards, alerts, confidence calibration, optional cross-engine scoring, batch rejection analysis, and AI-HR consistency reports.

**Depends on:** WP3 durable jobs and WP6 labeled feedback.

**Exit gate:** budget enforcement is tested; quality reports use a versioned golden set; the release records measured F1, evidence coverage, latency, throughput, and cost.

### WP8: DingTalk recruitment sync and MCP/Hermes

**Goal:** add external acquisition and conversational access without bypassing core authorization or audit rules.

**Includes:** DingTalk recruitment-document sync, cursor/idempotency handling, source metadata, MCP tools backed by WP4 services, Hermes integration, and scoped service identities.

**Depends on:** stable WP4 APIs and WP3 ingestion.

**Exit gate:** repeated synchronization is idempotent; MCP tools enforce the same access and audit policy as REST; failures do not block manual upload.

### WP9: JD intelligence and cross-position recommendation

**Goal:** build advanced recommendations from validated historical data.

**Includes:** JD health diagnosis, rule draft generation, embeddings, and opt-in cross-position matching.

**Depends on:** sufficient WP6 feedback data and WP7 quality measurement.

**Exit gate:** offline evaluation demonstrates defined quality thresholds before recommendations are shown to HR.

## 11. Dependency Graph

```text
WP0 -> WP1 -> WP2 -> WP3 -> WP4 -> WP5 -> WP6 -> WP7
WP3 -> WP8
WP4 -> WP8
WP6 -> WP9
WP7 -> WP9
```

WP4 design may begin while WP3 is being implemented, but its final schemas must use the job states finalized by WP3. WP5 visual design may begin before WP4 is complete, but implementation must not invent backend contracts.

## 12. Definition of Done

A work package is complete only when all applicable conditions hold:

- Its accepted specification has no unresolved placeholders or ambiguous requirements.
- New behavior was developed with failing tests first where deterministic testing is possible.
- Unit and integration tests cover success, authorization, validation, dependency failure, and retry behavior.
- Database migrations have upgrade and downgrade verification appropriate to the change.
- Ruff and mypy pass.
- External contracts are supported by captured official schemas or reproducible runtime evidence.
- API and operational documentation match implemented behavior.
- Security, PII, audit, and cost implications have explicit tests or a recorded non-applicability reason.
- HTTP changes emit structured logs and trace IDs; background workflows expose durable job state; external calls record latency, outcome, and provider identifiers.
- A rollback or disable strategy is documented for production-impacting changes.
- Git history contains scoped commits and the working tree has no uncommitted changes from the package.

## 13. Documentation Maintenance

After this specification is approved:

1. Add a short historical-status banner to the original product design and completed plans.
2. Mark the JWT/RBAC design as approved but not implemented.
3. Add a README documentation index pointing to this specification.
4. Create one implementation plan per work package rather than a single multi-month plan.
5. Update this document's traceability matrix when a work package is completed or materially redesigned.

Implementation plans must not be used as permanent status dashboards. Git commits, tests, and this traceability matrix are the status record.

## 14. Risks and Controls

| Risk | Control |
|---|---|
| Frontend work starts against unstable APIs | WP4 contract gate precedes WP5 implementation |
| Real MinerU behavior invalidates current adapter | WP2 requires runtime contract evidence before async ingestion |
| Retry creates duplicate candidates or scores | WP3 defines idempotency before batch support |
| Authentication is added but ownership remains over-broad | WP4 adds query-scope authorization separately from role checks |
| LLM scores appear explainable but evidence is fabricated | WP2 validates evidence quotes against normalized source text |
| Calibration is built before labels exist | WP6 produces labeled data before WP7 model-quality features |
| DingTalk integration dictates core domain behavior | WP8 calls stable application services and remains optional |
| Historical documents continue to be mistaken for status | Status banners and README index point to this document |

## 15. Acceptance of This Specification

Approval of this document means:

- Historical documents remain available but are not the current status authority.
- Security and data integrity take priority over new UI or integration features.
- Work proceeds through WP0-WP9 dependencies unless a later approved feature specification explicitly changes the order.
- Each work package receives a separate implementation plan and verification gate.
- Original MVP acceptance criteria remain product goals but are not considered achieved until measured.
