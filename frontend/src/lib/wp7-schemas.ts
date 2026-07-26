import { z } from "zod";

/**
 * WP7 wire contracts.
 *
 * Two deliberate and DIFFERENT numeric conventions live here:
 *  - operations + cross-engine money/latency/diff values are JSON **strings**,
 *    because exact decimal cost must survive the wire without float rounding;
 *  - quality ratios, targets, bin statistics, and batch percentages are JSON
 *    **numbers** bounded 0..1 (or a plain count share).
 * Unifying them would silently corrupt one side or the other.
 */

const decimalString = z.string();
const nullableDecimalString = z.string().nullable();
const nullableNumber = z.number().nullable();

export const OperationsTotals = z.object({
  attempt_count: z.number(),
  known_cost_cny: decimalString,
  known_token_total: z.number(),
  unknown_usage_count: z.number(),
  succeeded_count: z.number(),
  failed_count: z.number(),
  abandoned_count: z.number(),
  pending_count: z.number(),
  p50_latency_ms: nullableDecimalString,
  p95_latency_ms: nullableDecimalString,
  last_completed_at: z.string().nullable(),
});

export const OperationsDelta = z.object({
  absolute: nullableDecimalString,
  percentage: nullableDecimalString,
});

export const OperationsBreakdown = z.object({
  key: z.string(),
  attempt_count: z.number(),
  known_cost_cny: decimalString,
  unknown_usage_count: z.number(),
});

export const BudgetSnapshot = z.object({
  scope: z.enum(["daily", "monthly"]),
  period_start: z.string(),
  period_end: z.string(),
  budget_cny: decimalString,
  spend_cny: decimalString,
  ratio: nullableDecimalString,
  unknown_cost_count: z.number(),
  state: z.enum(["normal", "warning", "exceeded"]),
});

export const OperationsSeriesPoint = z.object({
  local_date: z.string(),
  attempt_count: z.number(),
  known_cost_cny: decimalString,
  unknown_usage_count: z.number(),
});

export const OperationsSummary = z.object({
  window: z.enum(["today", "7d", "30d"]),
  current_start: z.string(),
  current_end: z.string(),
  previous_start: z.string(),
  previous_end: z.string(),
  current: OperationsTotals,
  previous: OperationsTotals,
  cost_delta: OperationsDelta,
  attempt_delta: OperationsDelta,
  daily_series: z.array(OperationsSeriesPoint),
  by_operation: z.array(OperationsBreakdown),
  by_requested_model: z.array(OperationsBreakdown),
  by_actual_model: z.array(OperationsBreakdown),
  by_outcome: z.array(OperationsBreakdown),
  by_attempt_role: z.array(OperationsBreakdown),
  budgets: z.array(BudgetSnapshot),
});

export const UsageItem = z.object({
  id: z.number(),
  call_group_id: z.string(),
  trace_id: z.string().nullable(),
  ingestion_job_id: z.number().nullable(),
  score_id: z.number().nullable(),
  jd_id: z.number().nullable(),
  rule_version_id: z.number().nullable(),
  operation: z.string(),
  attempt_role: z.string(),
  requested_model: z.string(),
  actual_model: z.string().nullable(),
  prompt_version: z.string(),
  status: z.string(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  input_price_cny_per_million: decimalString,
  output_price_cny_per_million: decimalString,
  estimated_cost_cny: nullableDecimalString,
  latency_ms: z.number().nullable(),
  error_code: z.string().nullable(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
});

export const UsagePage = z.object({
  items: z.array(UsageItem),
  page: z.number(),
  page_size: z.number(),
  total: z.number(),
});

const TargetResult = z.object({
  value: nullableNumber,
  target: z.number(),
  status: z.enum([
    "meets_target",
    "below_target",
    "insufficient_data",
    "not_applicable",
  ]),
});

const ClassificationMetrics = z.object({
  labeled_total: z.number(),
  covered: z.number(),
  uncovered: z.number(),
  borderline_excluded: z.number(),
  confusion: z.object({
    tp: z.number(),
    fp: z.number(),
    tn: z.number(),
    fn: z.number(),
  }),
  precision: nullableNumber,
  recall: nullableNumber,
  f1: nullableNumber,
  accuracy: nullableNumber,
});

const EvidenceMetrics = z.object({
  participating_candidates: z.number(),
  hard_filter_rejects: z.number(),
  expected_count: z.number(),
  covered_count: z.number(),
  value: nullableNumber,
  status: z.enum(["ok", "insufficient_data", "not_applicable"]),
});

export const ConfidenceBin = z.object({
  lower: z.number(),
  upper: z.number(),
  upper_inclusive: z.boolean(),
  count: z.number(),
  mean_confidence: nullableNumber,
  decision_accuracy: nullableNumber,
  absolute_gap: nullableNumber,
  status: z.enum(["ok", "insufficient_data"]),
});

const ConfidenceMetrics = z.object({
  available_count: z.number(),
  confidence_unavailable: z.number(),
  bins: z.array(ConfidenceBin),
  ece: nullableNumber,
});

const AgreementMetrics = z.object({
  agreed: z.number(),
  disagreed: z.number(),
  hold: z.number(),
  denominator: z.number(),
  agreement_rate: nullableNumber,
});

const ReleaseOperationTotals = z.object({
  attempt_count: z.number(),
  succeeded_count: z.number(),
  failed_count: z.number(),
  abandoned_count: z.number(),
  unknown_usage_count: z.number(),
  known_cost_cny: z.number(),
  p50_latency_ms: nullableNumber,
  p95_latency_ms: nullableNumber,
  scored_count: z.number(),
  scores_per_day: nullableNumber,
});

const ReleaseOperationDelta = z.object({
  absolute: nullableNumber,
  percentage: nullableNumber,
});

export const QualityReleasePreview = z.object({
  window_start: z.string(),
  window_end: z.string(),
  selected: z.array(
    z.object({
      jd_id: z.number(),
      jd_code: z.string(),
      rule_version_id: z.number(),
    }),
  ),
  golden_total: z.number(),
  golden_advance: z.number(),
  golden_reject: z.number(),
  golden_borderline: z.number(),
  score_covered: z.number(),
  score_uncovered: z.number(),
  targets: z.record(z.string(), z.unknown()),
  input_fingerprint: z.string(),
});

const ReleaseCreator = z.object({
  user_id: z.number(),
  display_name: z.string(),
});

export const QualityReleaseDetail = z.object({
  id: z.number(),
  status: z.enum(["meets_target", "below_target"]),
  golden_snapshot_sha256: z.string(),
  golden_snapshot_item_count: z.number(),
  window_start: z.string(),
  window_end: z.string(),
  created_at: z.string(),
  created_by: ReleaseCreator,
  targets: z.record(z.string(), z.unknown()),
  classification: ClassificationMetrics,
  evidence: EvidenceMetrics,
  confidence: ConfidenceMetrics,
  agreement: AgreementMetrics,
  f1_target_result: TargetResult,
  evidence_target_result: TargetResult,
  operations: z.object({
    current: ReleaseOperationTotals,
    previous: ReleaseOperationTotals,
    cost_delta: ReleaseOperationDelta,
    attempt_delta: ReleaseOperationDelta,
  }),
  by_jd: z.array(z.record(z.string(), z.unknown())),
});

export const QualityReleaseList = z.object({
  items: z.array(
    z.object({
      id: z.number(),
      status: z.enum(["meets_target", "below_target"]),
      window_start: z.string(),
      window_end: z.string(),
      created_at: z.string(),
      created_by: ReleaseCreator,
      golden_snapshot_sha256: z.string(),
    }),
  ),
  page: z.number(),
  page_size: z.number(),
  total: z.number(),
});

export const BatchRejectionReport = z.object({
  filters: z.object({
    batch_id: z.string().nullable(),
    jd_code: z.string().nullable(),
  }),
  window_start: z.string(),
  window_end: z.string(),
  total_scored: z.number(),
  total_rejected: z.number(),
  grade_counts: z.record(z.string(), z.number()),
  reasons: z.array(
    z.object({
      reason_type: z.string(),
      reason_key: z.string(),
      occurrences: z.number(),
      affected_scores: z.number(),
      percentage: nullableNumber,
    }),
  ),
  percentages_may_overlap: z.boolean(),
});

export const SuspiciousPage = z.object({
  items: z.array(
    z.object({
      cross_check_id: z.number(),
      score_id: z.number(),
      candidate_id: z.number(),
      jd_code: z.string(),
      primary_total_score: nullableDecimalString,
      secondary_total_score: nullableDecimalString,
      absolute_diff: nullableDecimalString,
      threshold: nullableDecimalString,
      secondary_dimensions: z.array(z.record(z.string(), z.unknown())),
      sample_reasons: z.array(z.string()),
      secondary_model: z.string(),
      completed_at: z.string().nullable(),
    }),
  ),
  page: z.number(),
  page_size: z.number(),
  total: z.number(),
});

export const BackfillResult = z.object({
  dry_run: z.boolean(),
  selected: z.number(),
  already_existing: z.number(),
  would_queue: z.number(),
  newly_queued: z.number(),
});

export type OperationsSummary = z.infer<typeof OperationsSummary>;
export type UsagePage = z.infer<typeof UsagePage>;
export type QualityReleasePreview = z.infer<typeof QualityReleasePreview>;
export type QualityReleaseDetail = z.infer<typeof QualityReleaseDetail>;
export type QualityReleaseList = z.infer<typeof QualityReleaseList>;
export type BatchRejectionReport = z.infer<typeof BatchRejectionReport>;
export type SuspiciousPage = z.infer<typeof SuspiciousPage>;
export type BackfillResult = z.infer<typeof BackfillResult>;
