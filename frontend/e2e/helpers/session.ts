import crypto from "node:crypto";

// Must match `SESSION_COOKIE_SECRET` passed to the Playwright webServer in
// playwright.config.ts — the signature is only valid if both sides use the
// same secret.
export const E2E_SECRET = "e2e-secret-e2e-secret-e2e-secret-xx";

export interface E2ESession {
  token: string;
  displayName: string;
  role: string;
}

/**
 * Mints a signed session cookie value identical in shape to what
 * `src/lib/server/session.ts`'s `createSession` produces: HMAC-SHA256 (via
 * Node's `crypto`, not `crypto.subtle`, but byte-identical output) over the
 * base64url-encoded JSON payload, cookie = `payload.sigB64url`.
 *
 * This lets e2e tests set a REAL cookie that `readSession()` accepts, instead
 * of an unsigned stub that the server-side `(app)/layout.tsx` would reject
 * and redirect away from.
 */
export function mintSession(session: E2ESession): string {
  const payload = Buffer.from(JSON.stringify(session)).toString("base64url");
  const sig = crypto.createHmac("sha256", E2E_SECRET).update(payload).digest().toString("base64url");
  return `${payload}.${sig}`;
}
