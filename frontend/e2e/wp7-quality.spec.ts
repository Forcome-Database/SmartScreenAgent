import { test, expect } from "@playwright/test";
import { mintSession } from "./helpers/session";

// Covers the WP7 quality-release workspace: the read-only list, the gated
// preview -> create flow, and the leak blacklist. Same stubbing approach as the
// other specs — a real signed cookie plus page.route on the BFF proxy.

const RELEASE_LIST = {
  items: [
    {
      id: 7,
      status: "below_target",
      window_start: "2026-06-26T05:00:00Z",
      window_end: "2026-07-26T05:00:00Z",
      created_at: "2026-07-26T05:00:00Z",
      created_by: { user_id: 3, display_name: "质量负责人" },
      golden_snapshot_sha256: "a".repeat(64),
    },
  ],
  page: 1,
  page_size: 20,
  total: 1,
};

const PREVIEW = {
  window_start: "2026-06-26T05:00:00Z",
  window_end: "2026-07-26T05:00:00Z",
  selected: [{ jd_id: 1, jd_code: "FOREIGN_TRADE", rule_version_id: 4 }],
  golden_total: 12,
  golden_advance: 7,
  golden_reject: 4,
  golden_borderline: 1,
  score_covered: 10,
  score_uncovered: 2,
  targets: { f1_target: 0.75 },
  input_fingerprint: "b".repeat(64),
};

async function signIn(context: import("@playwright/test").BrowserContext, role: string) {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试Lead", role }),
      url: "http://127.0.0.1:4173",
    },
  ]);
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/proxy/api/v1/quality/releases?**", (r) =>
    r.fulfill({ status: 200, json: RELEASE_LIST }),
  );
  await page.route("**/api/proxy/api/v1/quality/releases", (r) =>
    r.request().method() === "POST"
      ? r.fulfill({
          status: 201,
          json: {
            ...PREVIEW,
            id: 8,
            status: "meets_target",
            golden_snapshot_sha256: "c".repeat(64),
            golden_snapshot_item_count: 12,
            created_at: "2026-07-26T06:00:00Z",
            created_by: { user_id: 3, display_name: "质量负责人" },
            classification: {
              labeled_total: 12,
              covered: 10,
              uncovered: 2,
              borderline_excluded: 1,
              confusion: { tp: 6, fp: 1, tn: 3, fn: 0 },
              precision: 0.857,
              recall: 1,
              f1: 0.923,
              accuracy: 0.9,
            },
            evidence: {
              participating_candidates: 10,
              hard_filter_rejects: 0,
              expected_count: 10,
              covered_count: 10,
              value: 1,
              status: "ok",
            },
            confidence: {
              available_count: 10,
              confidence_unavailable: 0,
              bins: [],
              ece: null,
            },
            agreement: {
              agreed: 8,
              disagreed: 1,
              hold: 1,
              denominator: 9,
              agreement_rate: 0.888,
            },
            f1_target_result: { value: 0.923, target: 0.75, status: "meets_target" },
            evidence_target_result: { value: 1, target: 0.95, status: "meets_target" },
            operations: {
              current: {
                attempt_count: 0,
                succeeded_count: 0,
                failed_count: 0,
                abandoned_count: 0,
                unknown_usage_count: 0,
                known_cost_cny: 0,
                p50_latency_ms: null,
                p95_latency_ms: null,
                scored_count: 0,
                scores_per_day: null,
              },
              previous: {
                attempt_count: 0,
                succeeded_count: 0,
                failed_count: 0,
                abandoned_count: 0,
                unknown_usage_count: 0,
                known_cost_cny: 0,
                p50_latency_ms: null,
                p95_latency_ms: null,
                scored_count: 0,
                scores_per_day: null,
              },
              cost_delta: { absolute: 0, percentage: null },
              attempt_delta: { absolute: 0, percentage: null },
            },
            by_jd: [],
          },
        })
      : r.fulfill({ status: 200, json: RELEASE_LIST }),
  );
  await page.route("**/api/proxy/api/v1/quality/releases/preview", (r) =>
    r.fulfill({ status: 200, json: PREVIEW }),
  );
});

test("the release list shows status and snapshot without candidate content", async ({
  context,
  page,
}) => {
  await signIn(context, "hr");
  await page.goto("/reports/quality");

  await expect(page.getByRole("heading", { name: "质量发布" })).toBeVisible();
  await expect(page.getByText("未达标")).toBeVisible();
  await expect(page.getByText("质量负责人")).toBeVisible();

  const body = (await page.locator("body").innerHTML()).toLowerCase();
  for (const forbidden of ["candidate_id", "name_cipher", "evidence_quotes", "bearer "]) {
    expect(body).not.toContain(forbidden);
  }
});

test("a plain reviewer cannot reach the create control", async ({ context, page }) => {
  await signIn(context, "hr");
  await page.goto("/reports/quality");
  await expect(page.getByRole("heading", { name: "质量发布" })).toBeVisible();

  await expect(page.getByRole("button", { name: "创建发布" })).toHaveCount(0);
});

test("a lead previews the frozen inputs and then creates the release", async ({
  context,
  page,
}) => {
  await signIn(context, "hr_lead");
  await page.goto("/reports/quality");

  await page.getByRole("button", { name: "创建发布" }).click();

  // The preview must state exactly what is about to be frozen.
  await expect(page.getByText("共 12 条（推进 7／淘汰 4／borderline 1）")).toBeVisible();
  await expect(page.getByText("已覆盖 10，未覆盖 2")).toBeVisible();
  await expect(page.getByText("FOREIGN_TRADE@4")).toBeVisible();

  await page.getByRole("button", { name: "确认创建" }).click();

  await expect(page.getByText("已创建发布 #8")).toBeVisible();
});
