import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Scorecard } from "@/components/scorecard";

describe("Scorecard", () => {
  it("renders grade, total, and judge evidence quotes", () => {
    render(
      <Scorecard
        detail={{
          score_id: 1,
          candidate_id: 7,
          jd_code: "FT",
          rule_version: "v1",
          total_score: 82,
          grade: "L4",
          hard_filter_result: { passed: true },
          rule_dimensions: { trade: { score: 20 } },
          judge_dimensions: {
            independence: { score: 8, evidence_quotes: ["独立负责北美大客户开发"], reasoning: "强" },
          },
        }}
      />,
    );
    expect(screen.getByText("L4")).toBeInTheDocument();
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getByText("独立负责北美大客户开发")).toBeInTheDocument();
  });

  it("renders hard-filter rejection reason", () => {
    render(
      <Scorecard
        detail={{
          score_id: 2,
          candidate_id: 8,
          jd_code: "FT",
          rule_version: "v1",
          total_score: 0,
          grade: "REJECTED",
          hard_filter_result: {
            rejected: true,
            failed_filter_ids: ["age_max"],
            audit_entries: [{ filter_id: "age_max", audit_tag: "age", rule: "age <= 45" }],
          },
          rule_dimensions: {},
          judge_dimensions: null,
        }}
      />,
    );
    expect(screen.getByText("拒绝")).toBeInTheDocument();
    expect(screen.getByText(/age <= 45/)).toBeInTheDocument();
  });
});
