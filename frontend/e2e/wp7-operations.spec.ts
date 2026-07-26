import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mintSession } from "./helpers/session";

// Covers the WP7 operations workspace (/reports/operations) and the shared
// responsive shell, following the same stubbing pattern as the WP6 specs: a
// real HMAC-signed `ssa_session` cookie plus page.route stubs on the BFF proxy
// (Playwright only intercepts browser-issued requests, so the upstream FastAPI
// call is never involved).

const totals = {
  attempt_count: 4,
  known_cost_cny: "2.000000000000",
  known_token_total: 20,
  unknown_usage_count: 2,
  succeeded_count: 2,
  failed_count: 1,
  abandoned_count: 0,
  pending_count: 1,
  p50_latency_ms: "200",
  p95_latency_ms: "290",
  last_completed_at: "2026-07-26T05:00:00Z",
};

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试Lead", role: "hr_lead" }),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.route("**/api/proxy/api/v1/operations/summary**", (r) =>
    r.fulfill({
      status: 200,
      json: {
        window: "7d",
        current_start: "2026-07-19T05:00:00Z",
        current_end: "2026-07-26T05:00:00Z",
        previous_start: "2026-07-12T05:00:00Z",
        previous_end: "2026-07-19T05:00:00Z",
        current: totals,
        previous: { ...totals, attempt_count: 1 },
        cost_delta: { absolute: "-2", percentage: "-50" },
        attempt_delta: { absolute: "3", percentage: "300" },
        daily_series: [
          {
            local_date: "2026-07-25",
            attempt_count: 4,
            known_cost_cny: "2",
            unknown_usage_count: 2,
          },
        ],
        by_operation: [
          {
            key: "judge",
            attempt_count: 3,
            known_cost_cny: "1.5",
            unknown_usage_count: 1,
          },
        ],
        by_requested_model: [],
        by_actual_model: [],
        by_outcome: [],
        by_attempt_role: [],
        budgets: [
          {
            scope: "daily",
            period_start: "2026-07-25T16:00:00Z",
            period_end: "2026-07-26T16:00:00Z",
            budget_cny: "100",
            spend_cny: "85",
            ratio: "0.85",
            unknown_cost_count: 2,
            state: "warning",
          },
        ],
      },
    }),
  );
  await page.route("**/api/proxy/api/v1/operations/usage**", (r) =>
    r.fulfill({ status: 200, json: { items: [], page: 1, page_size: 20, total: 0 } }),
  );
});

test("operations workspace shows cost, budget state, and a breadcrumb", async ({
  page,
}) => {
  await page.goto("/reports/operations");

  await expect(page.getByRole("heading", { name: "运营成本" })).toBeVisible();
  // Exact decimal, not a rounded float. Scoped to the metric tile because the
  // daily-series table repeats the same amount.
  const costTile = page.getByTestId("metric-tile").filter({ hasText: "已知成本" });
  await expect(costTile.getByText("¥2", { exact: true })).toBeVisible();
  // Budget condition is stated in words, not colour alone.
  await expect(page.getByText("接近预算")).toBeVisible();
  await expect(page.getByText("¥85 / ¥100（85%）")).toBeVisible();

  const breadcrumb = page.getByRole("navigation", { name: "面包屑" });
  await expect(breadcrumb.getByText("运营成本")).toBeVisible();
});

test("no candidate content reaches the operations DOM", async ({ page }) => {
  await page.goto("/reports/operations");
  await expect(page.getByRole("heading", { name: "运营成本" })).toBeVisible();

  const body = (await page.locator("body").innerHTML()).toLowerCase();
  for (const forbidden of ["name_cipher", "evidence_quotes", "object_key", "bearer "]) {
    expect(body).not.toContain(forbidden);
  }
});

test("operations workspace has no serious accessibility violations", async ({
  page,
}) => {
  await page.goto("/reports/operations");
  await expect(page.getByRole("heading", { name: "运营成本" })).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  const blocking = results.violations.filter((v) =>
    ["serious", "critical"].includes(v.impact ?? ""),
  );

  expect(blocking).toEqual([]);
});
