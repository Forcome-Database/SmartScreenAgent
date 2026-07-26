"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { QualityReleaseDetail } from "@/lib/wp7-schemas";

type Confidence = QualityReleaseDetail["confidence"];

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function ConfidenceBins({ confidence }: { confidence: Confidence }) {
  return (
    <div className="space-y-2">
      <h2 className="font-medium">判官自评置信度可靠性</h2>
      <p className="text-sm text-muted-foreground">
        这是判官对自己判断的信心，<strong>不是</strong>候选人应当推进的概率。
        样本不足的分箱不计算准确率，也不参与 ECE。
      </p>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>置信度区间</TableHead>
              <TableHead>样本</TableHead>
              <TableHead>平均置信度</TableHead>
              <TableHead>实际准确率</TableHead>
              <TableHead>偏差</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {confidence.bins.map((bin) => (
              <TableRow key={`${bin.lower}-${bin.upper}`}>
                <TableCell className="tabular-nums">
                  [{bin.lower}, {bin.upper}
                  {bin.upper_inclusive ? "]" : ")"}
                </TableCell>
                <TableCell>
                  {bin.count}
                  {bin.status === "insufficient_data" && bin.count > 0 && (
                    <span className="ml-1 text-xs text-muted-foreground">样本不足</span>
                  )}
                </TableCell>
                <TableCell>{pct(bin.mean_confidence)}</TableCell>
                <TableCell>{pct(bin.decision_accuracy)}</TableCell>
                <TableCell>{pct(bin.absolute_gap)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-sm">
        ECE {confidence.ece === null ? "—" : confidence.ece.toFixed(3)} · 可用样本{" "}
        {confidence.available_count} · 无法计算置信度{" "}
        {confidence.confidence_unavailable}
      </p>
    </div>
  );
}
