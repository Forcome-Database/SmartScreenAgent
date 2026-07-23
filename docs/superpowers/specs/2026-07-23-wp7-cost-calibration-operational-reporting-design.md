# WP7 Cost, Calibration, and Operational Reporting Design

**Date:** 2026-07-23

**Status:** Draft (independent review approved; awaiting user document approval)

**Work package:** WP7

**Depends on:** WP3 durable ingestion jobs, WP6 attributable feedback, versioned
golden labels, baseline metrics, and governed rule publication

## 1. Purpose

WP7 makes SmartScreen's LLM operating cost and scoring quality measurable from
first-party records. It adds:

- a centralized, attempt-level LLM usage ledger with immutable price snapshots
  and estimated CNY cost;
- daily/monthly budget states and deduplicated in-product audit alerts;
- manually created, immutable quality releases bound to a content-addressed
  golden-set snapshot and each JD's active rule version;
- descriptive confidence-reliability, evidence-coverage, AI-HR agreement,
  latency, throughput, and cost measurements;
- optional asynchronous cross-engine scoring for a deterministic risk sample;
- deterministic batch-rejection analysis; and
- a responsive operations-and-quality workspace in the existing Next.js app.

WP7 is one coordinated work package. The usage ledger is the source for cost
and operational metrics; the WP6 labels and rule versions are the source for
quality releases; and the same reporting surface exposes both. Implementation
may be sequenced into independently tested tasks, but no separate service or
deployment is introduced.

The roadmap says operating cost must be "enforceable" and budget enforcement
must be tested. For WP7, enforcement means that configured thresholds are
evaluated deterministically, shown as yellow/red states, and recorded as
deduplicated audit events. **Budget thresholds do not block LLM calls.** The
only fail-closed behavior is accounting integrity before a paid call: if a
pending ledger attempt cannot be created, the provider is not called.

## 2. Baseline and Gaps

### 2.1 Baseline

- `LLMGateway` is the central provider adapter for extraction, judge, fallback,
  and lightweight calls. It already returns model name, prompt/output token
  counts, prompt version, latency, and whether fallback was used, but only logs
  this metadata.
- Primary and fallback calls are separate provider attempts, but a fallback is
  represented only by the final `LLMResponse.used_fallback` flag. There is no
  durable record of the failed primary attempt.
- `Score` has legacy `cost_tokens` and `cost_cny` fields. `cost_tokens` records
  only the judge total and `cost_cny` is not populated. These fields cannot
  represent extraction, retries, fallbacks, failed calls, or historical prices.
- `DAILY_LLM_BUDGET_CNY` and `MONTHLY_LLM_BUDGET_CNY` exist, but nothing
  computes budget state or alerts on it.
- WP3 provides durable Celery jobs, leases, retries, a sweeper, and trace/job/
  score identifiers.
- WP6 provides:
  - one current golden label per `(candidate_id, jd_id)`;
  - AI-HR feedback with server-derived agreement;
  - active, published, and archived rule versions;
  - golden-set classification metrics; and
  - stored judge dimensions containing a validated per-dimension confidence and
    evidence quotes.
- `Score.cross_engine_diff` and `Score.is_suspicious` already exist but are not
  populated.
- The frontend is a Next.js 15 App Router application using React 19,
  Tailwind, Base UI-based shadcn components, TanStack Query, Zod, and Lucide.
  Its current global navigation is a single horizontal row.

### 2.2 Gaps

- Cost is not reconstructable or attributable per provider attempt.
- No price version is captured, so changing configuration could rewrite the
  apparent cost of old calls.
- No budget status, operational health view, or usage drill-down exists.
- Baseline quality is live-computed rather than preserved as a release record
  against immutable inputs.
- Judge confidence and evidence availability are not summarized.
- The optional second-engine fields are unused and there is no durable,
  recoverable sampling workflow.
- Rejection reasons and AI-HR consistency are not combined into a release
  quality view.

## 3. Goals

1. Persist one PII-free ledger row for every attempted LLM provider call,
   including primary and fallback attempts, transport/configuration failures,
   token usage when known, immutable rate snapshots, cost, and latency.
2. Refuse a paid provider call if its pending ledger record or configured model
   price cannot be established.
3. Compute daily and monthly CNY usage in `Asia/Shanghai`; show normal/yellow/
   red states and emit one audit event per threshold crossing and period.
4. Create immutable quality releases manually. Each release binds:
   - a content-addressed snapshot of the selected golden entries;
   - the active rule version for every selected JD;
   - an explicit observation window; and
   - the metric definitions and target values used.
5. Record classification F1/accuracy, evidence coverage, descriptive judge
   confidence reliability, AI-HR agreement, latency, throughput, and cost
   trends without blocking release creation.
6. Run optional second-engine checks asynchronously for a deterministic 10%
   sample plus explicit risk triggers, recover lost Celery wake-ups, and expose
   a PII-free suspicious queue.
7. Provide deterministic, aggregate batch-rejection analysis without sending
   reasons or resume data to another LLM.
8. Replace the crowded app header with an accessible, responsive information
   architecture for the existing pages and four new WP7 work surfaces.

## 4. Non-goals

- Blocking or throttling LLM calls because a daily/monthly budget is yellow or
  red.
- Email, Slack, Teams, DingTalk, or other external alert delivery.
- Reconciliation against a provider invoice or billing export.
- Fabricating historical usage rows for calls made before WP7.
- Training or fitting Platt scaling, isotonic regression, or another calibrated
  probability model.
- Treating judge confidence as the probability that a candidate should
  advance.
- Automatically creating daily quality releases.
- LLM-based clustering or summarization of rejection reasons.
- Replacing the primary score with the secondary-engine score.
- A Web UI for editing model prices, budgets, sampling configuration, or target
  thresholds.
- A data-deletion or retention-policy workflow for ledger/release rows.
- Adding a separate event bus, analytics warehouse, or microservice.

## 5. Architecture and Boundaries

WP7 remains a modular monolith in FastAPI/PostgreSQL/Celery:

```text
Extractor / Judge / Cross-check
              |
              v
       LLMGateway + UsageRecorder
       |  A: insert pending (short tx)
       |  B: finalize attempt (short tx)
       v
       LLM provider

llm_usage_attempts ---> operations queries ---> operations UI

Score / Feedback / GoldenSet
              |
              v
      risk sampling service
              |
       score_cross_checks (source of truth)
              |
        Celery wake-up + sweeper

GoldenSet + active RuleVersion + Score + Feedback + Usage
              |
      repeatable-read release creation
              |
golden_set_snapshots + quality_releases ---> quality UI
```

### 5.1 Module boundaries

- **Usage recorder:** creates/finalizes attempt rows and validates price
  configuration. It never sees prompts or response content.
- **Operations reporting:** reads ledger aggregates and budget state; it never
  calls a provider.
- **Quality metrics:** pure metric functions plus database selection queries.
  They reuse WP6's `metric_stats`.
- **Quality release service:** owns snapshot hashing, rule-version binding, and
  the transaction that persists immutable release results.
- **Cross-check service:** decides sampling, creates/claims queue rows, invokes a
  secondary judge through the same metered gateway, and persists sanitized
  comparison output.
- **Batch analysis:** deterministic queries over persisted score JSON and audit
  tags; it does not call an LLM.

### 5.2 Gateway and recorder interface

All public gateway calls receive an immutable `LLMCallContext`:

```text
operation
call_group_id
trace_id?
ingestion_job_id?
score_id?
jd_id?
rule_version_id?
```

The caller creates one `call_group_id` per logical operation. `ScoringPipeline`
accepts this context from the ingestion task (or creates one for synchronous
scoring) and persists the judge group on the resulting Score. The trace ID is
the ambient request/job trace rather than a new unrelated value per attempt.

`LLMGateway.judge` additionally accepts an internal-only `model_override` and
`attempt_role`. They are permitted only for the cross-check service, which
passes the configured secondary model and role `secondary`; that call does not
use the primary model's fallback. Normal extract/judge APIs retain their
configured primary/fallback selection and create a separate ledger attempt for
each.

`UsageRecorder` owns `AsyncSessionLocal`-backed independent sessions for its
short transactions. It never commits, rolls back, or reuses the caller's
candidate/score transaction. This keeps external calls outside business-data
transactions while allowing task, API, and cross-check callers to share one
metered gateway.

## 6. Data Model and Migration

One additive Alembic migration on the current WP6c head creates the following
tables and constraints. The implementation must also update the migration-head
assertion and `scripts/verify.py`.

### 6.1 `llm_usage_attempts`

One row represents one provider request, not one logical extract/judge
operation.

- `id`: bigint primary key
- `call_group_id`: UUID, non-null, indexed; generated once per logical
  extract/judge/cross-check/lightweight operation and shared by its
  primary/fallback attempts
- `trace_id`: string(64), nullable, indexed
- `ingestion_job_id`: nullable FK to `ingestion_jobs.id`, indexed
- `score_id`: nullable FK to `scores.id`, indexed
- `jd_id`: nullable FK to `jds.id`, indexed
- `rule_version_id`: nullable FK to `rule_versions.id`, indexed
- `operation`: string(32), one of `extract`, `judge`, `cross_check`,
  `lightweight`
- `attempt_role`: string(16), one of `primary`, `fallback`, `secondary`
- `requested_model`: string(128)
- `actual_model`: nullable string(128)
- `prompt_version`: string(64)
- `status`: string(32), one of `pending`, `succeeded`, `unavailable`,
  `invalid_response`, `configuration_error`, `abandoned`
- `input_tokens`: nullable integer
- `output_tokens`: nullable integer
- `input_price_cny_per_million`: numeric(18,6), non-null
- `output_price_cny_per_million`: numeric(18,6), non-null
- `estimated_cost_cny`: nullable numeric(24,12)
- `latency_ms`: nullable integer
- `error_code`: nullable string(64), stable taxonomy only
- `started_at`: timezone-aware datetime, non-null
- `finished_at`: nullable timezone-aware datetime

Checks enforce nonnegative tokens/rates/cost/latency, valid status and role, and
terminal rows having `finished_at`. Application services never update a
terminal row. There is no prompt, provider response, exception message,
candidate name, resume text, ciphertext, object key, or arbitrary metadata
column.

The same migration adds nullable, indexed UUID
`Score.llm_judge_call_group_id`. A new-score judge call receives a
`call_group_id` before the provider request and persists that value when the
`Score` is inserted. Its terminal ledger rows remain immutable and may have
`score_id = NULL`; the call-group link provides exact correlation after score
creation. Calls for an already existing score, including cross-checks, populate
`score_id` directly. `rule_version_id` is known before every judge/cross-check
call and supports version-exact release attribution.

`Score.cost_tokens` and `Score.cost_cny` remain for schema compatibility. New
reports do not read them; the ledger is authoritative. No historical rows are
synthesized from these incomplete fields.

### 6.2 `score_cross_checks`

- `id`: bigint primary key
- `score_id`: FK to `scores.id`, indexed
- `secondary_model`: string(128)
- `prompt_version`: string(64)
- `sample_reasons`: JSONB list drawn from `deterministic_sample`,
  `low_confidence`, `golden_error`, `ai_hr_disagreement`, `admin_backfill`
- `state`: string(32), one of `queued`, `running`, `completed`,
  `retryable_failed`, `terminal_failed`
- `attempts`: nonnegative integer
- `lease_expires_at`: nullable timezone-aware datetime
- `lease_token`: nullable UUID
- `last_error_code`: nullable string(64)
- `secondary_total_score`: nullable numeric(6,2)
- `secondary_dimensions`: nullable JSONB containing only dimension `id`, `tier`,
  `score`, and `confidence`
- `absolute_diff`: nullable numeric(6,2)
- `threshold_snapshot`: numeric(6,2), non-null
- timestamps from `TimestampMixin`; `completed_at` nullable

`(score_id, secondary_model, prompt_version)` is unique. The row is the durable
queue source of truth; Celery delivery is only a wake-up mechanism.
`sample_reasons` may be merged idempotently when a later risk signal selects an
already queued/completed score.

The sanitized `secondary_dimensions` intentionally excludes reasoning,
evidence quotes, prompts, and candidate text.

Indexes support the required query paths:

- ledger `(started_at, id)`, `(status, started_at)`,
  `(jd_id, rule_version_id, started_at)`, and `(operation, started_at)`;
- cross-check `(state, lease_expires_at)`, `(score_id, id)`, and
  `(completed_at, absolute_diff)`; and
- release `(created_at, id)` and release-JD `(jd_id, quality_release_id)`.

### 6.3 `golden_set_snapshots` and `golden_set_snapshot_entries`

`golden_set_snapshots`:

- `id`: bigint primary key
- `content_sha256`: unique string(64)
- `item_count`: nonnegative integer
- `created_by_user_id`: FK to `users.id`
- `created_at`: timezone-aware datetime

`golden_set_snapshot_entries`:

- `id`: bigint primary key
- `snapshot_id`: FK to `golden_set_snapshots.id`
- `candidate_id`: FK to `candidates.id`
- `jd_id`: FK to `jds.id`
- `label`: `advance`, `reject`, or `borderline`
- unique `(snapshot_id, candidate_id, jd_id)`

There are no update/delete APIs. Snapshot content is sorted by
`(jd_id, candidate_id, label)` and serialized as canonical UTF-8 JSON before
SHA-256 hashing. Concurrent creation uses the unique hash and
`ON CONFLICT`/reload so identical content reuses one snapshot.

### 6.4 `quality_releases` and `quality_release_jds`

`quality_releases`:

- `id`: bigint primary key
- `golden_snapshot_id`: FK to `golden_set_snapshots.id`
- `window_start`, `window_end`: timezone-aware datetimes
- `status`: `meets_target` or `below_target`
- `metrics_json`: immutable JSONB aggregate
- `targets_json`: immutable JSONB target/definition snapshot
- `created_by_user_id`: FK to `users.id`
- `created_at`: timezone-aware datetime

`quality_release_jds`:

- `id`: bigint primary key
- `quality_release_id`: FK to `quality_releases.id`
- `jd_id`: FK to `jds.id`
- `rule_version_id`: FK to `rule_versions.id`
- `metrics_json`: immutable JSONB for that JD
- unique `(quality_release_id, jd_id)`

The API exposes snapshot hash/count and rule-version mappings, not snapshot
candidate entries.

`targets_json` includes `metric_schema_version = "wp7_v1"` plus all numeric
targets, fixed bin boundaries, minimum bucket size, evidence definition, and
classification label mapping so old releases remain interpretable after code
changes.

### 6.5 `operations_reconciliation_state`

A small cursor table makes budget-alert recovery durable across extended
downtime:

- `key`: string(32) primary key (`budget_daily` or `budget_monthly`)
- `next_period_start`: timezone-aware datetime, non-null
- `updated_at`: timezone-aware datetime, non-null

On first use, a cursor initializes to the earliest ledger `started_at` at or
after WP7 rollout (or the current local period if the ledger is empty). This is
operational state, not a budget-blocking control and not a user-facing alert
record.

## 7. Attempt-Level Usage Recording

### 7.1 Call lifecycle

For each primary, fallback, secondary, or lightweight provider request:

1. Resolve the configured input/output price for the **requested** model.
2. In independent short transaction A, insert a `pending` attempt with the
   immutable price snapshot and non-content context identifiers.
3. Only after transaction A commits, make the external provider call. No
   database transaction or row lock remains open during the network request.
4. In independent short transaction B, finalize the attempt:
   - provider success: `succeeded`, actual model, tokens when reported, cost,
     and latency;
   - retryable transport/provider failure: `unavailable`;
   - malformed provider response: `invalid_response`;
   - rejected request/authentication/configuration: `configuration_error`.
5. If the primary attempt is eligible for fallback, finalize it first, then
   create a separate pending fallback row and repeat the lifecycle.

Cost is:

```text
(input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
```

The calculation uses Python `Decimal` constructed from validated decimal
strings, never binary float. The per-attempt result is quantized once to
12 fractional CNY digits with `ROUND_HALF_UP`, matching
`numeric(24,12)`. Aggregates sum stored decimals and round only for display.

If the provider does not report usage, token counts and cost are `null`, not
zero. A provider HTTP success that later fails domain validation is still a
successful paid provider attempt; the existing extraction/judge validation and
retry path records the application-level invalid-output failure separately.

### 7.2 Failure semantics

- Missing model price fails before transaction A with
  `model_price_missing`; no provider call occurs.
- If transaction A cannot commit, raise retryable
  `usage_ledger_unavailable`; no provider call occurs.
- Transaction B gets bounded local retries. If it still fails after a paid
  call, do not repeat the provider request solely for accounting. Return/raise
  according to the original provider outcome, log a metadata-only critical
  event, and leave the row `pending`.
- A sweeper marks pending rows older than the configured timeout
  `abandoned`, with unknown usage/cost. It never invents token counts.
- Ingestion maps `usage_ledger_unavailable` to its existing retryable job path.
  Synchronous scoring maps it to a retryable `503`.

This yields honest accounting even across process crashes: duplicate provider
calls caused by a retried ingestion job remain separate ledger attempts.

### 7.3 Time attribution

All database timestamps are timezone-aware and stored in UTC. Query intervals
are half-open `[start, end)`:

- a provider attempt and its cost belong to `started_at`;
- latency is included only after a terminal attempt has known `latency_ms`;
- score throughput belongs to `Score.created_at`;
- feedback belongs to `coalesce(Feedback.updated_at, Feedback.created_at)`;
- cross-check completion belongs to `completed_at`; and
- release creation belongs to `quality_releases.created_at`.

An attempt has `unknown_usage = true` if either input or output token count is
null; in that case both cost computation and `estimated_cost_cny` are null.

## 8. Pricing, Budget State, and Alerts

### 8.1 Price configuration

`LLM_PRICE_CNY_PER_MILLION_JSON` is a JSON object keyed by the exact requested
model name:

```json
{
  "gpt-5-mini": {"input": 1.25, "output": 10.0},
  "qwen-plus": {"input": 0.8, "output": 2.0}
}
```

Configuration validation requires finite, nonnegative input/output numbers for
every model that may be called, including fallbacks and the optional secondary
model. A rate with more than six fractional decimal places is rejected; accepted
rates are normalized to six places once and that exact Decimal is used both for
snapshot persistence and cost calculation. The price snapshot on the ledger
row, rather than current configuration, is used for all historical totals.

### 8.2 Budget semantics

- Existing `DAILY_LLM_BUDGET_CNY` and `MONTHLY_LLM_BUDGET_CNY` remain.
- `LLM_BUDGET_WARN_RATIO` defaults to `0.80`.
- Daily and calendar-month boundaries use `Asia/Shanghai`, converted to UTC
  half-open ranges for querying `started_at`.
- The numerator sums every non-null `estimated_cost_cny`, regardless of final
  success/failure status. Unknown-cost attempts are counted separately.
- State is:
  - `normal`: below warning ratio;
  - `warning`: at/above warning ratio and below 100%;
  - `exceeded`: at/above 100%.
- State is informational and never authorizes or denies a provider call.

After attempt finalization, threshold evaluation writes audit events
`llm_budget_warning` and `llm_budget_exceeded`. The dedupe key is
`scope:period_start:threshold`. A PostgreSQL transaction-level advisory lock
for that key serializes "check existing audit row + insert", producing at most
one event for each daily/monthly threshold crossing without another state
table. Audit payload contains only scope, period, threshold, budget, and
observed aggregate.

Alert creation is crash-safe through cursor-based reconciliation. For each
daily/monthly cursor, the sweeper locks its state row, evaluates complete local
periods in order (bounded work per run), inserts any missing event under the
same advisory lock, and advances the cursor only after that period succeeds.
It also evaluates the current partial period every run. Therefore downtime does
not skip an old crossing; backlog is drained over subsequent runs without an
unbounded single query. Finalization provides low-latency notification and the
durable cursor provides eventual delivery. A call that moves directly from
normal to exceeded emits both the warning and exceeded events because each
threshold/dedupe key is evaluated independently.

Operations comparison windows are:

- `today`: local midnight to now, compared with the same elapsed duration
  beginning at the previous local midnight;
- `7d`/`30d`: `[now - N days, now)`, compared with the immediately preceding
  equal-length interval.

## 9. Quality Release Semantics

### 9.1 Creation transaction

`POST /api/v1/quality/releases` executes in a repeatable-read transaction:

1. Resolve the requested JD codes, or all JDs represented by the current golden
   set when omitted.
2. Require at least one golden entry (`409 golden_set_empty`).
3. Bind each selected JD to its current `active_rule_version_id`; any selected
   JD without one produces `409 active_rule_missing`.
4. Create or reuse the content-addressed snapshot for only the selected JDs.
5. For each snapshot entry, select the most recent `Score` whose
   `rule_version_id` exactly matches that JD's bound version **and whose
   `created_at` is in `[window_start, window_end)`**. A score outside the
   observation window or produced by a different version is `uncovered`, not
   silently substituted.
6. Compute aggregate and per-JD metrics.
7. Persist the release, JD/version bindings, metric payloads, and target
   snapshot atomically.

The default observation window is the preceding 30 days. Input timestamps must
be ordered, timezone-aware, end no later than request time, and span no more
than 365 days.

Classification, evidence, confidence, feedback, latency, throughput, and cost
all use this same half-open current window. Trend metrics use the immediately
preceding equal-length window
`[window_start - (window_end - window_start), window_start)`.

Snapshot reuse and the release insert are covered by a bounded whole-transaction
retry (maximum three attempts) for PostgreSQL serialization failures and a
concurrent snapshot-hash unique conflict. Each retry rolls back and restarts the
entire repeatable-read transaction, including reading golden rows and active
rule versions; it never tries to reload a concurrently inserted snapshot inside
an old transaction snapshot. Exhaustion returns retryable `503
release_transaction_conflict`.

### 9.2 Immutable result

A quality release is append-only. There is no update/delete route. Later golden
imports, rule publications, feedback, prices, or targets do not change it.
Creating the same release request again creates another release record but may
reuse the same golden snapshot.

Quality targets do not block persistence. `below_target` is a visible and
audited result, not an HTTP error.

The release transaction always writes `quality_release_created`; when status is
`below_target` it also writes `quality_release_below_target`. Each `AuditLog`
uses actor `user:{created_by_user_id}`, target type `quality_release`, target ID
the new release ID, and a candidate-content-free payload containing snapshot
hash, selected JD/rule-version IDs, aggregate F1/evidence target states, and the
observation window. Release rows and both required audit rows commit atomically.

## 10. Metric Definitions

All ratios are returned as numbers from 0 to 1 or `null` when their denominator
is zero. Responses include counts/denominators so a percentage is never
detached from sample size.

WP7 strengthens the governed `RuleSchema` contract: every rule/judge dimension
weight must be finite and nonnegative. Zero-weight dimensions remain legal but
are excluded from weighted-confidence and low-score calculations. Draft
creation/import rejects a negative/non-finite weight as
`422 invalid_rule_schema`; release creation against a pre-existing invalid
active schema returns `409 invalid_active_rule` rather than producing a
nonsensical metric.

### 10.1 Classification

- Golden `advance` is positive; `reject` is negative.
- Golden `borderline` is excluded from precision/recall/F1/accuracy and counted
  separately.
- Missing version-matching score is `uncovered`.
- `grade != "rejected"` predicts advance.
- Confusion, precision, recall, F1, and accuracy reuse WP6's
  `metric_stats`.
- Default F1 target is `QUALITY_F1_TARGET=0.75`.

### 10.2 Evidence coverage

Only candidates whose stored score reached the judge
(`judge_dimensions is not null`) participate.

- Denominator: the number of judge dimensions expected by the bound rule
  version across all participating candidates.
- Numerator: expected dimensions whose persisted judge result is non-unknown,
  has a numeric score, and has at least one already-validated evidence quote.
- Unknown/missing judge dimensions remain in the denominator.
- Hard-filter rejects are counted separately and are not in the denominator.
- If selected rules define no judge dimensions, coverage is `null` with status
  `not_applicable`.
- If judge dimensions exist but there is no version/window-matching score that
  reached the judge, coverage is `null` with status `insufficient_data`.
- Default target is `QUALITY_EVIDENCE_COVERAGE_TARGET=0.95`.

No evidence quote is copied into a release response or metric payload.

### 10.3 Descriptive judge-confidence reliability

This is explicitly labeled **judge self-rated reliability**, not candidate
advance probability.

For each non-borderline, covered golden item that reached the judge:

1. Match persisted judge dimensions to the bound rule's dimension weights.
2. Exclude unknown dimensions from the candidate's confidence numerator and
   weight denominator.
3. Compute weighted judge confidence:

```text
sum(confidence_i * weight_i) / sum(weight_i)
```

4. Mark decision correctness as 1 when the score's advance/reject prediction
   matches the golden label, otherwise 0.
5. Place the item in one of five fixed bins:
   `[0,.2)`, `[.2,.4)`, `[.4,.6)`, `[.6,.8)`, `[.8,1]`.

If every matched dimension is unknown, missing, or has total eligible weight
zero, that candidate has no weighted confidence: it is excluded from bins/ECE
and counted as `confidence_unavailable`.

Each bin reports count, mean confidence, empirical decision accuracy, absolute
gap, and status. A bin with fewer than
`QUALITY_CONFIDENCE_MIN_BUCKET_SIZE=10` items has status
`insufficient_data`; its accuracy/gap are `null` and it is excluded from ECE.

ECE is:

```text
sum(n_bin / N_sufficient * abs(mean_confidence_bin - accuracy_bin))
```

over sufficient bins only. With no sufficient bin, ECE is `null`. ECE has no
release target in WP7.

### 10.4 AI-HR agreement

Reuse WP6's server-derived `Feedback.ai_agreed`:

- `hold`/null agreement is excluded from the agreement denominator and counted;
- only feedback linked to a score using the release's bound rule version and
  updated inside the observation window is included;
- report overall and per-JD agreed/disagreed/hold counts and agreement rate.

Agreement is descriptive and has no release target.

### 10.5 Latency, throughput, and cost

For the release window, report:

- ledger attempt count, success/failure/abandoned counts, and unknown-usage
  count;
- p50 and p95 latency for terminal attempts with known latency;
- sum of known estimated CNY cost;
- completed version-matching score count and average scores per day; and
- the same values for the immediately preceding equal-length window, with
  absolute and percentage deltas where defined.

Latency percentiles use PostgreSQL `percentile_cont(0.50)` and
`percentile_cont(0.95)` over known `latency_ms`, i.e. continuous linear
interpolation. The aggregate release operation metrics are the sum/union of the
same per-JD attribution rule: extraction attempts attributed to each selected
JD plus judge/cross-check attempts whose `rule_version_id` equals that JD's
release binding. Wrong-version judge attempts and unattributed usage remain in
the global operations dashboard total but not in a quality release. Latency,
throughput, cost, ECE, and agreement are trend/diagnostic metrics only; they do
not change release status.

### 10.6 Target result

Each target metric reports `meets_target`, `below_target`,
`insufficient_data`, or `not_applicable`.

- An evaluable F1/evidence value compares with its snapshotted target.
- A null F1 caused by no covered classification sample is
  `insufficient_data`.
- Evidence coverage for a rule with no judge dimensions is `not_applicable`.
- Only the **aggregate** F1 and aggregate evidence target results control the
  release rollup; per-JD target results are diagnostic and cannot overturn it.
- Release rollup is `below_target` if either aggregate target is
  `below_target` or `insufficient_data`. An aggregate `not_applicable`
  evidence result has no effect. Otherwise it is `meets_target`.
- All results are saved.

## 11. Cross-Engine Sampling and Recovery

### 11.1 Eligibility and triggers

Cross-engine checking is enabled only when `CROSS_ENGINE_MODEL` is configured
and differs from the primary judge model. A score is eligible only when the
bound RuleVersion schema has at least one judge dimension and persisted
`judge_dimensions.dimensions` is a non-empty list matching that schema. A
non-null payload with an empty dimensions list did not make a provider judge
call and is not eligible.

A check is ensured when any trigger is true:

- `deterministic_sample`: SHA-256 of the UTF-8 string
  `"wp7:{score_id}:{prompt_version}"`; interpret the first eight digest bytes
  as an unsigned big-endian integer, take modulo 100, and select when the result
  is below `CROSS_ENGINE_SAMPLE_PERCENT` (default 10%);
- `low_confidence`: weighted judge confidence is below
  `CROSS_ENGINE_LOW_CONFIDENCE` (default 0.60), or confidence is unavailable
  because all judge dimensions are unknown;
- `golden_error`: an advance/reject golden label disagrees with the score;
- `ai_hr_disagreement`: persisted feedback has `ai_agreed = false`;
- `admin_backfill`: selected by the bounded backfill endpoint.

New-score completion evaluates deterministic/low-confidence and any current
golden label. Feedback upsert and golden-set import/update re-evaluate their
respective disagreement triggers. All paths call the same idempotent
`ensure_cross_check` service **inside the same database transaction that
persists the Score, Feedback, or GoldenSet change**. Existing feedback/golden
services are refactored so the caller owns the commit. This prevents a crash
between the business write and queue-row creation from losing a trigger.
Celery delivery occurs only after that transaction commits.

### 11.2 Secondary computation

The worker:

1. claims the row under a lease in a short transaction;
2. loads the score, bound rule version, and candidate resume;
3. runs the same judge prompt/schema/validation with the configured secondary
   model through the metered gateway;
4. reuses the stored rule subtotal and computes the secondary total/grade with
   the bound rule's `_grade_from`;
5. stores only sanitized dimension summaries, the secondary total, absolute
   total-score difference, and the snapshotted threshold; and
6. atomically projects:
   - `Score.cross_engine_diff = absolute_diff`;
   - `Score.is_suspicious = absolute_diff >= threshold`.

The default `CROSS_ENGINE_DIFF_THRESHOLD` is 10 score points. The secondary
result never changes the primary total or grade.

The cross-check table, not Score, is authoritative history. For each score, the
row with the greatest `score_cross_checks.id` is the current requested
configuration. Creating a newer row atomically clears the two Score projection
fields. A worker may update the projection only if its row is still that
greatest ID; delayed completion of an older model/prompt row remains historical
and cannot overwrite it. The suspicious-list endpoint likewise considers only
the greatest-ID row per score, and only when that row is `completed` and meets
its snapshotted threshold.

### 11.3 Durable delivery

- Queue row creation commits before Celery delivery.
- A committed `queued` row is the source of truth; a lost `.delay()` is
  recovered by the sweeper.
- A new row starts `queued`, `attempts = 0`, with no lease/token.
- Claim uses `SELECT ... FOR UPDATE`, accepts `queued` only, verifies
  `attempts < CROSS_ENGINE_MAX_ATTEMPTS`, then commits `running`,
  `attempts + 1`, a random `lease_token`, and
  `lease_expires_at = now + CROSS_ENGINE_LEASE_SECONDS`.
- Completion/failure uses a conditional update matching row ID, `running`
  state, and lease token. A duplicate/stale worker cannot finalize.
- Retryable failure changes `running -> retryable_failed`, clears the lease,
  and records a stable error code. The sweeper changes it to `queued` when
  attempts remain, otherwise `terminal_failed`.
- An expired `running` lease follows the same retry/terminal rule.
- `unavailable`, `usage_ledger_unavailable`, and transient database/provider
  failures are retryable. `model_price_missing`, provider configuration/auth
  errors, invalid secondary output after validation retries, and a missing
  score/rule/candidate are terminal.
- `CROSS_ENGINE_LEASE_SECONDS` must be greater than Celery
  `task_time_limit` (currently 600 seconds); the default is 900.
- The periodic sweeper also marks stale usage attempts abandoned.

### 11.4 Historical backfill

There is no automatic full-history backfill. An admin may request a bounded
selection by JD, score-created time window, and limit. The service previews/
returns selected and newly queued counts, caps the limit at
`CROSS_ENGINE_BACKFILL_MAX` (default 500), and relies on the same unique
constraint and queue recovery.

## 12. Deterministic Batch-Rejection Analysis

`GET /api/v1/reports/batch` filters by an exact ingestion `batch_id`, JD code,
and/or a bounded score-created time window. It returns aggregates only.

For rejected scores:

- hard-filter reasons are counted by persisted `audit_tag`;
- rule dimensions are counted as low when their persisted
  `score < 0.5 * weight`;
- judge dimensions are grouped as `unknown` or low when their numeric
  `score < 0.5 * weight`;
- grade distribution is counted separately.

Each reason reports occurrences, distinct affected-score count, and the
affected-score percentage using total rejected scores as denominator. A score
may contribute to multiple reasons, so reason percentages are not required to
sum to 100%; the API states this explicitly. No free-text reasoning or evidence
is returned.

The default window is 30 days and the maximum is 90 days. At least one filter
must be present to prevent an accidental unbounded scan.

## 13. Backend API

Errors use the existing `{code, message}` detail shape. Pagination uses WP4's
page primitives.

### 13.1 Operations

Roles: `hr_lead`, `admin`.

- `GET /api/v1/operations/summary?window=today|7d|30d`
  - returns current/previous aggregates, daily series, operation/model/outcome
    breakdowns, daily/monthly budget states, unknown-cost counts, and last
    completed attempt time.
- `GET /api/v1/operations/usage`
  - paginated;
  - filters: half-open, at-most-90-day `from`/`to`, operation,
    requested/actual model, status,
    attempt role, trace ID, ingestion job ID, score ID, and JD code;
  - returns only ledger metadata defined in §6.1.

### 13.2 Quality releases

Read roles: `hr`, `hr_lead`, `admin`. Create roles: `hr_lead`, `admin`.

- `POST /api/v1/quality/releases`
  - body:
    `{window_start?, window_end?, jd_codes?, expected_input_fingerprint?}`;
  - returns `201` with the immutable release detail;
  - `409 golden_set_empty`, `409 active_rule_missing`;
  - when the optional preview fingerprint no longer matches, `409
    release_input_changed`;
  - invalid/range-too-large input is `422`.
- `POST /api/v1/quality/releases/preview`
  - same selection fields, read-only and non-persisting;
  - returns selected JD/version mappings, golden item/label counts,
    version/window-matching score coverage counts, targets, and an
    `input_fingerprint`;
  - the fingerprint is a canonical hash of golden content hash, JD/version
    bindings, window, targets, and metric schema version.
- `GET /api/v1/quality/releases`
  - paginated newest first; optional JD/status filters.
- `GET /api/v1/quality/releases/{release_id}`
  - aggregate metrics, per-JD metrics, target results, golden snapshot
    hash/count, rule-version map, creator, and timestamps.

No endpoint exposes snapshot entries.

### 13.3 Batch analysis

Roles: `hr`, `hr_lead`, `admin`.

- `GET /api/v1/reports/batch`
  - filters and output from §12;
  - missing filters or oversized range is `422`.

### 13.4 Cross-engine

List roles: `hr`, `hr_lead`, `admin`. Backfill role: `admin`.

- `GET /api/v1/cross-checks/suspicious`
  - paginated;
  - filters: JD, minimum difference, reason, time window;
  - returns cross-check ID, score ID, candidate ID, JD code, primary/secondary
    totals, absolute diff, threshold, sanitized dimension differences, sample
    reasons, model, and completion time;
  - no name, contact data, resume text, evidence, reasoning, ciphertext, object
    key, or prompt.
- `POST /api/v1/cross-checks/backfill`
  - body: `{jd_code?, from, to, limit, dry_run=false}`;
  - `dry_run=true` returns selected/already-existing/would-queue counts without
    mutation; the confirmed request sends `false` and returns newly queued;
  - invalid limit/window is `422`.

Candidate detail is not embedded in the suspicious response. WP7 adds a
metadata-only `score_detail_read` audit write to the existing authorized score
detail GET (actor user ID, target score ID, candidate ID, and JD code; no
candidate content), and the UI uses that audited route for drill-down.

## 14. Authorization and Leak Safety

| Surface | `hr` | `hr_lead` | `admin` |
|---|---:|---:|---:|
| Operations summary/usage | no | read | read |
| Quality release list/detail | read | read | read |
| Create quality release | no | yes | yes |
| Batch rejection report | read | read | read |
| Suspicious cross-check list | read | read | read |
| Cross-check backfill | no | no | yes |

No token gives `401`; an authenticated but disallowed role gives `403`.

Usage, metrics, releases, batch reports, and suspicious-list responses are
free of **candidate PII and candidate-authored content**. Release metadata may
return its authorized staff creator as `{user_id, display_name}` for
accountability; it never returns candidate names. Integration tests inspect
serialized response bodies for candidate names, `name_cipher`, contact fields,
object keys, prompt text, resume excerpts, evidence quotes, and reasoning.
Candidate IDs and score/job/trace IDs are operational references, not candidate
content.

## 15. Frontend Information Architecture and UX

WP7 changes `AppShell` for the whole authenticated app; existing routes and
permissions remain.

### 15.1 Global navigation

At desktop width (1024px and above), use a 190–220px grouped left sidebar:

- **招聘工作:** 候选人、上传简历
- **审核与规则:** 复核报告、黄金集、基线指标
- **运营与质量:** 用量与成本、质量发布、批次淘汰、双引擎异常

The top bar becomes contextual: breadcrumb, last refresh, and user menu. On
small screens, the sidebar becomes a Base UI/shadcn Sheet opened from a compact
header. Existing per-JD rule management remains contextual from JD/candidate
workflows rather than becoming an ambiguous global rule link.

### 15.2 Visual system

- Existing Geist sans/mono fonts; no new font download.
- Light operational workspace with one cobalt-blue interactive accent.
- Amber/red are reserved for warning/exceeded/error; green for healthy/success.
- Hierarchy comes from typography, alignment, whitespace, and thin dividers,
  not a grid of generic cards.
- IDs and numeric columns use tabular/mono treatment where useful.
- Lucide icons only; no emoji icons in the implementation.
- Micro-interactions are 150–200ms and honor `prefers-reduced-motion`.

### 15.3 Routes and page composition

- `/reports/operations`
  - status strip, four-metric rail, daily cost/anomaly trend, budget comparison,
    operation/model breakdown, and filterable usage ledger.
- `/reports/quality`
  - latest release first; aggregate metrics and target states; confidence bins,
    evidence coverage, agreement, trend measures, per-JD table, and immutable
    release history.
  - `hr_lead`/`admin` create via a right-side Sheet, inspect a preflight
    summary from the preview endpoint, then confirm immutability in a Dialog.
    The create call sends the preview fingerprint and refreshes the preview if
    inputs changed.
- `/reports/batch`
  - bounded filters, ranked deterministic reasons, grade distribution, and
    aggregate detail table.
- `/reports/cross-checks`
  - PII-free suspicious queue with a right-side inspector for score/dimension
    differences; link to the existing audited scorecard.
  - admin backfill uses a Sheet for filters/preview and a Dialog for final
    confirmation; preview calls `dry_run=true`.

### 15.4 Responsive and accessible behavior

- Desktop tables remain semantic `<table>` elements.
- On mobile, usage and suspicious records become expandable semantic lists;
  dense per-JD comparison tables use horizontal scrolling with a sticky first
  column.
- Filters move into a Sheet; active-filter count remains visible.
- Status always has text + icon + color.
- Charts expose an accessible name, exact values outside hover, and an adjacent
  anomaly/summary list. WP7 uses lightweight semantic HTML/CSS/SVG and does not
  add a charting dependency.
- Loading uses stable skeleton geometry; empty/error/retry use the existing
  `DataState` pattern.
- Sheet/Dialog focus is trapped while open, Escape closes, and focus returns to
  the invoking control. Visual order matches keyboard order.
- Validate at 375, 768, 1024, and 1440px.
- Server components that read cookies export
  `dynamic = "force-dynamic"`; Next 15 dynamic route params are awaited.
- Base UI buttons use `onClick`/`render`, never Radix `asChild`.

## 16. Runtime Configuration

New settings:

- `LLM_PRICE_CNY_PER_MILLION_JSON`
- `LLM_BUDGET_WARN_RATIO=0.80`
- `LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN=31`
- `LLM_USAGE_PENDING_TIMEOUT_SECONDS=600`
- `LLM_USAGE_FINALIZE_MAX_RETRIES=3`
- `QUALITY_F1_TARGET=0.75`
- `QUALITY_EVIDENCE_COVERAGE_TARGET=0.95`
- `QUALITY_CONFIDENCE_MIN_BUCKET_SIZE=10`
- `CROSS_ENGINE_MODEL=""` (empty disables cross-engine work)
- `CROSS_ENGINE_SAMPLE_PERCENT=10`
- `CROSS_ENGINE_LOW_CONFIDENCE=0.60`
- `CROSS_ENGINE_DIFF_THRESHOLD=10`
- `CROSS_ENGINE_MAX_ATTEMPTS=3`
- `CROSS_ENGINE_LEASE_SECONDS=900`
- `CROSS_ENGINE_SWEEP_INTERVAL_SECONDS=60`
- `CROSS_ENGINE_BACKFILL_MAX=500`

All numeric settings are validated for finite values and valid ranges. A
configured secondary model must have a price entry and differ from the primary
judge model. The cross-engine lease must exceed Celery's task time limit.
`.env.example`, deployment documentation, and test settings gain safe example
values; no secret or live provider price is committed.

## 17. Testing

Default tests remain offline and deterministic.

### 17.1 Backend unit tests

- Price-config validation (including rejecting more than six rate decimals) and
  immutable snapshot selection.
- Exact decimal cost calculation; unknown usage remains null.
- Half-open time attribution, prior equal-length windows, budget period
  boundaries in `Asia/Shanghai`, warning/exceeded state, and threshold
  dedupe-key construction.
- Weighted confidence, five-bin assignment, insufficient bucket handling, and
  ECE.
- Evidence coverage, including unknown dimensions, hard rejects, missing
  dimensions, and no-judge-dimension `not_applicable`.
- Classification and target rollup reuse WP6 metric semantics.
- Deterministic sampling stability, trigger merging, and score-difference
  threshold.
- Negative/non-finite rule weights are rejected; zero weights are safely
  excluded.
- Deterministic batch-reason aggregation with multi-reason percentages.

### 17.2 Backend integration tests

- Alembic upgrade/downgrade round trip and current-head assertions.
- Primary success, primary failure + fallback success, configuration failure,
  missing usage, and separate attempt rows with no prompts/PII.
- Call-group correlation links a pre-Score judge attempt to the created Score
  without mutating a terminal ledger row; secondary calls populate score ID.
- Transaction A failure proves the provider mock was not called and produces
  `usage_ledger_unavailable`.
- Finalization failure does not trigger a duplicate provider call; stale
  pending becomes abandoned.
- Budget yellow/red audit events are deduplicated under concurrent finalizers
  and do not block calls; reconciliation restores a deliberately missed event
  after multiple elapsed daily periods, advances its durable cursor, and a
  direct jump emits both thresholds.
- Operations endpoints aggregate exact price snapshots and enforce RBAC/
  pagination/filter bounds.
- Snapshot hashing is order-independent, identical content is reused, and
  concurrent release creation retries the whole transaction safely.
- Release preview performs no writes; matching fingerprint creates, while a
  changed input fingerprint returns `release_input_changed`.
- Release creation binds active rule versions under repeatable read, excludes
  borderline, marks wrong-version scores uncovered, saves `below_target`, and
  returns 409 for empty golden set/missing active rule.
- Release creation atomically writes its created/below-target audit events, and
  rollback leaves neither release nor audit residue.
- Confidence/evidence/agreement results have expected denominators and no
  evidence/PII response fields.
- Latency percentiles and deterministic sample selection match the specified
  database/hash algorithms exactly.
- Cross-check insertion is idempotent from score, golden import, and feedback;
  the trigger row commits atomically with each source write; lost delivery is
  recovered; duplicate task delivery produces one provider call; lease tokens
  reject stale completion; retries/terminal classification are correct; and an
  older delayed model/prompt row cannot overwrite the newest Score projection.
- Admin backfill is bounded/idempotent; unauthorized roles fail.
- Backfill dry-run performs no writes and matches the subsequent confirmed
  selection when inputs are unchanged.
- Batch analysis is aggregate, deterministic, bounded, and PII-free.
- Suspicious drill-down writes `score_detail_read` audit metadata.

### 17.3 Frontend tests

- Vitest for the responsive app shell, role-visible navigation/actions,
  operations states, release metrics/targets, Sheet/Dialog workflows, batch
  reasons, suspicious inspector, schemas, and error/empty/loading states.
- Playwright desktop + mobile for:
  - operations budget/ledger filtering;
  - below-target release creation and immutable detail;
  - batch analysis;
  - suspicious cross-check inspection and admin backfill;
  - keyboard focus return and axe checks; and
  - response/rendered-content assertions that no PII/token/prompt leaks.

### 17.4 Full gate

- Backend offline and real-service integration suites.
- Ruff and mypy.
- Frontend lint, typecheck, Vitest, Playwright desktop/mobile, and production
  build.
- Hosted backend CI must pass before WP7 is marked Complete.

## 18. Rollout, Recovery, and Compatibility

- Migration is additive. Existing scores, feedback, golden entries, rules, and
  routes remain valid.
- Deploy migration/application before enabling a secondary model.
- From rollout onward, all gateway calls require price configuration and a
  pending ledger row. Startup/config tests catch missing configured-model
  prices before normal traffic.
- Historic operations cost before ledger rollout is shown as unavailable, not
  estimated from legacy score fields.
- Cross-engine starts with new eligible scores only; bounded admin backfill is
  optional.
- Disabling `CROSS_ENGINE_MODEL` stops new sampling and worker calls but leaves
  existing completed results readable.
- A previous application image can read existing core tables after rollback;
  Alembic downgrade removes WP7 tables. Existing `Score.cross_engine_diff` and
  `is_suspicious` projections may remain populated and are safe for the old
  application.

## 19. Exit Criteria

WP7 is complete when:

- every primary/fallback/secondary/lightweight provider attempt after rollout
  is represented by a PII-free ledger row or is proven not to have been called
  because pre-call accounting failed;
- CNY cost uses immutable price snapshots; daily/monthly normal/warning/
  exceeded states and deduplicated audit alerts are tested, and thresholds do
  not block calls;
- a curator can create and read an immutable release bound to a content-hashed
  golden snapshot and exact active rule versions;
- the release records measured F1, evidence coverage, descriptive confidence
  reliability, AI-HR agreement, latency, throughput, and cost/trends, with
  target results saved even when below target;
- optional cross-engine sampling/recovery and bounded backfill populate the
  existing Score projection without exposing PII or replacing the primary
  grade;
- batch rejection analysis is deterministic and aggregate;
- the desktop/mobile operations-and-quality workspace is accessible and
  role-correct; and
- all local backend/frontend gates and hosted backend CI pass.

After implementation and local gates, documentation must mark WP7 **In
progress** until hosted CI and merge evidence are recorded. Only then may WP7
be marked Complete and WP9's quality-measurement dependency be considered
satisfied.

## 20. Approval

Approval means this design may be converted into a task-by-task TDD
implementation plan. It does not authorize implementation, push, PR creation,
or marking WP7 Complete.
