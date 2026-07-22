"use client";
import { Suspense, use } from "react";
import Link from "next/link";
import { PaginatedList } from "@/components/paginated-list";
import { RankedCandidate } from "@/lib/schemas";
import { RankedTable } from "@/components/ranked-table";

export default function JdRankedPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">JD {code} · 候选人排名</h1>
        <Link className="text-sm underline underline-offset-4" href={`/jds/${code}/rules`}>
          规则版本
        </Link>
      </div>
      <Suspense fallback={<p className="text-muted-foreground">加载中…</p>}>
        <PaginatedList
          queryKey={["jd-ranked", code]}
          upstreamPath={`/api/v1/jds/${code}/candidates`}
          itemSchema={RankedCandidate}
          emptyText="该 JD 暂无已评分候选人"
          render={(rows, { page, pageSize }) => <RankedTable rows={rows} startRank={(page - 1) * pageSize} />}
        />
      </Suspense>
    </section>
  );
}
