import { test, expect } from "@playwright/test";
import { mintSession } from "./helpers/session";

// Covers the WP6a feedback report page (/reports/feedback) against a stubbed
// BFF. Auth follows the same pattern as golden-path.spec.ts / a11y.spec.ts: a
// real HMAC-SHA256-signed `ssa_session` cookie minted via mintSession() (see
// ./helpers/session.ts), not the real DingTalk handshake — Playwright's
// page.route only intercepts browser-issued requests, so it stubs
// /api/proxy/api/v1/feedback/report directly rather than the real upstream
// FastAPI call.

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试HR", role: "hr" }),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.route("**/api/proxy/api/v1/feedback/report**", (route) =>
    route.fulfill({
      status: 200,
      json: {
        overall: { total: 2, agreed: 1, disagreed: 1, hold: 0, agreement_rate: 0.5 },
        by_jd: [{ jd_code: "FT", total: 2, agreed: 1, disagreed: 1, hold: 0, agreement_rate: 0.5 }],
        disagreements: { items: [], page: 1, page_size: 20, total: 0 },
      },
    }),
  );
});

test("feedback report shows the agreement rate", async ({ page }) => {
  await page.goto("/reports/feedback");
  // The nav bar also links to /reports/feedback with the same "复核报告" text
  // (see app-shell.tsx), so getByText("复核报告") is ambiguous — scope to the
  // page's <h1> heading instead.
  await expect(page.getByRole("heading", { name: "复核报告" })).toBeVisible();
  // "50%" also appears in the per-JD table row (same stubbed rate), so
  // getByText("50%") is ambiguous — scope to the overall-rate <span> (see
  // report.test.tsx, which disambiguates the same way with `selector: "span"`).
  await expect(page.locator("span.text-2xl.font-semibold")).toHaveText("50%");
});
