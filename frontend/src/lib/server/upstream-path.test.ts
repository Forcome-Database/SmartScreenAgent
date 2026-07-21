import { describe, expect, it } from "vitest";
import { safeUpstreamPath } from "@/lib/server/upstream-path";

describe("safeUpstreamPath", () => {
  it("allows a normal /api/v1 path", () => {
    expect(safeUpstreamPath(["api", "v1", "candidates"])).toBe("/api/v1/candidates");
    expect(safeUpstreamPath(["api", "v1", "candidates", "7", "raw-file"])).toBe("/api/v1/candidates/7/raw-file");
  });
  it("rejects traversal segments (the review's bypass)", () => {
    expect(safeUpstreamPath(["api", "v1", "..", "..", "..", "secret"])).toBeNull();
    expect(safeUpstreamPath(["api", "v1", "foo", ".", "bar"])).toBeNull();
  });
  it("rejects empty segments", () => {
    expect(safeUpstreamPath(["api", "v1", "", "x"])).toBeNull();
  });
  it("rejects /auth/* (token-issuing routes must not go through the generic proxy)", () => {
    expect(safeUpstreamPath(["auth", "dingtalk", "login"])).toBeNull();
  });
  it("rejects paths outside the allowlist", () => {
    expect(safeUpstreamPath(["etc", "passwd"])).toBeNull();
    expect(safeUpstreamPath([])).toBeNull();
  });
});
