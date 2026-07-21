import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobStatusView } from "@/components/job-status";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

function wrap(ui: React.ReactNode) {
  const client = new QueryClient();
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

// Full JobStatus shape — the real backend (and the Task 2 schema) requires
// attempts/last_error_code/candidate_id/score_id/batch_id, not just job_id/state.
function jobBody(state: string) {
  return {
    state,
    attempts: 1,
    last_error_code: null,
    candidate_id: null,
    score_id: null,
    batch_id: null,
  };
}

describe("JobStatusView", () => {
  it("polls until a terminal state then shows it", async () => {
    const states = ["parsing", "extracting", "ready"];
    let i = 0;
    global.fetch = vi.fn(async () => {
      const state = states[Math.min(i, states.length - 1)];
      i += 1;
      return new Response(JSON.stringify(jobBody(state)), { status: 200 });
    }) as unknown as typeof fetch;
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <JobStatusView jobId="j1" pollMs={10} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/ready/)).toBeInTheDocument(), { timeout: 2000 });
  });

  it("keeps polling through retryable_failed instead of stopping", async () => {
    const states = ["retryable_failed", "retryable_failed", "ready"];
    let i = 0;
    const fetchMock = vi.fn(async () => {
      const state = states[Math.min(i, states.length - 1)];
      i += 1;
      return new Response(JSON.stringify(jobBody(state)), { status: 200 });
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    render(wrap(<JobStatusView jobId="j2" pollMs={10} />));
    await waitFor(() => expect(screen.getByText(/ready/)).toBeInTheDocument(), { timeout: 2000 });
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
