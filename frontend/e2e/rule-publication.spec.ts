import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mintSession } from "./helpers/session";

const versions = {
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

const evaluation = {
  draft: {
    confusion: { tp: 2, fp: 0, tn: 1, fn: 1 },
    precision: 1,
    recall: 0.6667,
    f1: 0.8,
    accuracy: 0.75,
    evaluated: 4,
    indeterminate: 1,
    borderline_excluded: 1,
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
    borderline_excluded: 1,
    uncovered: 0,
  },
  judge_dimensions_changed: true,
};

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试Lead", role: "hr_lead" }),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.route("**/api/proxy/api/v1/jds/FT/rule-versions**", (route) => {
    const response = route.request().url().endsWith("/evaluate") ? evaluation : versions;
    return route.fulfill({ status: 200, json: response });
  });
});

test("rule management evaluates a draft without leaking candidate data", async ({ page }) => {
  await page.goto("/jds/FT/rules");
  await expect(page.getByRole("heading", { name: "规则版本 · FT" })).toBeVisible();
  await expect(page.getByText("v2")).toBeVisible();
  await expect(page.getByText("draft")).toBeVisible();
  await expect(page.getByRole("button", { name: "发布 v2" })).toBeDisabled();

  await page.getByRole("button", { name: "评估 v2" }).click();
  await expect(page.getByText("80%").first()).toBeVisible();
  await expect(page.getByText(/结果为近似值/)).toBeVisible();
  await expect(page.getByRole("button", { name: "发布 v2" })).toBeEnabled();

  const body = page.locator("body");
  await expect(body).not.toContainText("name_cipher");
  await expect(body).not.toContainText("张三");
  await expect(body).not.toContainText("Bearer e2e");

  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
});
