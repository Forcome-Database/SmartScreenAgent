"use client";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiPost, ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";

const ScoreResponse = z.object({
  score_id: z.number(),
  total_score: z.number(),
  grade: z.string(),
  rejected: z.boolean(),
});

export function RescoreButton({ candidateId, jdCode }: { candidateId: number; jdCode: string }) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      apiPost(`/api/v1/candidates/${candidateId}/score`, { jd_code: jdCode }, ScoreResponse),
    onSuccess: () => {
      toast.success("已重新评分");
      void qc.invalidateQueries();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "重新评分失败"),
  });
  return (
    <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
      {mutation.isPending ? "评分中…" : "重新评分"}
    </Button>
  );
}
