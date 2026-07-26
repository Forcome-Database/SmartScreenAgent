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
import { ReleaseCreate } from "@/components/quality/release-create";
import { apiGet } from "@/lib/api-client";
import { QualityReleaseList } from "@/lib/wp7-schemas";

const STATUS_LABEL: Record<string, string> = {
  meets_target: "达标",
  below_target: "未达标",
};

export function QualityReleasesView({ canCreate }: { canCreate: boolean }) {
  const query = useQuery({
    queryKey: ["quality-releases"],
    queryFn: () => apiGet("/api/v1/quality/releases", {}, QualityReleaseList),
  });

  useShellHeader({
    breadcrumbs: [{ label: "报表" }, { label: "质量发布" }],
    lastRefreshedAt: query.dataUpdatedAt
      ? new Date(query.dataUpdatedAt).toLocaleTimeString("zh-CN")
      : null,
  });

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">质量发布</h1>
        <ReleaseCreate canCreate={canCreate} />
      </div>
      <p className="text-sm text-muted-foreground">
        每个发布把一份内容寻址的黄金集快照与各 JD 当时的 active
        规则版本绑定，创建后不可变。
      </p>

      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        isEmpty={query.data?.items.length === 0}
        emptyText="还没有质量发布"
        onRetry={() => query.refetch()}
      >
        {query.data && (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>发布</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>观察窗口</TableHead>
                  <TableHead>快照</TableHead>
                  <TableHead>创建人</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>#{item.id}</TableCell>
                    <TableCell>
                      <span
                        className={
                          item.status === "meets_target"
                            ? "text-emerald-700"
                            : "text-red-700"
                        }
                      >
                        {STATUS_LABEL[item.status]}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      {item.window_start} → {item.window_end}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {item.golden_snapshot_sha256.slice(0, 12)}…
                    </TableCell>
                    <TableCell>{item.created_by.display_name}</TableCell>
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
