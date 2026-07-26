"use client";

import { use } from "react";
import { useQuery } from "@tanstack/react-query";

import { useShellHeader } from "@/components/app-session-context";
import { DataState } from "@/components/data-state";
import { ConfidenceBins } from "@/components/quality/confidence-bins";
import { apiGet } from "@/lib/api-client";
import { QualityReleaseDetail } from "@/lib/wp7-schemas";

const TARGET_LABEL: Record<string, string> = {
  meets_target: "达标",
  below_target: "未达标",
  insufficient_data: "样本不足",
  not_applicable: "不适用",
};

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export default function QualityReleaseDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const query = useQuery({
    queryKey: ["quality-release", id],
    queryFn: () => apiGet(`/api/v1/quality/releases/${id}`, {}, QualityReleaseDetail),
  });

  useShellHeader({
    breadcrumbs: [
      { label: "报表" },
      { label: "质量发布", href: "/reports/quality" },
      { label: `#${id}` },
    ],
    lastRefreshedAt: query.dataUpdatedAt
      ? new Date(query.dataUpdatedAt).toLocaleTimeString("zh-CN")
      : null,
  });

  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">质量发布 #{id}</h1>

      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        onRetry={() => query.refetch()}
      >
        {query.data && (
          <div className="space-y-8">
            <div className="grid gap-3 sm:grid-cols-2">
              <dl className="rounded-md border p-3">
                <dt className="text-sm text-muted-foreground">总体结论</dt>
                <dd className="text-2xl font-semibold">
                  {TARGET_LABEL[query.data.status]}
                </dd>
                <dd className="mt-1 text-xs text-muted-foreground">
                  F1 {TARGET_LABEL[query.data.f1_target_result.status]}／证据覆盖{" "}
                  {TARGET_LABEL[query.data.evidence_target_result.status]}
                </dd>
              </dl>
              <dl className="rounded-md border p-3">
                <dt className="text-sm text-muted-foreground">冻结的输入</dt>
                <dd className="font-mono text-xs break-all">
                  {query.data.golden_snapshot_sha256}
                </dd>
                <dd className="mt-1 text-xs text-muted-foreground">
                  {query.data.golden_snapshot_item_count} 条黄金标签 ·{" "}
                  {query.data.window_start} → {query.data.window_end}
                </dd>
              </dl>
            </div>

            <div>
              <h2 className="mb-2 font-medium">分类指标</h2>
              <p className="text-sm">
                精确率 <strong>{pct(query.data.classification.precision)}</strong>
                {"　"}召回率 <strong>{pct(query.data.classification.recall)}</strong>
                {"　"}F1 <strong>{pct(query.data.classification.f1)}</strong>
              </p>
              <p className="text-xs text-muted-foreground">
                标注 {query.data.classification.labeled_total} · 已覆盖{" "}
                {query.data.classification.covered} · 未覆盖{" "}
                {query.data.classification.uncovered} · borderline 排除{" "}
                {query.data.classification.borderline_excluded}
              </p>
            </div>

            <div>
              <h2 className="mb-2 font-medium">证据覆盖</h2>
              <p className="text-sm">
                <strong>{pct(query.data.evidence.value)}</strong>（
                {query.data.evidence.covered_count}／
                {query.data.evidence.expected_count}）· 硬过滤淘汰{" "}
                {query.data.evidence.hard_filter_rejects} 人不计入分母
              </p>
            </div>

            <ConfidenceBins confidence={query.data.confidence} />

            <div>
              <h2 className="mb-2 font-medium">AI 与 HR 一致性</h2>
              <p className="text-sm">
                一致率 <strong>{pct(query.data.agreement.agreement_rate)}</strong>
                （一致 {query.data.agreement.agreed}／不一致{" "}
                {query.data.agreement.disagreed}／待定 {query.data.agreement.hold}，
                待定不计入分母）
              </p>
            </div>

            <div>
              <h2 className="mb-2 font-medium">窗口内运营指标</h2>
              <p className="text-sm">
                归属调用 {query.data.operations.current.attempt_count} 次 · 成本 ¥
                {query.data.operations.current.known_cost_cny} · 完成评分{" "}
                {query.data.operations.current.scored_count} 份
              </p>
              <p className="text-xs text-muted-foreground">
                P50{" "}
                {query.data.operations.current.p50_latency_ms === null
                  ? "—"
                  : `${Math.round(query.data.operations.current.p50_latency_ms)} ms`}{" "}
                · P95{" "}
                {query.data.operations.current.p95_latency_ms === null
                  ? "—"
                  : `${Math.round(query.data.operations.current.p95_latency_ms)} ms`}
              </p>
            </div>
          </div>
        )}
      </DataState>
    </section>
  );
}
