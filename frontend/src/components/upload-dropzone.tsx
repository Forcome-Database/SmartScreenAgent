"use client";
import { useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { UploadResponse } from "@/lib/schemas";
import { JobStatusView } from "@/components/job-status";

export function UploadDropzone() {
  const [jobs, setJobs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function onFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    const newJobs: string[] = [];
    for (const file of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch("/api/upload", { method: "POST", body: fd });
        const body = await res.json();
        if (!res.ok) {
          toast.error(`${file.name}：${body.message ?? "上传失败"}`);
          continue;
        }
        const parsed = UploadResponse.safeParse(body);
        if (parsed.success) {
          // job_id is a number on the wire; the poll URL/prop is a string.
          newJobs.push(String(parsed.data.job_id));
        } else {
          toast.error(`${file.name}：响应格式异常`);
        }
      } catch {
        toast.error(`${file.name}：网络错误`);
      }
    }
    setJobs((prev) => [...newJobs, ...prev]);
    setBusy(false);
    if (newJobs.length) toast.success(`已提交 ${newJobs.length} 份简历`);
  }

  return (
    <div className="space-y-4">
      <label className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed p-8 text-center">
        <span className="text-muted-foreground">选择或拖入 PDF/Word 简历（可多选）</span>
        <Input type="file" multiple accept=".pdf,.doc,.docx" disabled={busy} onChange={(e) => onFiles(e.target.files)} />
      </label>
      {jobs.length > 0 ? (
        <div className="space-y-2">
          <h2 className="font-medium">处理进度</h2>
          {jobs.map((j) => (
            <JobStatusView key={j} jobId={j} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
