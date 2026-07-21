"use client";
import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { CandidateDetail } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { RescoreButton } from "@/components/rescore-button";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function CandidateDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useQuery({
    queryKey: ["candidate", id],
    queryFn: () => apiGet(`/api/v1/candidates/${id}`, {}, CandidateDetail),
  });
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">候选人 #{id}</h1>
        <Button variant="outline" render={<a href={`/api/candidates/${id}/raw-file`} />}>
          下载原始简历
        </Button>
      </div>
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        onRetry={() => query.refetch()}
      >
        {query.data ? (
          <Card>
            <CardHeader>
              <CardTitle>{query.data.name}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <div>手机：{query.data.phone ?? "—"}</div>
                <div>邮箱：{query.data.email ?? "—"}</div>
                <div>学历：{query.data.education ?? "—"}</div>
                <div>年龄：{query.data.age ?? "—"}</div>
              </div>
              <div className="space-y-2">
                <div className="font-medium">评分</div>
                {query.data.scores.length === 0 ? (
                  <p className="text-muted-foreground">暂无评分</p>
                ) : (
                  query.data.scores.map((s) => (
                    <div
                      key={s.score_id}
                      className="flex items-center justify-between gap-2 rounded-md border p-2"
                    >
                      <Link className="underline" href={`/candidates/${id}/scores/${s.score_id}`}>
                        {s.jd_code}：{s.total_score}（{s.grade}）
                      </Link>
                      <RescoreButton candidateId={Number(id)} jdCode={s.jd_code} />
                    </div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>
        ) : null}
      </DataState>
    </section>
  );
}
