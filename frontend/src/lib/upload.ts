export interface UploadItem {
  file: File;
  status: "pending" | "uploading" | "queued" | "failed";
  jobId?: string;
  error?: string;
}

export function summarize(items: UploadItem[]): { queued: number; failed: number; pending: number } {
  return {
    queued: items.filter((i) => i.status === "queued").length,
    failed: items.filter((i) => i.status === "failed").length,
    pending: items.filter((i) => i.status === "pending" || i.status === "uploading").length,
  };
}
