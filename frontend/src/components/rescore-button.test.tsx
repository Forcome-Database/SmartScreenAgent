import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RescoreButton } from "@/components/rescore-button";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

function wrap(ui: React.ReactNode) {
  const client = new QueryClient();
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("RescoreButton", () => {
  it("POSTs the re-score endpoint through the proxy", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe("/api/proxy/api/v1/candidates/7/score");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body as string)).toEqual({ jd_code: "FT" });
      return new Response(
        JSON.stringify({ score_id: 9, total_score: 82, grade: "L4", rejected: false }),
        { status: 200 },
      );
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    render(wrap(<RescoreButton candidateId={7} jdCode="FT" />));
    await userEvent.click(screen.getByRole("button", { name: /重新评分/ }));
    expect(fetchMock).toHaveBeenCalled();
  });
});
