"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { FeedbackReport } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function pct(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

export default function FeedbackReportPage() {
  const query = useQuery({ queryKey: ["feedback-report"], queryFn: () => apiGet("/api/v1/feedback/report", {}, FeedbackReport) });
  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">复核报告</h1>
      <DataState isLoading={query.isLoading} error={query.error ? { message: (query.error as Error).message } : null} onRetry={() => query.refetch()}>
        {query.data ? (
          <div className="space-y-6">
            <div className="rounded-md border p-4">
              <p>总体一致率：<span className="text-2xl font-semibold">{pct(query.data.overall.agreement_rate)}</span></p>
              <p className="text-muted-foreground text-sm">
                共 {query.data.overall.total} 条 · 一致 {query.data.overall.agreed} · 不一致 {query.data.overall.disagreed} · 待定 {query.data.overall.hold}
              </p>
            </div>
            <div>
              <h2 className="mb-2 font-medium">按 JD</h2>
              <Table>
                <TableHeader><TableRow><TableHead>JD</TableHead><TableHead>一致率</TableHead><TableHead>一致/不一致/待定</TableHead></TableRow></TableHeader>
                <TableBody>
                  {query.data.by_jd.map((j) => (
                    <TableRow key={j.jd_code}>
                      <TableCell>{j.jd_code}</TableCell>
                      <TableCell>{pct(j.agreement_rate)}</TableCell>
                      <TableCell>{j.agreed}/{j.disagreed}/{j.hold}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div>
              <h2 className="mb-2 font-medium">不一致明细（{query.data.disagreements.total}）</h2>
              <Table>
                <TableHeader><TableRow><TableHead>候选人</TableHead><TableHead>JD</TableHead><TableHead>裁决</TableHead><TableHead>理由</TableHead><TableHead>审阅人</TableHead></TableRow></TableHeader>
                <TableBody>
                  {query.data.disagreements.items.map((d) => (
                    <TableRow key={d.feedback_id}>
                      <TableCell><Link className="underline" href={`/candidates/${d.candidate_id}/scores/${d.score_id}`}>{d.candidate_id}</Link></TableCell>
                      <TableCell>{d.jd_code}</TableCell>
                      <TableCell>{d.decision}</TableCell>
                      <TableCell>{d.reason ?? "—"}</TableCell>
                      <TableCell>{d.reviewer_display_name}</TableCell>
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
