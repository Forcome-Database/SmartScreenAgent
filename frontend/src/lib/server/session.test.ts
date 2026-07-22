import { describe, expect, it, beforeEach } from "vitest";
import { createSession, readSession } from "@/lib/server/session";

beforeEach(() => {
  process.env.API_BASE_URL = "http://backend.test";
  process.env.SESSION_COOKIE_SECRET = "test-secret-test-secret-test-secret-xx";
  process.env.DINGTALK_CLIENT_ID = "cid";
  process.env.DINGTALK_REDIRECT_URI = "http://frontend.test/auth/callback";
});

describe("session cookie", () => {
  it("round-trips a signed session and rejects tampering", async () => {
    const value = await createSession({ token: "jwt123", displayName: "张三", role: "hr" });
    const back = await readSession(value);
    expect(back).toEqual({ token: "jwt123", displayName: "张三", role: "hr" });
    // tamper the payload
    const tampered = value.replace(/.$/, (c) => (c === "a" ? "b" : "a"));
    expect(await readSession(tampered)).toBeNull();
  });

  it("returns null for garbage", async () => {
    expect(await readSession("not-a-cookie")).toBeNull();
  });
});
