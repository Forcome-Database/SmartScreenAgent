"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiPost } from "@/lib/api-client";
import { BackfillResult } from "@/lib/wp7-schemas";

const ERROR_MESSAGE: Record<string, string> = {
  invalid_cross_check_window: "时间窗口不合法",
  cross_check_window_too_large: "时间窗口不能超过 90 天",
  invalid_cross_check_limit: "数量超出允许范围",
};

export function CrossCheckBackfill({ canBackfill }: { canBackfill: boolean }) {
  const [limit, setLimit] = React.useState("50");
  const [preview, setPreview] = React.useState<BackfillResult | null>(null);
  const queryClient = useQueryClient();

  const run = useMutation({
    mutationFn: (dryRun: boolean) =>
      apiPost(
        `/api/v1/cross-checks/backfill?limit=${encodeURIComponent(limit)}&dry_run=${dryRun}`,
        {},
        BackfillResult,
      ),
    onSuccess: (result) => {
      setPreview(result);
      if (!result.dry_run) {
        toast.success(`已排队 ${result.newly_queued} 条`);
        void queryClient.invalidateQueries({ queryKey: ["cross-checks-suspicious"] });
      }
    },
    onError: (error) =>
      toast.error(
        error instanceof ApiError
          ? (ERROR_MESSAGE[error.code] ?? error.message)
          : (error as Error).message,
      ),
  });

  if (!canBackfill) return null;

  return (
    <section className="space-y-3 rounded-md border p-4">
      <h2 className="font-medium">回填交叉校验</h2>
      <p className="text-sm text-muted-foreground">
        对既有评分补跑次引擎校验。先试运行确认范围，再确认执行；已排队过的评分不会重复排队。
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-32 space-y-1">
          <Label htmlFor="backfill-limit">数量上限</Label>
          <Input
            id="backfill-limit"
            value={limit}
            onChange={(event) => setLimit(event.target.value)}
            inputMode="numeric"
          />
        </div>
        <Button
          variant="outline"
          onClick={() => run.mutate(true)}
          disabled={run.isPending}
        >
          试运行
        </Button>
        <Button
          onClick={() => run.mutate(false)}
          disabled={run.isPending || preview === null}
        >
          确认回填
        </Button>
      </div>

      {preview && (
        <p className="text-sm" data-testid="backfill-summary">
          命中 {preview.selected} 条，已存在 {preview.already_existing} 条，
          {preview.dry_run
            ? `将排队 ${preview.would_queue} 条`
            : `已排队 ${preview.newly_queued} 条`}
        </p>
      )}
    </section>
  );
}
