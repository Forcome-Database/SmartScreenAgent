import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { proxyJson } from "@/lib/server/api";

const originalFetch = global.fetch;

beforeEach(() => {
  process.env.API_BASE_URL = "http://backend.test";
  process.env.SESSION_COOKIE_SECRET = "x".repeat(32);
  process.env.DINGTALK_CLIENT_ID = "cid";
  process.env.DINGTALK_REDIRECT_URI = "http://frontend.test/auth/callback";
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("proxyJson", () => {
  it("forwards bearer + query and returns upstream json", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toContain("/api/v1/candidates?page=2");
      expect((init.headers as Record<string, string>).Authorization).toBe("Bearer t0");
      return new Response(JSON.stringify({ items: [], page: 2, page_size: 20, total: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    const res = await proxyJson("/api/v1/candidates", {
      method: "GET",
      token: "t0",
      query: { page: "2" },
    });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ items: [], page: 2, page_size: 20, total: 0 });
  });

  it("normalizes an upstream error body to {code,message}", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ detail: { code: "not_found", message: "JD not found" } }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;
    const res = await proxyJson("/api/v1/jds/NOPE/candidates", { method: "GET", token: "t0" });
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ code: "not_found", message: "JD not found" });
  });

  it("maps a network failure to 502 upstream_unavailable", async () => {
    global.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const res = await proxyJson("/api/v1/candidates", { method: "GET", token: "t0" });
    expect(res.status).toBe(502);
    expect(res.body).toEqual({ code: "upstream_unavailable", message: expect.any(String) });
  });
});
