import Link from "next/link";
import { z } from "zod";
import { CandidateListItem } from "@/lib/schemas";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

type Row = z.infer<typeof CandidateListItem>;

export function CandidateTable({ rows }: { rows: Row[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>候选人 ID</TableHead>
          <TableHead>创建时间</TableHead>
          <TableHead>最新状态</TableHead>
          <TableHead>已评 JD</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.candidate_id}>
            <TableCell>
              <Link className="underline" href={`/candidates/${r.candidate_id}`}>{r.candidate_id}</Link>
            </TableCell>
            <TableCell>{new Date(r.created_at).toLocaleString("zh-CN")}</TableCell>
            <TableCell>{r.latest_state ?? "—"}</TableCell>
            <TableCell className="flex flex-wrap gap-1">
              {r.scored_jd_codes.map((c) => (
                <Badge key={c} variant="secondary">{c}</Badge>
              ))}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
