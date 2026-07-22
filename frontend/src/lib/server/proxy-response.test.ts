import { describe, expect, it } from "vitest";
import { sessionExpiredResponse } from "@/lib/server/proxy-response";

describe("sessionExpiredResponse", () => {
  it("returns 401 and clears the session cookie", async () => {
    const res = sessionExpiredResponse();
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ code: "unauthorized", message: "会话已失效，请重新登录" });
    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain("ssa_session=");
    expect(setCookie.toLowerCase()).toMatch(/max-age=0|expires=/);
  });
});
