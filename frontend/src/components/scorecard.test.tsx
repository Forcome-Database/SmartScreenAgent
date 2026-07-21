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
});
