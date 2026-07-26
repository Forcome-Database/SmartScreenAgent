# WP8 DingTalk Recruitment Sync and MCP/Hermes Design

**Date:** 2026-07-27

**Status:** Draft

**Work package:** WP8

**Depends on:** WP3 durable ingestion (job state machine, lease/retry/sweeper),
WP4 read APIs and their role gates, WP7 audit and operations reporting

## 1. Purpose

WP8 gives SmartScreen a second way in and a second way out.

**In:** resumes arrive automatically from DingTalk recruitment instead of only
by manual upload, so HR opens the web workspace to work already-scored
candidates rather than to feed the system.

**Out:** a Model Context Protocol server lets a self-hosted Hermes agent answer
questions from a DingTalk group without any path around the authorization and
audit rules the REST API enforces.

The web workspace remains the primary surface. The conversational surface is
deliberately thin in this package.

## 2. Baseline and Gaps

### 2.1 Baseline

- WP3 provides `IngestionJob` with `source`, `source_external_id`, `batch_id`,
  a durable state machine, leases, bounded retries, and a sweeper.
  `IngestionJobService.create_or_reuse` already accepts an external source.
- `ingestion_jobs` carries a partial unique index on `raw_file_sha256`
  restricted to non-terminal states; `candidates.pii_hash` is unique.
- `Candidate.source` already anticipates `"dingtalk"`, `"boss"`, and
  `"zhilian"` values.
- WP4 exposes role-gated read services; WP7 added `score_detail_read` auditing
  for the one read that decrypts evidence.
- `DingTalkOAuthClient` exists for login only. It reads `unionId` into
  `users.dingtalk_userid`.
- The repository has an established pattern for unverified external APIs:
  recorded contract fixtures under `backend/tests/contracts/<provider>/` plus
  live probes marked `external_contract`, excluded from the default CI command.

### 2.2 Gaps

- No resume acquisition path other than manual upload.
- No cursor, no source-level deduplication, no sync reporting.
- No MCP server, so no conversational access at all.
- **The DingTalk recruitment endpoints are unverified.** The original design
  (`docs/specs/2026-05-12-resume-screening-agent-design.md` §8.2) names
  `GET /v1.0/recruitment/candidates` and `GET /v1.0/recruitment/jobs`, but
  unlike its OAuth section it cites no `oas-ref:` for them. A read of the
  `dingtalk-api` OAS on 2026-07-27 found no `recruitment` namespace at all.
  The same design records this as open question #1: who confirms the
  recruitment API scope with the DingTalk administrator. The administrator can
  grant the permission but has not yet.

## 3. Goals

1. Import resumes from DingTalk recruitment into the existing WP3 pipeline,
   with no second pipeline.
2. Make repeated synchronization idempotent, and make it cheap: a repeat must
   not re-download or re-parse, because parsing and extraction cost money.
3. Contain the unverified-endpoint risk inside one adapter file.
4. Isolate failures so one bad item never aborts a run and no sync failure can
   block manual upload.
5. Synchronize JD metadata without ever touching governed rule state.
6. Expose an MCP tool set that cannot reach candidate content, structurally
   rather than by convention.

## 4. Non-goals

- Any DingTalk group message handling. Hermes owns its own DingTalk channel.
- End-user identity passthrough from Hermes into MCP calls (see §8.1).
- Exposing evidence quotes, reasoning, PII, ciphertext, or object keys over MCP.
- Triggering paid scoring from MCP.
- `jd_health_check`, `generate_rule_from_jd`, and `cross_position_match` from
  the original design: these are new capabilities, not wrappers over existing
  services, and belong to WP9.
- Boss/Zhilian adapters. The port makes them possible; this package does not
  build them.

## 5. Architecture and Boundaries

```
DingTalk recruitment API ──┐
                           ├──► ResumeSourceAdapter (port)
future: Boss / Zhilian ────┘              │
                                          ▼
                          SyncRunner (Celery Beat, default off)
                            cursor → list changed → per item fetch
                                          │
                                          ▼
                          ResumeStorageService ──► MinIO
                                          │
                                          ▼
                          IngestionJobService.create_or_reuse
                            source='dingtalk', source_external_id=<id>
                                          │
                                          ▼
                          existing WP3 state machine → web workspace
```

### 5.1 Modules

| Module | Owns | Does not own |
|---|---|---|
| `services/sync/adapter.py` | The `ResumeSourceAdapter` protocol: `list_changed(cursor)` and `fetch(item)` | Persistence, scheduling, retry policy |
| `services/sync/dingtalk.py` | DingTalk endpoints, field mapping, pagination, app-level auth | Idempotency, cursor semantics |
| `services/sync/runner.py` | Cursor read/write, per-item delegation, failure isolation, run report | Knowledge of any specific source |
| `services/sync/replay.py` | Bounded re-drive of `outcome='failed'` ledger rows | Listing; it works only from the ledger |
| `mcp/server.py` | Four tools over existing services | Anything that decrypts or scores |

Two Celery tasks: `sync.pull_dingtalk` (Beat, interval below) and
`sync.replay_failed` (Beat, hourly). Both follow the WP7 wrapper shape — owned
session, commit on success, `engine.dispose()` in `finally`.

`runner.py` imports nothing from `dingtalk.py`. This is the whole point: when
the real endpoints turn out to differ from the documented shape, one file
changes.

### 5.2 Kill switch

`DINGTALK_SYNC_ENABLED` defaults to `false`. When false the Beat schedule does
not register the sync task and no adapter is constructed. Manual upload is
unaffected in every case — this is the exit gate's "failures do not block
manual upload", enforced by construction rather than by care.

## 6. Data Model

```sql
sync_cursors
  source          TEXT PRIMARY KEY        -- 'dingtalk_recruitment'
  cursor_value    TEXT        NOT NULL    -- ISO-8601 UTC instant (see §8.3)
  last_run_at     TIMESTAMPTZ
  updated_at      TIMESTAMPTZ NOT NULL

sync_source_items
  id                 BIGSERIAL PRIMARY KEY
  source             TEXT        NOT NULL
  source_external_id TEXT        NOT NULL
  content_sha256     TEXT        NOT NULL
  ingestion_job_id   BIGINT      REFERENCES ingestion_jobs(id)
  outcome            TEXT        NOT NULL  -- ingested | skipped_duplicate | failed
  error_code         TEXT
  attempts           INTEGER     NOT NULL DEFAULT 0
  first_seen_at      TIMESTAMPTZ NOT NULL
  last_seen_at       TIMESTAMPTZ NOT NULL
  UNIQUE (source, source_external_id, content_sha256)
  CHECK (outcome IN ('ingested','skipped_duplicate','failed'))
  CHECK (attempts >= 0)
```

Neither table stores a candidate name, phone, email, or resume text.
`source_external_id` is the recruitment system's own identifier.

## 7. Idempotency

Three layers, distinct and all required.

**Content level (inherited).** The partial unique index on
`ingestion_jobs.raw_file_sha256` prevents the same bytes being in flight twice;
`candidates.pii_hash` prevents a duplicate person.

**Source level (new).** The inherited index excludes terminal states, so once a
job completes its hash no longer blocks anything. Re-syncing the same candidate
would therefore re-download, re-parse, and re-extract before finally colliding
on `pii_hash` — correct, but it pays MinerU and LLM costs for nothing. The
dedupe key is the triple:

```
(source, source_external_id, content_sha256)
```

The content hash is part of the key on purpose. Keying on
`(source, source_external_id)` alone would permanently ignore a candidate who
uploads a revised resume.

**Cursor level (new).** See §8.

## 8. Cursor Semantics

### 8.1 Overlap is deliberate

A `since=last_sync_at` cursor is unsafe on its own: source timestamps can tie,
clocks can skew, and items can change while we page. Each run therefore queries
from `cursor_value - SYNC_OVERLAP_SECONDS` (default 300).

Overlap and the §7 dedupe ledger are a *pair*. Read alone, each looks
redundant; together they mean "re-see items cheaply rather than miss them".

### 8.2 Advancement

The cursor advances to **the source timestamp of the last successfully
persisted item**, after that item's ingestion job is durable — never to `now`,
and never only at end of page. A crash mid-page therefore resumes from the last
good point, and the items after it are re-listed on the next run.

### 8.3 The cursor is a timestamp

`cursor_value` holds an ISO-8601 UTC instant, and §8.1's overlap is defined in
terms of it. This commits to the documented `since=` parameter shape.

If the live probe (§13.1) reveals an opaque continuation token instead, overlap
is meaningless for it and the design changes: the adapter would own token
persistence and the runner would stop subtracting a window. That is a change to
`sync_cursors` semantics and to §8.1, and it must be made explicitly rather than
by storing a token in a field documented as an instant.

## 9. Failure Isolation

**WP8's failure domain ends when the ingestion job exists.** After that, WP3's
state machine, lease, bounded retries, and sweeper own the work. WP8 adds no
parallel retry machinery for it.

That leaves two classes:

**Run-level (listing failed).** Revoked permission, expired credential,
provider outage. Abort the run, do **not** advance the cursor, write
`resume_sync_failed`. The next Beat tick retries from the same cursor. No
in-task retry loop: on a permission problem it would only spin.

**Item-level (fetch or job creation failed).** Record `outcome='failed'` with an
error code, continue with the next item.

A failed item cannot be rediscovered by the cursor — the cursor has moved past
it and the 300-second overlap will not reach back. Failed ledger rows are
therefore re-driven by a bounded sweeper up to `SYNC_MAX_ITEM_ATTEMPTS`, then
made terminal. This is the same idiom as the WP3 ingestion sweeper and the WP7
cross-check sweeper.

### 9.1 Cost guards

Synchronization itself is cheap, but every job it creates costs a MinerU parse
plus LLM extraction and scoring downstream.

- `SYNC_MAX_ITEMS_PER_RUN` (default 200) bounds a run.
- On hitting the cap the run **must log and audit the dropped count**. Silent
  truncation would read as "sync finished" while thousands remain.
- The §7 ledger is the largest saving: a repeat still downloads, but it never
  parses, extracts, or scores. That is where the money is. The dedupe key
  includes the content hash, which cannot be known before the transfer, so the
  check is necessarily made after the download and deliberately so — a
  pre-download guard could only key on `(source, source_external_id)` plus a
  timestamp, which is precisely the "permanently ignore a revised resume"
  failure §7 rules out.

### 9.2 No transaction across a network call

Each item's database writes are their own short transaction. The DingTalk HTTP
call and the MinIO upload happen outside any business transaction.

This is a WP7 lesson applied in advance: holding a transaction across a
provider call there exhausted the connection pool and forced a refactor. Here
the risk is worse, because a stalled sync holding connections would take manual
upload down with it — a direct violation of the exit gate. Integration tests
assert `not db.in_transaction()` at each external call, the same way WP7 does.

## 10. JD Metadata Synchronization

Permitted: create a JD that does not exist; update `name` and `description`.

Forbidden: `jds.active_rule_version_id`, `rule_versions.schema_json`, and
`jds.status`.

WP6c built a gated publication workflow — draft, What-If, recorded regression,
then publish. A background task able to change the active rule version would be
a back door around it. An integration test asserts `active_rule_version_id` is
unchanged after synchronizing an existing JD.

## 11. MCP Surface

Hermes is self-hosted on the company intranet, connects to DingTalk itself, and
reaches us through an MCP server address in its own configuration. We deliver
only the server.

### 11.1 Identity

Hermes' ability to pass the asking user's DingTalk `unionId` through to a tool
call is not established. Rather than assume it, WP8 exposes **only tools that
need no end-user identity**: nothing they return is user-specific or requires
PII authorization.

If Hermes is later confirmed to pass identity reliably, a follow-up package can
map `unionId` to `users.dingtalk_userid` and widen the surface. That decision is
deferred, not skipped.

### 11.2 Tools

| Tool | Backed by | Returns |
|---|---|---|
| `list_jds()` | WP4 JD list | code, name, active rule version |
| `top_candidates(jd_code, n=10, days=7)` | WP4 ranked list | candidate id, total, grade, scored at |
| `score_summary(score_id)` | Aggregate projection of WP4 score detail | total, grade, per-dimension `{id, tier, score}`, hard-filter rejected |
| `operations_summary(window)` | WP7 operations summary | cost, attempts, budget state |

### 11.3 Content safety, structurally

1. **Never read what must not leak.** `score_summary` folds judge dimensions at
   the service layer to `{id, tier, score}`. Evidence quotes and reasoning are
   not read and then filtered; they are not read.
2. **Role ceiling.** The MCP service identity maps to a role that fails
   `require_roles` on every PII-decrypting path. Calling
   `GET /candidates/{id}` or the raw-file route as that identity returns 403 —
   the guarantee is "cannot reach", not "not offered".
3. **Blacklist assertions.** Each tool's output is asserted field-set-exact and
   scanned for seeded secrets. (A substring sweep is explicitly *not* used: in
   WP7 it false-positived on the legitimate `prompt_version` value
   `resume_judge_v1`.)

### 11.4 Why this satisfies the exit gate

The gate requires MCP tools to enforce the *same* access and audit policy as
REST. Every REST path behind these four tools is already readable by `hr` and
already writes no `score_detail_read` audit — that audit exists only for the
evidence-bearing detail read, which is not exposed. So "same" is literally true
rather than a relaxation, and no path requiring PII authorization is reachable
over MCP at all.

## 12. Configuration

| Setting | Default | Meaning |
|---|---|---|
| `DINGTALK_SYNC_ENABLED` | `false` | Registers the Beat task and constructs the adapter |
| `DINGTALK_SYNC_INTERVAL_SECONDS` | `1800` | Beat interval |
| `SYNC_OVERLAP_SECONDS` | `300` | Cursor look-back |
| `SYNC_MAX_ITEMS_PER_RUN` | `200` | Per-run item cap |
| `SYNC_MAX_ITEM_ATTEMPTS` | `3` | Bounded replay for failed items |
| `SYNC_REPLAY_INTERVAL_SECONDS` | `3600` | `sync.replay_failed` Beat interval |
| `MCP_ENABLED` | `false` | Registers the MCP server |
| `MCP_SERVICE_ROLE` | `mcp_service` | Role ceiling for the service identity |

Every new setting needs a matching entry in
`backend/tests/test_bootstrap.py::TEST_ENV_DEFAULTS`; a guardrail test asserts
the key sets match `Settings.model_fields`.

## 13. Testing

### 13.1 Two-stage external verification

**Recorded fixtures (runnable now).**
`backend/tests/contracts/dingtalk-recruitment/v1.0/` holds a normal page, an
empty result, and malformed variants. Adapter parsing is strict: a missing
required field raises a typed error rather than yielding `None`. These tests
fix our reading of the documentation as executable assertions, so a later
mismatch fails loudly instead of drifting silently in production.

**Live probe (after the permission is granted).**
`backend/tests/external/test_dingtalk_recruitment_contract.py`, marked
`external_contract` and thus excluded from the default CI command, checks
endpoint existence, required fields, and pagination semantics. This mirrors how
WP2 verified the MinerU official v4 API.

### 13.2 Layers

| Layer | Covers |
|---|---|
| Unit | Triple-key dedupe, cursor advancement value, overlap window, failure classification |
| Unit | Adapter parsing against recorded fixtures |
| Integration | Sync → `create_or_reuse` → WP3 state machine, with a stub adapter |
| Integration | Repeated-sync idempotency (exit gate) |
| Integration | No transaction held across external calls |
| Integration | JD sync leaves `active_rule_version_id` untouched |
| Integration | MCP role matrix and content blacklist |
| External contract | Live DingTalk endpoints |

### 13.3 Exit-gate assertions

**Idempotent repeat.** Run the same source data twice. The second run reports
`ingested=0`, `skipped_duplicate=N`, job and candidate counts unchanged, and
**no fetch call occurs** — proving the saving is money, not just rows.

**MCP parity.** Parameterized role matrix per tool, plus a reverse assertion:
the MCP service identity calling `GET /candidates/{id}` and the raw-file route
receives 403.

**Manual upload unaffected.** With the adapter raising so the whole run fails,
a web upload still returns 202 and its job completes. With
`DINGTALK_SYNC_ENABLED=false`, the Beat schedule does not contain the task.

### 13.4 Python 3.10

The project supports Python 3.10–3.14 while local development runs 3.14. This
package is timestamp-heavy, and `datetime.UTC` is 3.11+. Use
`timezone.utc`, and verify with
`uv run --python 3.10 --extra dev pytest -m "not integration and not external_contract" -q`
before pushing. Hosted CI caught exactly this defect in WP7.

## 14. Rollout

1. Ship with `DINGTALK_SYNC_ENABLED=false` and `MCP_ENABLED=false`. The package
   is inert on deploy.
2. Once the administrator grants the recruitment permission, run the
   `external_contract` probe. If the real endpoints differ, change
   `services/sync/dingtalk.py` only.
3. Enable sync in a low interval against one JD, watch the audit report, then
   widen.
4. Enable MCP and point one Hermes instance at it.

Rollback is configuration: set either flag to `false`. No data migration is
reversed, and the ledger and cursor are additive tables.

## 15. Exit Criteria

- Repeated synchronization is idempotent and performs no redundant fetch.
- MCP tools enforce the same access and audit policy as REST, and no
  PII-authorizing path is reachable over MCP.
- A sync failure, at run level or item level, never blocks manual upload.
- JD synchronization cannot alter governed rule state.
- Full local gate green; hosted CI green on Python 3.10, 3.14, and integration.

## 16. Open Questions

1. **Recruitment API scope.** Inherited unresolved from the original design.
   The administrator can grant it; until the probe runs, the endpoint binding
   in `services/sync/dingtalk.py` is provisional.
2. **Hermes identity passthrough.** Whether Hermes can attach the asking user's
   `unionId` to a tool call. Determines whether the MCP surface can later widen
   beyond content-free tools.
3. **Attachment formats.** Whether recruitment attachments are always PDF/DOCX,
   or include formats WP1 upload validation currently rejects.

## 17. Approval

Pending review.
