"use client";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { JobStatus, isTerminalJobState } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";

export function JobStatusView({ jobId, pollMs = 2000 }: { jobId: string; pollMs?: number }) {
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => apiGet(`/api/v1/candidates/jobs/${jobId}`, {}, JobStatus),
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s && isTerminalJobState(s) ? false : pollMs;
    },
  });
  const state = query.data?.state ?? "…";
  const terminal = query.data ? isTerminalJobState(query.data.state) : false;
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">任务 {jobId.slice(0, 8)}</span>
      <Badge variant={terminal ? (state === "ready" || state === "completed" ? "default" : "destructive") : "secondary"}>
        {state}
      </Badge>
    </div>
  );
}
