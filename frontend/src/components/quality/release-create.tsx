"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { ApiError, apiPost } from "@/lib/api-client";
import {
  QualityReleaseDetail,
  QualityReleasePreview,
} from "@/lib/wp7-schemas";

const ERROR_MESSAGE: Record<string, string> = {
  golden_set_empty: "所选 JD 没有黄金标签，无法度量",
  active_rule_missing: "有 JD 缺少 active 规则版本",
  invalid_active_rule: "有 JD 的 active 规则不合法",
  release_input_changed: "输入已变化，请重新预览",
  invalid_release_window: "观察窗口不合法",
  release_window_too_large: "观察窗口不能超过 365 天",
  release_transaction_conflict: "并发冲突，请重试",
};

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return ERROR_MESSAGE[error.code] ?? error.message;
  }
  return (error as Error).message;
}

export function ReleaseCreate({ canCreate }: { canCreate: boolean }) {
  const [open, setOpen] = React.useState(false);
  const [preview, setPreview] = React.useState<QualityReleasePreview | null>(null);
  const queryClient = useQueryClient();

  const previewMutation = useMutation({
    mutationFn: () =>
      apiPost("/api/v1/quality/releases/preview", {}, QualityReleasePreview),
    onSuccess: setPreview,
    onError: (error) => toast.error(describe(error)),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error("先预览再创建");
      // Echo the previewed window and fingerprint so the server can refuse if
      // the inputs moved underneath us.
      return apiPost(
        "/api/v1/quality/releases",
        {
          window_start: preview.window_start,
          window_end: preview.window_end,
          expected_input_fingerprint: preview.input_fingerprint,
        },
        QualityReleaseDetail,
      );
    },
    onSuccess: (detail) => {
      toast.success(`已创建发布 #${detail.id}`);
      setOpen(false);
      setPreview(null);
      void queryClient.invalidateQueries({ queryKey: ["quality-releases"] });
    },
    onError: (error) => toast.error(describe(error)),
  });

  if (!canCreate) return null;

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setPreview(null);
      }}
    >
      <SheetTrigger
        render={<Button onClick={() => previewMutation.mutate()}>创建发布</Button>}
      />
      <SheetContent side="right" aria-label="创建质量发布">
        <SheetTitle>创建质量发布</SheetTitle>
        <SheetDescription>
          先预览本次将要冻结的输入，确认后创建。创建后不可修改。
        </SheetDescription>

        {previewMutation.isPending && <p className="text-sm">正在预览…</p>}

        {preview && (
          <div className="space-y-4 text-sm">
            <dl className="space-y-1">
              <dt className="text-muted-foreground">观察窗口</dt>
              <dd className="font-mono text-xs">
                {preview.window_start} → {preview.window_end}
              </dd>
            </dl>
            <dl className="space-y-1">
              <dt className="text-muted-foreground">黄金集</dt>
              <dd>
                共 {preview.golden_total} 条（推进 {preview.golden_advance}／淘汰{" "}
                {preview.golden_reject}／borderline {preview.golden_borderline}）
              </dd>
            </dl>
            <dl className="space-y-1">
              <dt className="text-muted-foreground">评分覆盖</dt>
              <dd>
                已覆盖 {preview.score_covered}，未覆盖 {preview.score_uncovered}
              </dd>
            </dl>
            <dl className="space-y-1">
              <dt className="text-muted-foreground">绑定的规则版本</dt>
              <dd>
                {preview.selected
                  .map((s) => `${s.jd_code}@${s.rule_version_id}`)
                  .join("、")}
              </dd>
            </dl>
            <dl className="space-y-1">
              <dt className="text-muted-foreground">输入指纹</dt>
              <dd className="font-mono text-xs break-all">
                {preview.input_fingerprint}
              </dd>
            </dl>

            <Button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
            >
              {createMutation.isPending ? "创建中…" : "确认创建"}
            </Button>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
