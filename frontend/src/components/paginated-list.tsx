"use client";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { z } from "zod";
import { apiGet } from "@/lib/api-client";
import { pageEnvelope } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { Button } from "@/components/ui/button";

export function usePageParams() {
  const params = useSearchParams();
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const pageSize = Math.min(100, Math.max(1, Number(params.get("page_size") ?? "20") || 20));
  return { page, pageSize };
}

export function PaginatedList<T>({
  queryKey,
  upstreamPath,
  extraQuery,
  itemSchema,
  render,
  emptyText,
}: {
  queryKey: unknown[];
  upstreamPath: string;
  extraQuery?: Record<string, string | undefined>;
  itemSchema: z.ZodType<T>;
  render: (rows: T[]) => React.ReactNode;
  emptyText?: string;
}) {
  const { page, pageSize } = usePageParams();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const envelope = pageEnvelope(itemSchema);
  const query = useQuery({
    queryKey: [...queryKey, page, pageSize, extraQuery],
    queryFn: () => apiGet(upstreamPath, { ...extraQuery, page: String(page), page_size: String(pageSize) }, envelope),
  });

  function goTo(nextPage: number) {
    const sp = new URLSearchParams(params.toString());
    sp.set("page", String(nextPage));
    router.push(`${pathname}?${sp.toString()}`);
  }

  const total = query.data?.total ?? 0;
  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="space-y-4">
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        isEmpty={query.data?.items.length === 0}
        emptyText={emptyText}
        onRetry={() => query.refetch()}
      >
        {query.data ? render(query.data.items) : null}
      </DataState>
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">共 {total} 条 · 第 {page}/{maxPage} 页</span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => goTo(page - 1)}>上一页</Button>
          <Button variant="outline" size="sm" disabled={page >= maxPage} onClick={() => goTo(page + 1)}>下一页</Button>
        </div>
      </div>
    </div>
  );
}
