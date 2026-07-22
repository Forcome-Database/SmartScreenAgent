import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FeedbackPanel } from "@/components/feedback-panel";

const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("FeedbackPanel", () => {
  it("requires a reason before submitting a disagreement-capable verdict", async () => {
    global.fetch = vi.fn(async (url: string) => {
      if (url.includes("/feedback")) return new Response(JSON.stringify([]), { status: 200 });
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    render(wrap(<FeedbackPanel candidateId={7} scoreId={9} aiRejected={false} />));
    // choose reject (disagrees with AI advance) then submit without reason → blocked client-side
    await userEvent.click(await screen.findByRole("button", { name: "淘汰" }));
    await userEvent.click(screen.getByRole("button", { name: /提交/ }));
    expect(screen.getByText(/请填写理由/)).toBeInTheDocument();
  });
});
