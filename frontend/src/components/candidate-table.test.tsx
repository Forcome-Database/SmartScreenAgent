import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CandidateTable } from "@/components/candidate-table";

describe("CandidateTable", () => {
  const rows = [
    { candidate_id: 7, created_at: "2026-07-21T00:00:00Z", latest_state: "ready", scored_jd_codes: ["FT"] },
  ];
  it("renders id, state, and jd codes but no PII", () => {
    render(<CandidateTable rows={rows} />);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("FT")).toBeInTheDocument();
    // no PII column headers
    expect(screen.queryByText(/姓名|手机|邮箱|name|phone|email/i)).toBeNull();
  });
});
