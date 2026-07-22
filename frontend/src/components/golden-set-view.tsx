"use client";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { z } from "zod";
import { apiGet, ApiError } from "@/lib/api-client";
import { GoldenImportResult, GoldenSetList } from "@/lib/schemas";
import { Button } from "@/components/ui/button";
import { DataState } from "@/components/data-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

async function importCsv(file: File): Promise<z.infer<typeof GoldenImportResult>> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/golden-set/import", { method: "POST", body: form });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = body as { code?: string; message?: string };
    throw new ApiError(e.code ?? `http_${res.status}`, e.message ?? "导入失败", res.status);
  }
  return GoldenImportResult.parse(body);
}

export function GoldenSetView({ canImport }: { canImport: boolean }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<z.infer<typeof GoldenImportResult> | null>(null);
  const list = useQuery({
    queryKey: ["golden-set"],
    queryFn: () => apiGet("/api/v1/golden-set", {}, GoldenSetList),
  });

  const mutation = useMutation({
    mutationFn: importCsv,
    onSuccess: (r) => {
      setResult(r);
      toast.success(`导入完成：新增 ${r.created} · 更新 ${r.updated} · 错误 ${r.errors.length}`);
      void qc.invalidateQueries({ queryKey: ["golden-set"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "导入失败"),
  });

  return (
    <section className="space-y-6">
      <h1 className="text-xl font-semibold">黄金集</h1>
      {canImport ? (
        <div className="space-y-2 rounded-md border p-4">
          <label htmlFor="golden-csv" className="text-sm font-medium">
            导入 CSV
          </label>
          <p className="text-muted-foreground text-sm">列：candidate_id, jd_code, label</p>
          <input
            id="golden-csv"
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            aria-label="导入 CSV"
            className="block text-sm"
          />
          <Button
            size="sm"
            disabled={mutation.isPending}
            onClick={() => {
              const f = fileRef.current?.files?.[0];
              if (!f) {
                toast.error("请先选择 CSV 文件");
                return;
              }
              mutation.mutate(f);
            }}
          >
            {mutation.isPending ? "导入中…" : "导入"}
          </Button>
          {result ? (
            <p className="text-sm">
              新增 {result.created} · 更新 {result.updated} · 错误 {result.errors.length}
              {result.errors.length > 0
                ? `（第 ${result.errors.map((e) => e.row).join("、")} 行）`
                : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      <DataState
        isLoading={list.isLoading}
        error={list.error ? { message: (list.error as Error).message } : null}
        isEmpty={list.data?.items.length === 0}
        emptyText="暂无黄金集条目"
        onRetry={() => list.refetch()}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>候选人</TableHead>
              <TableHead>JD</TableHead>
              <TableHead>标签</TableHead>
              <TableHead>导入人</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data?.items.map((g) => (
              <TableRow key={g.id}>
                <TableCell>{g.candidate_id}</TableCell>
                <TableCell>{g.jd_code}</TableCell>
                <TableCell>{g.label}</TableCell>
                <TableCell>{g.imported_by_display_name}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DataState>
    </section>
  );
}
