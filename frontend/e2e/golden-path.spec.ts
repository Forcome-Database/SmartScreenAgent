import { test, expect } from "@playwright/test";
import { mintSession } from "./helpers/session";
import { stubCandidateFlow } from "./fixtures/stub-backend";

// This spec drives the HR golden path — candidate list -> candidate detail
// (audited PII) -> scorecard (judge evidence) — entirely against a stubbed
// BFF, and runs under both the `desktop` and `mobile` Playwright projects
// (see playwright.config.ts) for responsive coverage.
//
// It does NOT exercise the real DingTalk login handshake. Playwright's
// `page.route` only intercepts requests issued by the BROWSER; it cannot
// intercept the Next server's own outbound fetch to FastAPI (the
// /api/auth/callback route calls the upstream /auth/dingtalk/login
// server-side, then sets the session cookie). Stubbing that server-side call
// is not possible from an e2e test without adding a test-only bypass to
// production code, which is disallowed.
//
// Instead we mint a REAL HMAC-SHA256-signed session cookie using the same
// secret and algorithm as src/lib/server/session.ts (see ./helpers/session.ts),
// so the protected (app)/layout.tsx's server-side readSession() accepts it
// exactly as it would a cookie minted by a real login. The login handshake
// itself is unit-covered by src/lib/server/session.test.ts (sign/verify/
// tamper) and the callback route's own tests.

test.beforeEach(async ({ page, context }) => {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试HR", role: "hr" }),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await stubCandidateFlow(page);
});

test("HR can reach a scorecard with evidence, no token leak", async ({ page }) => {
  await page.goto("/candidates");
  await expect(page.getByText("候选人列表")).toBeVisible();

  await page.getByRole("link", { name: "7" }).click();
  await expect(page.getByText("张三")).toBeVisible();

  await page.getByRole("link", { name: /FT：82/ }).click();
  await expect(page.getByText("独立负责北美客户")).toBeVisible();

  // No bearer token or presigned URL leaked into the DOM.
  const html = await page.content();
  expect(html).not.toMatch(/Bearer /);
  expect(html).not.toMatch(/X-Amz-Signature|presigned/i);
});
