"use client";
import { Suspense } from "react";
import { PaginatedList } from "@/components/paginated-list";
import { CandidateListItem } from "@/lib/schemas";
import { CandidateTable } from "@/components/candidate-table";

export default function CandidatesPage() {
  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">候选人列表</h1>
      <Suspense fallback={<p className="text-muted-foreground">加载中…</p>}>
        <PaginatedList
          queryKey={["candidates"]}
          upstreamPath="/api/v1/candidates"
          itemSchema={CandidateListItem}
          emptyText="还没有候选人，去上传简历"
          render={(rows) => <CandidateTable rows={rows} />}
        />
      </Suspense>
    </section>
  );
}
