import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("shows the draft-vs-baseline comparison and approximation warning", async () => {
    const evaluation = {
      draft: {
        confusion: { tp: 2, fp: 0, tn: 1, fn: 1 },
        precision: 1,
        recall: 0.6667,
        f1: 0.8,
        accuracy: 0.75,
        evaluated: 4,
        indeterminate: 0,
        borderline_excluded: 0,
        uncovered: 0,
      },
      baseline: {
        confusion: { tp: 1, fp: 1, tn: 1, fn: 1 },
        precision: 0.5,
        recall: 0.5,
        f1: 0.5,
        accuracy: 0.5,
        evaluated: 4,
        indeterminate: 0,
        borderline_excluded: 0,
        uncovered: 0,
      },
      judge_dimensions_changed: true,
    };
    global.fetch = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("/evaluate")) {
        return new Response(JSON.stringify(evaluation), { status: 200 });
      }
      return new Response(JSON.stringify(LIST), { status: 200 });
    }) as unknown as typeof fetch;

    render(wrap(<RuleManagementView code="FT" canManage={true} />));

    const publish = await screen.findByRole("button", { name: "发布 v2" });
    expect(publish).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "评估 v2" }));
    expect(await screen.findByText("草稿 F1")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText(/结果为近似值/)).toBeInTheDocument();
  });
});
