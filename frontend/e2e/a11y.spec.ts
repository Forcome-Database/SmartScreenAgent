import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mintSession } from "./helpers/session";
import { stubEmptyCandidateList } from "./fixtures/stub-backend";

// Auth is established via a minted, HMAC-signed session cookie, not the real
// DingTalk handshake — see the header comment in golden-path.spec.ts for why.

test.beforeEach(async ({ page, context }) => {
  await context.addCookies([
    {
      name: "ssa_session",
      value: mintSession({ token: "e2e", displayName: "测试HR", role: "hr" }),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await stubEmptyCandidateList(page);
});

test("candidate list has no serious/critical a11y violations", async ({ page }) => {
  await page.goto("/candidates");
  await expect(page.getByText("还没有候选人，去上传简历")).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
});
