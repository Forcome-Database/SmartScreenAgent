"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { DataState } from "@/components/data-state";
import { useShellHeader } from "@/components/app-session-context";
import { BudgetStatus } from "@/components/operations/budget-status";
import {
  MetricRail,
  cny,
  signedPercent,
} from "@/components/operations/metric-rail";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGet } from "@/lib/api-client";
import { OperationsSummary, UsagePage } from "@/lib/wp7-schemas";

const WINDOWS = [
  { value: "today", label: "今日" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
] as const;

export default function OperationsPage() {
  const [window, setWindow] = useState<(typeof WINDOWS)[number]["value"]>("7d");

  const summary = useQuery({
    queryKey: ["operations-summary", window],
    queryFn: () =>
      apiGet("/api/v1/operations/summary", { window }, OperationsSummary),
  });
  const usage = useQuery({
    queryKey: ["operations-usage"],
    queryFn: () =>
      apiGet("/api/v1/operations/usage", { page_size: "20" }, UsagePage),
  });

  useShellHeader({
    breadcrumbs: [{ label: "报表" }, { label: "运营成本" }],
    lastRefreshedAt: summary.dataUpdatedAt
      ? new Date(summary.dataUpdatedAt).toLocaleTimeString("zh-CN")
      : null,
  });

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">运营成本</h1>
        <div className="flex gap-1" role="group" aria-label="统计窗口">
          {WINDOWS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setWindow(option.value)}
              aria-pressed={window === option.value}
              className={`min-h-11 rounded-md border px-3 text-sm transition-colors duration-150 motion-reduce:transition-none ${
                window === option.value
                  ? "bg-accent font-medium"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <DataState
        isLoading={summary.isLoading}
        error={summary.error ? { message: (summary.error as Error).message } : null}
        onRetry={() => summary.refetch()}
      >
        {summary.data && (
          <div className="space-y-6">
            <MetricRail
              metrics={[
                {
                  label: "已知成本",
                  value: cny(summary.data.current.known_cost_cny),
                  hint: `环比 ${signedPercent(summary.data.cost_delta.percentage)}`,
                },
                {
                  label: "调用次数",
                  value: String(summary.data.current.attempt_count),
                  hint: `环比 ${signedPercent(summary.data.attempt_delta.percentage)}`,
                },
                {
                  label: "失败 / 放弃",
                  value: `${summary.data.current.failed_count} / ${summary.data.current.abandoned_count}`,
                  hint: `用量未知 ${summary.data.current.unknown_usage_count}`,
                },
                {
                  label: "P95 延迟",
                  value: summary.data.current.p95_latency_ms
                    ? `${Math.round(Number(summary.data.current.p95_latency_ms))} ms`
                    : "—",
                  hint: summary.data.current.p50_latency_ms
                    ? `P50 ${Math.round(Number(summary.data.current.p50_latency_ms))} ms`
                    : undefined,
                },
              ]}
            />

            <BudgetStatus budgets={summary.data.budgets} />

            <div>
              <h2 className="mb-2 font-medium">每日成本</h2>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>日期</TableHead>
                      <TableHead>调用</TableHead>
                      <TableHead>成本</TableHead>
                      <TableHead>用量未知</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.data.daily_series.map((point) => (
                      <TableRow key={point.local_date}>
                        <TableCell>{point.local_date}</TableCell>
                        <TableCell>{point.attempt_count}</TableCell>
                        <TableCell>{cny(point.known_cost_cny)}</TableCell>
                        <TableCell>{point.unknown_usage_count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>

            <div>
              <h2 className="mb-2 font-medium">按操作类型</h2>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>类型</TableHead>
                      <TableHead>调用</TableHead>
                      <TableHead>成本</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.data.by_operation.map((row) => (
                      <TableRow key={row.key}>
                        <TableCell>{row.key}</TableCell>
                        <TableCell>{row.attempt_count}</TableCell>
                        <TableCell>{cny(row.known_cost_cny)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          </div>
        )}
      </DataState>

      <div>
        <h2 className="mb-2 font-medium">调用明细</h2>
        <DataState
          isLoading={usage.isLoading}
          error={usage.error ? { message: (usage.error as Error).message } : null}
          isEmpty={usage.data?.items.length === 0}
          emptyText="窗口内没有调用记录"
          onRetry={() => usage.refetch()}
        >
          {usage.data && (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>操作</TableHead>
                    <TableHead>模型</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>成本</TableHead>
                    <TableHead>延迟</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {usage.data.items.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell>{item.started_at}</TableCell>
                      <TableCell>
                        {item.operation}／{item.attempt_role}
                      </TableCell>
                      <TableCell>{item.actual_model ?? item.requested_model}</TableCell>
                      <TableCell>{item.status}</TableCell>
                      <TableCell>{cny(item.estimated_cost_cny)}</TableCell>
                      <TableCell>{item.latency_ms ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </DataState>
      </div>
    </section>
  );
}
