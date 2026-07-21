import Link from "next/link";
import { z } from "zod";
import { RankedCandidate } from "@/lib/schemas";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

type Row = z.infer<typeof RankedCandidate>;

export function RankedTable({ rows }: { rows: Row[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>排名</TableHead>
          <TableHead>候选人 ID</TableHead>
          <TableHead>总分</TableHead>
          <TableHead>等级</TableHead>
          <TableHead>规则版本</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={r.score_id}>
            <TableCell>{i + 1}</TableCell>
            <TableCell>
              <Link className="underline" href={`/candidates/${r.candidate_id}`}>{r.candidate_id}</Link>
            </TableCell>
            <TableCell>{r.total_score.toFixed(2)}</TableCell>
            <TableCell><Badge>{r.grade}</Badge></TableCell>
            <TableCell>{r.rule_version}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
