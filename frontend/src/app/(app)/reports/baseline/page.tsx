"use client";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { GoldenMetricsReport } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

export default function BaselineReportPage() {
  const query = useQuery({
    queryKey: ["baseline-metrics"],
    queryFn: () => apiGet("/api/v1/golden-set/metrics", {}, GoldenMetricsReport),
  });
  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">基线指标</h1>
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        onRetry={() => query.refetch()}
      >
        {query.data ? (
          <div className="space-y-6">
            <div className="rounded-md border p-4">
              <p>
                精确率 <span className="text-2xl font-semibold">{pct(query.data.overall.precision)}</span>
                {"　"}召回率 <span className="text-2xl font-semibold">{pct(query.data.overall.recall)}</span>
                {"　"}F1 <span className="text-2xl font-semibold">{pct(query.data.overall.f1)}</span>
              </p>
              <p className="text-muted-foreground text-sm">
                标注 {query.data.overall.labeled_total} · 已评分 {query.data.overall.scored} · 未覆盖{" "}
                {query.data.overall.uncovered} · borderline 排除 {query.data.overall.borderline_excluded} ·
                混淆 TP{query.data.overall.confusion.tp}/FP{query.data.overall.confusion.fp}/TN
                {query.data.overall.confusion.tn}/FN{query.data.overall.confusion.fn}
              </p>
            </div>
            <div>
              <h2 className="mb-2 font-medium">按 JD</h2>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>JD</TableHead>
                    <TableHead>精确率</TableHead>
                    <TableHead>召回率</TableHead>
                    <TableHead>F1</TableHead>
                    <TableHead>TP/FP/TN/FN</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data.by_jd.map((j) => (
                    <TableRow key={j.jd_code}>
                      <TableCell>{j.jd_code}</TableCell>
                      <TableCell>{pct(j.precision)}</TableCell>
                      <TableCell>{pct(j.recall)}</TableCell>
                      <TableCell>{pct(j.f1)}</TableCell>
                      <TableCell>
                        {j.confusion.tp}/{j.confusion.fp}/{j.confusion.tn}/{j.confusion.fn}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        ) : null}
      </DataState>
    </section>
  );
}
