import { describe, expect, it } from "vitest";
import {
  BackfillResult,
  BatchRejectionReport,
  OperationsSummary,
  QualityReleaseDetail,
  SuspiciousPage,
  UsagePage,
} from "@/lib/wp7-schemas";

const totals = {
  attempt_count: 4,
  known_cost_cny: "2.000000000000",
  known_token_total: 20,
  unknown_usage_count: 2,
  succeeded_count: 2,
  failed_count: 1,
  abandoned_count: 0,
  pending_count: 1,
  p50_latency_ms: "200",
  p95_latency_ms: null,
  last_completed_at: "2026-07-26T05:00:00Z",
};

const summary = {
  window: "7d",
  current_start: "2026-07-19T05:00:00Z",
  current_end: "2026-07-26T05:00:00Z",
  previous_start: "2026-07-12T05:00:00Z",
  previous_end: "2026-07-19T05:00:00Z",
  current: totals,
  previous: { ...totals, attempt_count: 1 },
  cost_delta: { absolute: "-2.000000000000", percentage: "-50" },
  attempt_delta: { absolute: "3", percentage: "300" },
  daily_series: [
    {
      local_date: "2026-07-25",
      attempt_count: 4,
      known_cost_cny: "2",
      unknown_usage_count: 2,
    },
  ],
  by_operation: [
    { key: "judge", attempt_count: 3, known_cost_cny: "1.5", unknown_usage_count: 1 },
  ],
  by_requested_model: [],
  by_actual_model: [
    { key: "(unknown)", attempt_count: 1, known_cost_cny: "0", unknown_usage_count: 1 },
  ],
  by_outcome: [],
  by_attempt_role: [],
  budgets: [
    {
      scope: "daily",
      period_start: "2026-07-25T16:00:00Z",
      period_end: "2026-07-26T16:00:00Z",
      budget_cny: "100",
      spend_cny: "2",
      ratio: "0.02",
      unknown_cost_count: 2,
      state: "normal",
    },
  ],
};

describe("operations contracts", () => {
  it("keeps decimal money and latency as exact strings", () => {
    const parsed = OperationsSummary.parse(summary);

    expect(parsed.current.known_cost_cny).toBe("2.000000000000");
    expect(parsed.current.p50_latency_ms).toBe("200");
    // Counts stay numeric; only decimals are stringly typed.
    expect(parsed.current.attempt_count).toBe(4);
  });

  it("preserves nulls rather than coercing them to zero", () => {
    const parsed = OperationsSummary.parse(summary);

    expect(parsed.current.p95_latency_ms).toBeNull();
    expect(parsed.current.last_completed_at).not.toBeNull();
  });

  it("rejects a payload missing required metadata", () => {
    const withoutWindow = { ...summary };
    delete (withoutWindow as Partial<typeof summary>).window;

    expect(() => OperationsSummary.parse(withoutWindow)).toThrow();
  });

  it("strips any key the contract does not declare", () => {
    const parsed = UsagePage.parse({
      items: [
        {
          id: 1,
          call_group_id: "8d1b0f2e-0000-4000-8000-000000000000",
          trace_id: "t-1",
          ingestion_job_id: null,
          score_id: null,
          jd_id: null,
          rule_version_id: null,
          operation: "judge",
          attempt_role: "primary",
          requested_model: "test-judge",
          actual_model: null,
          prompt_version: "resume_judge_v1",
          status: "succeeded",
          input_tokens: 10,
          output_tokens: 5,
          input_price_cny_per_million: "1.000000",
          output_price_cny_per_million: "2.000000",
          estimated_cost_cny: "0.000020000000",
          latency_ms: 12,
          error_code: null,
          started_at: "2026-07-26T05:00:00Z",
          finished_at: null,
          candidate_name: "private-name",
          object_key: "resumes/secret",
        },
      ],
      page: 1,
      page_size: 20,
      total: 1,
    });

    expect(parsed.items[0]).not.toHaveProperty("candidate_name");
    expect(parsed.items[0]).not.toHaveProperty("object_key");
    expect(JSON.stringify(parsed)).not.toContain("private-name");
  });
});

describe("quality contracts", () => {
  const releaseTotals = {
    attempt_count: 2,
    succeeded_count: 2,
    failed_count: 0,
    abandoned_count: 0,
    unknown_usage_count: 0,
    known_cost_cny: 2,
    p50_latency_ms: 200,
    p95_latency_ms: 290,
    scored_count: 1,
    scores_per_day: 0.5,
  };

  const detail = {
    id: 1,
    status: "below_target",
    golden_snapshot_sha256: "a".repeat(64),
    golden_snapshot_item_count: 2,
    window_start: "2026-06-26T05:00:00Z",
    window_end: "2026-07-26T05:00:00Z",
    created_at: "2026-07-26T05:00:00Z",
    created_by: { user_id: 7, display_name: "Reviewer" },
    targets: { f1_target: 0.75 },
    classification: {
      labeled_total: 2,
      covered: 2,
      uncovered: 0,
      borderline_excluded: 0,
      confusion: { tp: 1, fp: 0, tn: 1, fn: 0 },
      precision: 1,
      recall: 1,
      f1: 1,
      accuracy: 1,
    },
    evidence: {
      participating_candidates: 2,
      hard_filter_rejects: 0,
      expected_count: 2,
      covered_count: 1,
      value: 0.5,
      status: "ok",
    },
    confidence: {
      available_count: 2,
      confidence_unavailable: 0,
      bins: [],
      ece: null,
    },
    agreement: {
      agreed: 1,
      disagreed: 0,
      hold: 0,
      denominator: 1,
      agreement_rate: 1,
    },
    f1_target_result: { value: 1, target: 0.75, status: "meets_target" },
    evidence_target_result: { value: 0.5, target: 0.95, status: "below_target" },
    operations: {
      current: releaseTotals,
      previous: releaseTotals,
      cost_delta: { absolute: 0, percentage: null },
      attempt_delta: { absolute: 0, percentage: null },
    },
    by_jd: [],
  };

  it("reads ratios and targets as JSON numbers, not strings", () => {
    const parsed = QualityReleaseDetail.parse(detail);

    expect(parsed.classification.f1).toBe(1);
    expect(parsed.evidence.value).toBe(0.5);
    expect(parsed.f1_target_result.target).toBe(0.75);
    // Release operation amounts are numbers here, unlike the operations API.
    expect(parsed.operations.current.known_cost_cny).toBe(2);
  });

  it("keeps a null ECE and a null delta percentage", () => {
    const parsed = QualityReleaseDetail.parse(detail);

    expect(parsed.confidence.ece).toBeNull();
    expect(parsed.operations.cost_delta.percentage).toBeNull();
  });
});

describe("batch and cross-engine contracts", () => {
  it("reads batch percentages as numbers", () => {
    const parsed = BatchRejectionReport.parse({
      filters: { batch_id: null, jd_code: "TRADE" },
      window_start: "2026-06-26T05:00:00Z",
      window_end: "2026-07-26T05:00:00Z",
      total_scored: 3,
      total_rejected: 2,
      grade_counts: { rejected: 2, L1: 1 },
      reasons: [
        {
          reason_type: "hard_filter",
          reason_key: "no_degree",
          occurrences: 2,
          affected_scores: 2,
          percentage: 100,
        },
      ],
      percentages_may_overlap: true,
    });

    expect(parsed.reasons[0].percentage).toBe(100);
    expect(parsed.percentages_may_overlap).toBe(true);
  });

  it("reads cross-engine totals and diffs as exact strings", () => {
    const parsed = SuspiciousPage.parse({
      items: [
        {
          cross_check_id: 1,
          score_id: 2,
          candidate_id: 3,
          jd_code: "TRADE",
          primary_total_score: "70.00",
          secondary_total_score: "40.00",
          absolute_diff: "30.00",
          threshold: "10.00",
          secondary_dimensions: [{ id: "independence", score: 1 }],
          sample_reasons: ["deterministic_sample"],
          secondary_model: "test-secondary",
          completed_at: "2026-07-26T05:00:00Z",
        },
      ],
      page: 1,
      page_size: 20,
      total: 1,
    });

    expect(parsed.items[0].absolute_diff).toBe("30.00");
    expect(parsed.items[0].cross_check_id).toBe(1);
  });

  it("parses backfill counts", () => {
    const parsed = BackfillResult.parse({
      dry_run: true,
      selected: 3,
      already_existing: 1,
      would_queue: 2,
      newly_queued: 0,
    });

    expect(parsed.would_queue).toBe(2);
  });
});
