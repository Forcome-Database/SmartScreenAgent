import { defineConfig, devices } from "@playwright/test";
import { E2E_SECRET } from "./e2e/helpers/session";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  timeout: 30_000,
  reporter: [["html", { open: "never" }]],
  use: {
    // 3100/3000/8080/etc. were all found bound on this host by unrelated
    // long-lived processes (Docker Desktop's backend + wslrelay silently
    // port-forward a bunch of "conventional" dev ports even when nothing of
    // ours is listening there); with reuseExistingServer enabled, Playwright
    // would happily treat that unrelated service as "the app" and every test
    // would fail against its content instead of ours. 4173 (Vite's preview
    // port, unused by anything in this repo) was confirmed free.
    baseURL: "http://127.0.0.1:4173",
    trace: "on-first-retry",
  },
  webServer: {
    // `npm run build` (~15-30s) then start the production server. Bumped
    // well past the default 120s wait so a cold build never races the
    // webServer url-ready poll.
    command: "npm run build && npm run start -- -p 4173",
    url: "http://127.0.0.1:4173/login",
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
    env: {
      // Dead address: no e2e spec exercises the real upstream fetch — every
      // browser-visible call is stubbed via page.route on /api/proxy/** and
      // /api/auth/**. The real DingTalk login handshake (server-side
      // token exchange in src/app/api/auth/callback/route.ts) is NOT
      // e2e-tested — Playwright can only intercept browser-originated
      // requests, not the Next server's own outbound fetch to FastAPI/
      // DingTalk, so that handshake stays unit-covered (session.test.ts,
      // and the callback route's own tests).
      API_BASE_URL: "http://127.0.0.1:9",
      // Must match e2e/helpers/session.ts's E2E_SECRET so mintSession()'s
      // HMAC signature verifies against the server's readSession().
      SESSION_COOKIE_SECRET: E2E_SECRET,
      DINGTALK_CLIENT_ID: "e2e",
      DINGTALK_REDIRECT_URI: "http://127.0.0.1:4173/auth/callback",
    },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
