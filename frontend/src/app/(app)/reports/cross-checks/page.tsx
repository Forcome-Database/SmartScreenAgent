"use client";

import { useQuery } from "@tanstack/react-query";

import { useShellHeader } from "@/components/app-session-context";
import { DataState } from "@/components/data-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGet } from "@/lib/api-client";
import { SuspiciousPage } from "@/lib/wp7-schemas";

const REASON_LABEL: Record<string, string> = {
  deterministic_sample: "抽样",
  low_confidence: "低置信度",
  golden_error: "与金标不符",
  ai_hr_disagreement: "与 HR 不一致",
  admin_backfill: "管理员回填",
};

export default function CrossChecksPage() {
  const query = useQuery({
    queryKey: ["cross-checks-suspicious"],
    queryFn: () =>
      apiGet("/api/v1/cross-checks/suspicious", {}, SuspiciousPage),
  });

  useShellHeader({
    breadcrumbs: [{ label: "报表" }, { label: "交叉校验" }],
    lastRefreshedAt: query.dataUpdatedAt
      ? new Date(query.dataUpdatedAt).toLocaleTimeString("zh-CN")
      : null,
  });

  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">交叉校验存疑列表</h1>
      <p className="text-sm text-muted-foreground">
        次引擎重新评分与主评分差异超过阈值的候选人。次引擎结果仅作参考，不会替换主评分。
      </p>

      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        isEmpty={query.data?.items.length === 0}
        emptyText="没有超过阈值的差异"
        onRetry={() => query.refetch()}
      >
        {query.data && (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>候选人</TableHead>
                  <TableHead>JD</TableHead>
                  <TableHead>主评分</TableHead>
                  <TableHead>次引擎</TableHead>
                  <TableHead>差异</TableHead>
                  <TableHead>触发原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.items.map((item) => (
                  <TableRow key={item.cross_check_id}>
                    <TableCell>#{item.candidate_id}</TableCell>
                    <TableCell>{item.jd_code}</TableCell>
                    <TableCell className="tabular-nums">
                      {item.primary_total_score ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {item.secondary_total_score ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums font-medium">
                      {item.absolute_diff ?? "—"}
                      <span className="text-xs text-muted-foreground">
                        （阈值 {item.threshold ?? "—"}）
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      {item.sample_reasons
                        .map((reason) => REASON_LABEL[reason] ?? reason)
                        .join("、")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DataState>
    </section>
  );
}
