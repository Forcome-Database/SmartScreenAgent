import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RankedTable } from "@/components/ranked-table";

describe("RankedTable", () => {
  const oneRow = [
    {
      candidate_id: 7,
      score_id: 100,
      total_score: 88.5,
      grade: "A",
      rule_version: "v1",
      scored_at: "2026-07-21T00:00:00Z",
    },
  ];

  it("shows global rank when startRank is provided (page 2, page_size 20)", () => {
    render(<RankedTable rows={oneRow} startRank={20} />);
    expect(screen.getByText("21")).toBeInTheDocument();
  });

  it("defaults to page-1 rank when startRank is omitted", () => {
    render(<RankedTable rows={oneRow} />);
    expect(screen.getByText("1")).toBeInTheDocument();
  });
});
