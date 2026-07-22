import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { apiGet, ApiError } from "@/lib/api-client";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

describe("apiGet", () => {
  it("hits the proxy with the encoded upstream path and zod-parses", async () => {
    global.fetch = vi.fn(async (url: string) => {
      expect(url).toBe("/api/proxy/api/v1/candidates?page=1");
      return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), { status: 200 });
    }) as unknown as typeof fetch;
    const schema = z.object({ total: z.number() });
    const out = await apiGet("/api/v1/candidates", { page: "1" }, schema);
    expect(out.total).toBe(0);
  });

  it("throws ApiError with {code,message} on non-2xx", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ code: "not_found", message: "JD not found" }), { status: 404 }),
    ) as unknown as typeof fetch;
    const promise = apiGet("/api/v1/jds/X/candidates", {}, z.unknown());
    await expect(promise).rejects.toBeInstanceOf(ApiError);
    await expect(promise).rejects.toMatchObject({ code: "not_found", status: 404 });
  });
});
