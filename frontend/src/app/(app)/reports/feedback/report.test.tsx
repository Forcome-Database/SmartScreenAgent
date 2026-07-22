import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReportPage from "./page";

const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });

describe("Feedback report page", () => {
  it("renders the overall agreement rate", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify({
      overall: { total: 4, agreed: 3, disagreed: 1, hold: 0, agreement_rate: 0.75 },
      by_jd: [{ jd_code: "FT", total: 4, agreed: 3, disagreed: 1, hold: 0, agreement_rate: 0.75 }],
      disagreements: { items: [], page: 1, page_size: 20, total: 0 },
    }), { status: 200 })) as unknown as typeof fetch;
    render(<QueryClientProvider client={new QueryClient()}><ReportPage /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByText("75%", { selector: "span" })).toBeInTheDocument());
  });
});
