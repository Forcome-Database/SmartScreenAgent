import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReportPage from "./page";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

const STATS = {
  labeled_total: 4,
  scored: 3,
  uncovered: 1,
  borderline_excluded: 1,
  confusion: { tp: 1, fp: 1, tn: 0, fn: 0 },
  precision: 0.5,
  recall: 1,
  f1: 0.6667,
  accuracy: 0.5,
};

describe("Baseline metrics report page", () => {
  it("renders the overall precision", async () => {
    global.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ overall: STATS, by_jd: [{ ...STATS, jd_code: "FT" }] }),
          { status: 200 },
        ),
    ) as unknown as typeof fetch;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ReportPage />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("50%", { selector: "span" })).toBeInTheDocument());
  });
});
