"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { z } from "zod";
import { apiGet, apiPut, ApiError } from "@/lib/api-client";
import { FeedbackItem, FeedbackList } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { DataState } from "@/components/data-state";

type Decision = "advance" | "reject" | "hold";
const LABELS: Record<Decision, string> = { advance: "推进", reject: "淘汰", hold: "待定" };

export function FeedbackPanel({
  candidateId, scoreId, aiRejected,
}: { candidateId: number; scoreId: number; aiRejected: boolean }) {
  const qc = useQueryClient();
  const [decision, setDecision] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const path = `/api/v1/candidates/${candidateId}/scores/${scoreId}/feedback`;

  const list = useQuery({ queryKey: ["feedback", candidateId, scoreId], queryFn: () => apiGet(path, {}, FeedbackList) });

  const disagrees = (d: Decision) => (d === "reject") !== aiRejected && d !== "hold";

  const mutation = useMutation({
    mutationFn: () => apiPut(path, { decision, reason: reason.trim() || undefined }, FeedbackItem),
    onSuccess: () => { toast.success("已保存反馈"); setReason(""); void qc.invalidateQueries({ queryKey: ["feedback", candidateId, scoreId] }); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "保存失败"),
  });

  function submit() {
    setLocalError(null);
    if (!decision) { setLocalError("请选择裁决"); return; }
    if (disagrees(decision) && !reason.trim()) { setLocalError("与 AI 不一致时请填写理由"); return; }
    mutation.mutate();
  }

  return (
    <div className="space-y-3 rounded-md border p-4">
      <h3 className="font-medium">我的复核</h3>
      <div className="flex gap-2">
        {(["advance", "reject", "hold"] as Decision[]).map((d) => (
          <Button key={d} variant={decision === d ? "default" : "outline"} size="sm" onClick={() => setDecision(d)}>
            {LABELS[d]}
          </Button>
        ))}
      </div>
      <textarea
        className="w-full rounded-md border p-2 text-sm"
        placeholder="理由（与 AI 不一致时必填）"
        aria-label="复核理由"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {localError ? <p className="text-destructive text-sm">{localError}</p> : null}
      <Button onClick={submit} disabled={mutation.isPending}>{mutation.isPending ? "提交中…" : "提交反馈"}</Button>

      <div className="pt-2">
        <h4 className="text-muted-foreground text-sm">复核记录</h4>
        <DataState isLoading={list.isLoading} error={list.error ? { message: (list.error as Error).message } : null} isEmpty={list.data?.length === 0} emptyText="暂无复核">
          <ul className="mt-1 space-y-1 text-sm">
            {list.data?.map((f: z.infer<typeof FeedbackItem>) => (
              <li key={f.id} className="flex items-center justify-between">
                <span>{f.reviewer_display_name}：{LABELS[f.decision as Decision] ?? f.decision}{f.reason ? `（${f.reason}）` : ""}</span>
                <span className={f.ai_agreed === false ? "text-destructive" : "text-muted-foreground"}>
                  {f.ai_agreed === true ? "与AI一致" : f.ai_agreed === false ? "与AI不一致" : "待定"}
                </span>
              </li>
            ))}
          </ul>
        </DataState>
      </div>
    </div>
  );
}
