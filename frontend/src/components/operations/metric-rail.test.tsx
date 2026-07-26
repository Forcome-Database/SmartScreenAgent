import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BudgetStatus } from "@/components/operations/budget-status";
import {
  MetricRail,
  cny,
  ratioPercent,
  signedPercent,
} from "@/components/operations/metric-rail";

describe("decimal formatting", () => {
  it("formats an exact decimal string without float rounding", () => {
    // Number("2.000000000000") would be fine, but "0.1" + "0.2" arithmetic in
    // JS is not — so the string is trimmed, never parsed, for display.
    expect(cny("2.000000000000")).toBe("¥2");
    expect(cny("1.2345678")).toBe("¥1.2345");
    expect(cny("100")).toBe("¥100");
  });

  it("renders an unknown amount as a dash rather than zero", () => {
    expect(cny(null)).toBe("—");
    expect(ratioPercent(null)).toBe("—");
    expect(signedPercent(null)).toBe("—");
  });

  it("signs a delta so a rise and a fall are distinguishable", () => {
    expect(signedPercent("300")).toBe("+300%");
    expect(signedPercent("-50")).toBe("-50%");
  });
});

describe("MetricRail", () => {
  it("pairs each value with its label and hint", () => {
    render(
      <MetricRail
        metrics={[{ label: "已知成本", value: "¥2", hint: "环比 +10%" }]}
      />,
    );

    expect(screen.getByText("已知成本")).toBeInTheDocument();
    expect(screen.getByText("¥2")).toBeInTheDocument();
    expect(screen.getByText("环比 +10%")).toBeInTheDocument();
  });
});

describe("BudgetStatus", () => {
  const budget = {
    scope: "daily" as const,
    period_start: "2026-07-25T16:00:00Z",
    period_end: "2026-07-26T16:00:00Z",
    budget_cny: "100",
    spend_cny: "85",
    ratio: "0.85",
    unknown_cost_count: 0,
    state: "warning" as const,
  };

  it("states the budget condition in words, not only colour", () => {
    render(<BudgetStatus budgets={[budget]} />);

    expect(screen.getByText("接近预算")).toBeInTheDocument();
    expect(screen.getByText("¥85 / ¥100（85%）")).toBeInTheDocument();
  });

  it("discloses spend that could not be priced", () => {
    render(<BudgetStatus budgets={[{ ...budget, unknown_cost_count: 3 }]} />);

    expect(screen.getByText(/3 次调用用量未知/)).toBeInTheDocument();
  });

  it("labels an exceeded budget distinctly", () => {
    render(<BudgetStatus budgets={[{ ...budget, state: "exceeded" }]} />);

    expect(screen.getByText("已超预算")).toBeInTheDocument();
  });
});
