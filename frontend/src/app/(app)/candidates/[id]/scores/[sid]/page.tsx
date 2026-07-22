"use client";
import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { ScoreDetail } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { Scorecard } from "@/components/scorecard";
import { RescoreButton } from "@/components/rescore-button";

export default function ScorecardPage({ params }: { params: Promise<{ id: string; sid: string }> }) {
  const { id, sid } = use(params);
  const query = useQuery({
    queryKey: ["score", id, sid],
    queryFn: () => apiGet(`/api/v1/candidates/${id}/scores/${sid}`, {}, ScoreDetail),
  });
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">评分卡</h1>
        {query.data ? (
          <RescoreButton candidateId={query.data.candidate_id} jdCode={query.data.jd_code} />
        ) : null}
      </div>
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        onRetry={() => query.refetch()}
      >
        {query.data ? <Scorecard detail={query.data} /> : null}
      </DataState>
    </section>
  );
}
