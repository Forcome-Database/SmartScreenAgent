"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { z } from "zod";
import { DataState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGet, ApiError, apiPost } from "@/lib/api-client";
import {
  EvaluateResponse,
  RuleMetrics,
  RuleVersionList,
  RuleVersionRef,
} from "@/lib/schemas";

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function persistedMetrics(value: Record<string, unknown> | null) {
  const parsed = RuleMetrics.safeParse(value);
  return parsed.success ? parsed.data : null;
}

export function RuleManagementView({ code, canManage }: { code: string; canManage: boolean }) {
  const queryClient = useQueryClient();
  const [schemaText, setSchemaText] = useState("");
  const [notes, setNotes] = useState("");
  const [evaluation, setEvaluation] = useState<{
    version: string;
    data: z.infer<typeof EvaluateResponse>;
  } | null>(null);
  const path = `/api/v1/jds/${code}/rule-versions`;
  const list = useQuery({
    queryKey: ["rule-versions", code],
    queryFn: () => apiGet(path, {}, RuleVersionList),
  });

  const create = useMutation({
    mutationFn: () => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(schemaText);
      } catch {
        throw new ApiError("invalid_json", "schema JSON 无法解析", 400);
      }
      return apiPost(
        path,
        { schema_json: parsed, notes: notes.trim() || null },
        RuleVersionRef,
      );
    },
    onSuccess: () => {
      toast.success("草稿已创建");
      setSchemaText("");
      setNotes("");
      void queryClient.invalidateQueries({ queryKey: ["rule-versions", code] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "创建失败"),
  });

  const evaluate = useMutation({
    mutationFn: (version: string) =>
      apiPost(`${path}/${version}/evaluate`, {}, EvaluateResponse).then((data) => ({
        version,
        data,
      })),
    onSuccess: (result) => {
      setEvaluation(result);
      void queryClient.invalidateQueries({ queryKey: ["rule-versions", code] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "评估失败"),
  });

  const publish = useMutation({
    mutationFn: (version: string) =>
      apiPost(`${path}/${version}/publish`, {}, RuleVersionRef),
    onSuccess: () => {
      toast.success("规则版本已发布");
      setEvaluation(null);
      void queryClient.invalidateQueries({ queryKey: ["rule-versions", code] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "发布失败"),
  });

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">规则版本 · {code}</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          创建草稿并查看各版本的发布与回归状态。
        </p>
      </div>

      {canManage ? (
        <div className="space-y-3 border-y py-4">
          <div>
            <label htmlFor="schema-json" className="text-sm font-medium">
              规则 schema JSON
            </label>
            <p className="text-muted-foreground mt-1 text-xs">
              schema 内的 version 将作为新草稿版本号。
            </p>
          </div>
          <textarea
            id="schema-json"
            aria-label="规则 schema JSON"
            className="border-input bg-background min-h-40 w-full rounded-md border p-3 font-mono text-xs"
            value={schemaText}
            onChange={(event) => setSchemaText(event.target.value)}
            placeholder={'{"version":"v2", ...}'}
          />
          <input
            aria-label="备注"
            className="border-input bg-background w-full rounded-md border px-3 py-2 text-sm"
            placeholder="备注（可选）"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
          <Button
            size="sm"
            disabled={create.isPending || !schemaText.trim()}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "创建中…" : "创建草稿"}
          </Button>
        </div>
      ) : null}

      <DataState
        isLoading={list.isLoading}
        error={list.error ? { message: (list.error as Error).message } : null}
        isEmpty={list.data?.items.length === 0}
        emptyText="暂无规则版本"
        onRetry={() => list.refetch()}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>版本</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>回归 F1</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.items.map((version) => {
              const storedMetrics = persistedMetrics(version.golden_set_metrics);
              const currentMetrics =
                evaluation?.version === version.version ? evaluation.data.draft : storedMetrics;
              const hasRecordedMetrics =
                currentMetrics !== null;
              return (
                <TableRow key={version.id}>
                  <TableCell className="font-medium">
                    {version.version}
                    {version.is_active ? (
                      <span className="text-muted-foreground ml-2 text-xs">生效中</span>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <Badge variant={version.status === "published" ? "default" : "outline"}>
                      {version.status}
                    </Badge>
                  </TableCell>
                  <TableCell>{currentMetrics ? pct(currentMetrics.f1) : "未记录"}</TableCell>
                  <TableCell>
                    {canManage && version.status === "draft" ? (
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          aria-label={`评估 ${version.version}`}
                          disabled={evaluate.isPending}
                          onClick={() => evaluate.mutate(version.version)}
                        >
                          评估
                        </Button>
                        <Button
                          size="sm"
                          aria-label={`发布 ${version.version}`}
                          disabled={!hasRecordedMetrics || publish.isPending}
                          onClick={() => publish.mutate(version.version)}
                        >
                          发布
                        </Button>
                      </div>
                    ) : null}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </DataState>

      {evaluation ? (
        <div className="space-y-3 border-t pt-4">
          <h2 className="font-medium">What-If 对比 · 草稿 {evaluation.version}</h2>
          {evaluation.data.judge_dimensions_changed ? (
            <p className="text-destructive text-sm">
              草稿修改了 judge 维度；重算复用了原 judge 子分，结果为近似值。
            </p>
          ) : null}
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <p className="text-muted-foreground">草稿 F1</p>
              <p className="text-2xl font-semibold">{pct(evaluation.data.draft.f1)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">基线 F1</p>
              <p className="text-2xl font-semibold">
                {evaluation.data.baseline ? pct(evaluation.data.baseline.f1) : "—"}
              </p>
            </div>
          </div>
          <p className="text-muted-foreground text-sm">
            草稿混淆矩阵 TP {evaluation.data.draft.confusion.tp} / FP{" "}
            {evaluation.data.draft.confusion.fp} / TN {evaluation.data.draft.confusion.tn} / FN{" "}
            {evaluation.data.draft.confusion.fn} · 已评估 {evaluation.data.draft.evaluated} · 无法判定{" "}
            {evaluation.data.draft.indeterminate}
          </p>
        </div>
      ) : null}
    </section>
  );
}
