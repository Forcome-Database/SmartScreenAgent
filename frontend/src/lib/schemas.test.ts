import { describe, expect, it } from "vitest";
import {
  BatchResponse,
  BatchStatus,
  JobStatus,
  UploadResponse,
  isTerminalJobState,
} from "@/lib/schemas";

describe("JobStatus", () => {
  it("parses the real backend job-status shape", () => {
    const parsed = JobStatus.parse({
      state: "ready",
      attempts: 1,
      last_error_code: null,
      candidate_id: 7,
      score_id: 9,
      batch_id: null,
    });
    expect(parsed.state).toBe("ready");
  });

  it("rejects the old wrong shape (missing required fields)", () => {
    const result = JobStatus.safeParse({ job_id: "x", state: "ready" });
    expect(result.success).toBe(false);
  });
});

describe("BatchStatus", () => {
  it("parses the real backend batch-status shape", () => {
    const parsed = BatchStatus.parse({ total: 3, by_state: { ready: 2, queued: 1 } });
    expect(parsed.total).toBe(3);
  });
});

describe("UploadResponse", () => {
  it("parses job_id as a number", () => {
    const parsed = UploadResponse.parse({ job_id: 5, batch_id: null, state: "queued" });
    expect(parsed.job_id).toBe(5);
  });
});

describe("BatchResponse", () => {
  it("parses jobs with nullable job_id", () => {
    const parsed = BatchResponse.parse({
      batch_id: "b1",
      jobs: [
        { job_id: 5, state: "queued" },
        { job_id: null, state: "terminal_failed", error_code: "invalid_pdf" },
      ],
    });
    expect(parsed.jobs).toHaveLength(2);
  });
});

describe("isTerminalJobState", () => {
  it("treats retryable_failed as non-terminal (the sweeper still retries it)", () => {
    expect(isTerminalJobState("retryable_failed")).toBe(false);
  });

  it("treats ready and terminal_failed as terminal", () => {
    expect(isTerminalJobState("ready")).toBe(true);
    expect(isTerminalJobState("terminal_failed")).toBe(true);
  });
});
