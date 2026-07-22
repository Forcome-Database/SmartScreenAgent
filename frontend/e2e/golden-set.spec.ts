import { test, expect } from "@playwright/test";
import { mintSession } from "./helpers/session";

// Covers the WP6b baseline metrics report page (/reports/baseline) against a
// stubbed BFF. Auth follows the same pattern as golden-path.spec.ts /
// feedback.spec.ts: a real HMAC-SHA256-signed `ssa_session` cookie minted via
// mintSession() (see ./helpers/session.ts), not the real DingTalk handshake —
// Playwright's page.route only intercepts browser-issued requests, so it
// stubs /api/proxy/api/v1/golden-set/metrics directly rather than the real
// upstream FastAPI call.

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试Lead", role: "hr_lead" }),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.route("**/api/proxy/api/v1/golden-set/metrics**", (r) =>
    r.fulfill({
      status: 200,
      json: {
        overall: {
          labeled_total: 4,
          scored: 3,
          uncovered: 1,
          borderline_excluded: 1,
          confusion: { tp: 1, fp: 1, tn: 0, fn: 0 },
          precision: 0.5,
          recall: 1,
          f1: 0.6667,
          accuracy: 0.5,
        },
        by_jd: [
          {
            jd_code: "FT",
            labeled_total: 4,
            scored: 3,
            uncovered: 1,
            borderline_excluded: 1,
            confusion: { tp: 1, fp: 1, tn: 0, fn: 0 },
            precision: 0.5,
            recall: 1,
            f1: 0.6667,
            accuracy: 0.5,
          },
        ],
      },
    }),
  );
});

test("baseline report shows the overall precision", async ({ page }) => {
  await page.goto("/reports/baseline");
  // The nav bar may also link to /reports/baseline, so scope to the page's
  // <h1> heading rather than a bare text match (see feedback.spec.ts, which
  // disambiguates the same way).
  await expect(page.getByRole("heading", { name: "基线指标" })).toBeVisible();
  // The precision <span> is the first "text-2xl" span in DOM order (精确率
  // then 召回率 then F1 — see reports/baseline/page.tsx), so .first() picks
  // the stubbed overall precision of 50% (see report.test.tsx, which
  // disambiguates the same value with an explicit `selector: "span"`).
  await expect(page.locator("span.text-2xl").first()).toHaveText("50%");
});
