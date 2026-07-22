import { describe, expect, it } from "vitest";
import { summarize, type UploadItem } from "@/lib/upload";

describe("summarize", () => {
  it("counts queued/failed/pending", () => {
    const items: UploadItem[] = [
      { file: new File([""], "a.pdf"), status: "queued", jobId: "j1" },
      { file: new File([""], "b.pdf"), status: "failed", error: "x" },
      { file: new File([""], "c.pdf"), status: "uploading" },
    ];
    expect(summarize(items)).toEqual({ queued: 1, failed: 1, pending: 1 });
  });

  it("counts pending files too, and returns zeros for an empty list", () => {
    expect(summarize([])).toEqual({ queued: 0, failed: 0, pending: 0 });
    const items: UploadItem[] = [{ file: new File([""], "a.pdf"), status: "pending" }];
    expect(summarize(items)).toEqual({ queued: 0, failed: 0, pending: 1 });
  });
});
