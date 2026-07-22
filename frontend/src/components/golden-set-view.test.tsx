import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GoldenSetView } from "@/components/golden-set-view";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("GoldenSetView", () => {
  it("hides the import control when canImport is false", async () => {
    global.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
          status: 200,
        }),
    ) as unknown as typeof fetch;
    render(wrap(<GoldenSetView canImport={false} />));
    expect(await screen.findByText(/黄金集/)).toBeInTheDocument();
    expect(screen.queryByLabelText("导入 CSV")).not.toBeInTheDocument();
  });

  it("shows the import control when canImport is true", async () => {
    global.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
          status: 200,
        }),
    ) as unknown as typeof fetch;
    render(wrap(<GoldenSetView canImport={true} />));
    expect(await screen.findByLabelText("导入 CSV")).toBeInTheDocument();
  });
});
