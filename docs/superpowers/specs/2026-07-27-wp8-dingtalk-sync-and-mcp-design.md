# WP8 DingTalk Recruitment Sync and MCP/Hermes Design

**Date:** 2026-07-27

**Status:** Approved

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
  `"zhilian"` values. WP8 writes the more specific `"dingtalk_recruitment"`,
  because DingTalk may later be a source through a different channel and the
  same string is the primary key of `sync_cursors` (§6). The column is
  `String(32)` with no CHECK constraint and nothing reads it today, so the
  narrower value costs nothing.
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
                            source='dingtalk_recruitment', source_external_id=<id>
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

The cursor advances to **the newest source timestamp among every item the run
processed, failed ones included** — `max(updated_at)`, computed by
`ledger.next_cursor` once, after the whole batch, never to `now` and never
backwards. §9 requires exactly that of the failures: a failed item is one "the
cursor has moved past", which is why the bounded replay sweeper exists at all.

Because the advance is written once, at the end, a run that dies part-way
through never writes a cursor. The next run therefore re-opens the **original**
window — it does not resume from the last good point.

That is safe, not merely tolerable. Every item the dead run did ingest is
already in the ledger under the exact `(source, source_external_id,
content_sha256)` triple of §7, so re-opening the window buys a duplicate of
nothing: no second parse, no second extraction, no second score. The re-run is
paid for in re-listing and re-downloading, which is the cheap half; §9.1's
money is never spent twice.

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

### 10.1 The unscored window — an operational consequence, not a bug

A JD this task creates necessarily has `active_rule_version_id = NULL`, because
setting it is forbidden above. `ingestion_jobs.jd_code` carries no foreign key,
so a resume arriving for such a JD does not fail: the code is stored, the
lookup returns nothing, `jd_has_active_rule` is false, and
`backend/app/tasks/ingest.py` skips scoring. The resume is still downloaded,
parsed, extracted, and persisted as a Candidate, and the job completes
successfully.

The consequence is permanent. Candidates surface under a JD only through
`Score.jd_id`; there is no rescore or backfill path anywhere in the codebase;
the replay sweeper re-drives only rows whose outcome is `failed`; and the
dedupe ledger prevents a re-pull. So every resume synced between "the JD
appears" and "a human publishes a rule version for it" is ingested and then
invisible.

Running JD sync before the resume pull does not close this — the JD's rule
state is the same either way. The two operational rules that do:

- Publish a rule version for a JD **before** its postings start attracting
  applicants, not after.
- Treat a JD that sync created as incomplete until WP6c's publish flow has run
  against it.

Closing the window properly needs a backfill path — rescoring already-ingested
candidates when a JD's first rule version is published. That is out of WP8's
scope and is recorded here so the next work package inherits the question
rather than rediscovering it.

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
`ingested=0`, `skipped_duplicate=N`, and **creates no ingestion job** — so job
and candidate counts are unchanged and the repeat costs no MinerU parse and no
LLM call. That is the saving, and it is money rather than rows. The repeat does
download again, for the reason §9.1 gives: the dedupe key carries the content
hash, and nothing can know that hash before paying for the transfer. Asserted by
`test_a_repeat_run_creates_no_job_and_costs_no_parse` in
`backend/tests/integration/test_sync_runner.py`.

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

- Repeated synchronization is idempotent: a repeat creates no ingestion job, and
  therefore pays for no redundant parse, extraction, or score. It does download
  again, by §9.1's design.
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

### 16.1 Open question 1, itemized — the UNVERIFIED recruitment-API inventory

This is the committed, authoritative copy of the risk register behind open
question 1. It lives in the specification rather than in a task report because
it is the most consequential artifact of this work package: an operator or a
reviewer in any clone must be able to read exactly what is assumed.

**Provenance.** Every item below is transcribed from
`docs/specs/2026-05-12-resume-screening-agent-design.md` §8.2, which — unlike
its OAuth section — carries no `oas-ref:` citation for any of them. A read of
the project's DingTalk OAS on 2026-07-27 found no `recruitment` namespace at
all (§2.2), and the recruitment API permission has not been granted, so **not
one of these has ever been seen in a real response.**

**How they are settled.** One command, once the permission is granted:

```bash
uv run pytest backend/tests/external/test_dingtalk_recruitment_contract.py -m external_contract
```

A green offline or integration suite proves the code agrees with this
document, not that it agrees with DingTalk.

**Containment.** Nothing outside `backend/app/services/sync/dingtalk.py` and
the recorded fixtures in `backend/tests/contracts/dingtalk-recruitment/v1.0/`
references any of the 22. If the real shape differs, only that module and those
fixtures change. The `Site` column names constants and functions rather than
line numbers on purpose: line numbers in a committed specification rot, and
every item is anchored inside that one module.

**UNVERIFIED — 15 named endpoint / parameter / field facts**

| # | Kind | Value | Site (`services/sync/dingtalk.py`) |
|---|---|---|---|
| 1 | Endpoint path | `/v1.0/recruitment/candidates` | `CANDIDATES_PATH` |
| 2 | Method | `GET` on that path | `DingTalkRecruitmentAdapter.list_changed` |
| 3 | Query param | `since` (ISO-8601 with offset) | `list_changed` |
| 4 | Query param | `maxResults` (integer) | `list_changed` |
| 5 | Header applies | `x-acs-dingtalk-access-token` accepted by the *recruitment* namespace | `ACCESS_TOKEN_HEADER`, sent by `list_changed` |
| 6 | Response field | `list` (array) | `parse_candidates_page` |
| 7 | Response field | `hasMore` (bool) — recorded, deliberately **unread** | fixture only |
| 8 | Response field | `nextCursor` (string\|null) — recorded, deliberately **unread** | fixture only |
| 9 | Row field | `candidateId` — **required**, a row without it raises | `parse_candidates_page` |
| 10 | Row field | `updateTime` — **required**, a row without it raises | `parse_candidates_page` |
| 11 | Row field | `jobCode` (nullable, `str()`-coerced) | `parse_candidates_page` |
| 12 | Row field | `resume` (object) — optional | `parse_candidates_page` |
| 13 | Resume field | `fileName` — optional | `parse_candidates_page`, `_resolve_filename` |
| 14 | Resume field | `fileType` — optional | `parse_candidates_page` |
| 15 | Resume field | `downloadUrl` — optional | `parse_candidates_page`, `fetch` |

The JD half of the surface (`/v1.0/recruitment/jobs` and its `jobCode` / `name`
/ `description` rows, `JOBS_PATH` and `parse_jobs_page`) has exactly the same
status and the same provenance; §10 added it after this inventory was first
taken.

**UNVERIFIED — 7 behavioural assumptions**

| # | Assumption | How the code hedges |
|---|---|---|
| A | `updateTime` is ISO-8601 | Epoch milliseconds are *also* accepted; a timezone-naive value is refused, never guessed (`_parse_updated_at`) |
| B | `fileName` carries a file extension | One is derived from the content type or the URL path when it does not (`_resolve_filename`) |
| C | `downloadUrl` accepts a plain authenticated GET | Any failure → `ItemUnavailable`, one failed item, the batch continues (`fetch`) |
| D | `downloadUrl` is on the DingTalk origin | Assumed **not**: the token goes out only over HTTPS to the API host, compared by hostname (`_download_headers`, `_comparable_host`) |
| E | Pagination via `hasMore` / `nextCursor` | Not implemented, and **under G that can lose data silently**. `list_changed` asks for `maxResults = SYNC_MAX_ITEMS_PER_RUN + 1`. If the server honours that limit, orders newest-first, and more items than that match `since`, the page returned is the *newest* ones. G's sort then keeps the oldest `SYNC_MAX_ITEMS_PER_RUN` **of that page** and the cursor advances to their maximum, which sits near the top of the whole changed set: everything between `since` and that point that the server never returned is never listed again. `truncated` fires, but reports `dropped_at_least: 1` while far more were lost. The sort protects the page that arrived; it cannot protect against server-side truncation under an unknown order. `hasMore` is already in the recorded fixture, so **reading it and emitting a distinct audit event** would turn silent loss into a signal without building pagination against an unverified field. That guard does not exist today |
| F | `since` / `maxResults` are the filter semantics | Nothing. A wrong filter yields a wrong window, and the probe is the only detector |
| G | **Page ordering** — whether the feed lists oldest-first or newest-first | Depended on **not at all for the page that arrives**: the server's order is unknown, so `list_changed` sorts ascending by `updated_at` *before* the run cap truncates. The cap therefore always keeps the oldest end of **the returned page** and the cursor advances minimally under either ordering. It does not reach a page the server itself truncated — see row E. The probe `test_the_raw_page_arrives_oldest_first` records the raw order on its first real run |

**A further finding: the recruitment surface documents no single-candidate
lookup.** Established 2026-07-27. §8.2 of the source design names the list path
and nothing else — there is no `GET /candidates/{id}` and no id filter on the
list. This is a finding about the API rather than one of the 22 assumptions: it
is an absence in the documentation, not a guess at a shape.

Its consequence is structural. `DingTalkRecruitmentAdapter.describe` raises
`SourceCapabilityUnavailable` and issues no request, so the bounded replay of
failed items (§9) is **inert for DingTalk** until this is answered. The sweeper
spends no attempt on that refusal and counts such rows as `undescribable`,
never folded into `failed`, so an operator reading `replayed: 0, failed: 0` is
not told the queue is clean. Guessing a path would be strictly worse than
refusing: every guessed 404 would spend an attempt, and roughly
`SYNC_MAX_ITEM_ATTEMPTS` sweeps of that would drive the whole failed queue
terminal — permanent, silent loss of precisely the rows replay exists to
recover. This is the first thing to re-check when the permission is granted.

**What is verified, by contrast.** The corp access token this adapter
authenticates with: `POST /v1.0/oauth2/accessToken` with `appKey` / `appSecret`
returning `accessToken` / `expireIn` (seconds), read from the authoritative OAS
and in `backend/app/services/dingtalk/oauth.py`. So is the credential header
spelling `x-acs-dingtalk-access-token`, already in production use for the user
token, and the suffix/content-type set the WP1 upload gate accepts. The
recruitment namespace is the only unverified surface in the package.

## 17. Approval

Approved on 2026-07-27. Approval means implementation may proceed with both
halves shipped inert — `DINGTALK_SYNC_ENABLED=false` and `MCP_ENABLED=false`.

It does not authorize enabling the sync, and it does not mark WP8 Complete.
Completion remains blocked until the recruitment permission is granted and the
`external_contract` probe of §13.1 settles the §16.1 inventory against a real
response. `docs/superpowers/plans/README.md` records the same status.
