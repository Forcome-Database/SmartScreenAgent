"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useShellHeader } from "@/components/app-session-context";
import { DataState } from "@/components/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGet } from "@/lib/api-client";
import { BatchRejectionReport } from "@/lib/wp7-schemas";

const REASON_LABEL: Record<string, string> = {
  hard_filter: "硬过滤",
  rule_low: "规则维度偏低",
  judge_low: "判官维度偏低",
  judge_unknown: "判官无法判定",
};

export default function BatchReportPage() {
  const [jdCode, setJdCode] = useState("");

  const query = useQuery({
    queryKey: ["batch-report", jdCode],
    queryFn: () =>
      apiGet("/api/v1/reports/batch", { jd_code: jdCode }, BatchRejectionReport),
    enabled: jdCode.trim().length > 0,
  });

  useShellHeader({
    breadcrumbs: [{ label: "报表" }, { label: "批量分析" }],
    lastRefreshedAt: query.dataUpdatedAt
      ? new Date(query.dataUpdatedAt).toLocaleTimeString("zh-CN")
      : null,
  });

  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">批量淘汰分析</h1>

      <div className="max-w-sm space-y-1">
        <Label htmlFor="jd-code">JD 编码</Label>
        <Input
          id="jd-code"
          value={jdCode}
          onChange={(event) => setJdCode(event.target.value)}
          placeholder="例如 FOREIGN_TRADE"
        />
        <p className="text-xs text-muted-foreground">
          至少需要一个筛选条件：JD 编码、批次 ID 或显式时间窗口。
        </p>
      </div>

      {jdCode.trim().length > 0 && (
        <DataState
          isLoading={query.isLoading}
          error={query.error ? { message: (query.error as Error).message } : null}
          onRetry={() => query.refetch()}
        >
          {query.data && (
            <div className="space-y-6">
              <p className="text-sm">
                窗口内共评分 <strong>{query.data.total_scored}</strong> 份，其中淘汰{" "}
                <strong>{query.data.total_rejected}</strong> 份。
              </p>

              <div>
                <h2 className="mb-2 font-medium">淘汰原因</h2>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>类型</TableHead>
                        <TableHead>维度 / 标签</TableHead>
                        <TableHead>影响人数</TableHead>
                        <TableHead>占比</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {query.data.reasons.map((reason) => (
                        <TableRow key={`${reason.reason_type}-${reason.reason_key}`}>
                          <TableCell>
                            {REASON_LABEL[reason.reason_type] ?? reason.reason_type}
                          </TableCell>
                          <TableCell>{reason.reason_key}</TableCell>
                          <TableCell>{reason.affected_scores}</TableCell>
                          <TableCell>
                            {reason.percentage === null
                              ? "—"
                              : `${Math.round(reason.percentage)}%`}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                {query.data.percentages_may_overlap && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    一位候选人可能同时触发多条原因，因此各项占比之和可能超过 100%。
                  </p>
                )}
              </div>
            </div>
          )}
        </DataState>
      )}
    </section>
  );
}
