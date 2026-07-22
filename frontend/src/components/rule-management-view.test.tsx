import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RuleManagementView } from "@/components/rule-management-view";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

const LIST = {
  items: [
    {
      id: 1,
      version: "v1",
      status: "published",
      published_at: "2026-07-23T00:00:00Z",
      published_by_user_id: null,
      notes: null,
      golden_set_metrics: null,
      is_active: true,
    },
    {
      id: 2,
      version: "v2",
      status: "draft",
      published_at: null,
      published_by_user_id: null,
      notes: null,
      golden_set_metrics: null,
      is_active: false,
    },
  ],
  page: 1,
  page_size: 20,
  total: 2,
};

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("RuleManagementView", () => {
  it("hides the create-draft form when canManage is false", async () => {
    global.fetch = vi.fn(
      async () => new Response(JSON.stringify(LIST), { status: 200 }),
    ) as unknown as typeof fetch;

    render(wrap(<RuleManagementView code="FT" canManage={false} />));

    expect(await screen.findByText("v2")).toBeInTheDocument();
    expect(screen.queryByLabelText("规则 schema JSON")).not.toBeInTheDocument();
  });

  it("shows the create-draft form when canManage is true", async () => {
    global.fetch = vi.fn(
      async () => new Response(JSON.stringify(LIST), { status: 200 }),
    ) as unknown as typeof fetch;

    render(wrap(<RuleManagementView code="FT" canManage={true} />));

    expect(await screen.findByLabelText("规则 schema JSON")).toBeInTheDocument();
  });
});
