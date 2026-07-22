import { z } from "zod";
import { ScoreDetail } from "@/lib/schemas";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

type Detail = z.infer<typeof ScoreDetail>;

function DimensionBlock({ title, dims }: { title: string; dims: Record<string, unknown> | null }) {
  if (!dims) return null;
  return (
    <div className="space-y-2">
      <h3 className="font-medium">{title}</h3>
      {Object.entries(dims).map(([key, raw]) => {
        const d = (raw ?? {}) as { score?: number; evidence_quotes?: string[]; reasoning?: string };
        return (
          <div key={key} className="rounded-md border p-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{key}</span>
              {typeof d.score === "number" ? <Badge variant="secondary">{d.score}</Badge> : null}
            </div>
            {d.reasoning ? <p className="text-muted-foreground mt-1 text-sm">{d.reasoning}</p> : null}
            {d.evidence_quotes?.length ? (
              <ul className="mt-2 list-inside list-disc text-sm">
                {d.evidence_quotes.map((q, i) => (
                  <li key={i} className="text-foreground/80">
                    <span aria-hidden="true">&ldquo;</span>
                    {q}
                    <span aria-hidden="true">&rdquo;</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function HardFilterBlock({ result }: { result: Record<string, unknown> }) {
  const rejected = result.rejected === true;
  const passed = result.passed === true;
  const auditEntries = Array.isArray(result.audit_entries)
    ? (result.audit_entries as Array<{ filter_id?: string; rule?: string; audit_tag?: string }>)
    : [];
  return (
    <div className="space-y-2">
      <h3 className="font-medium">硬性筛选</h3>
      <div className="rounded-md border p-3">
        <Badge variant={rejected ? "destructive" : "default"}>
          {rejected ? "拒绝" : passed ? "通过" : "—"}
        </Badge>
        {rejected && auditEntries.length > 0 ? (
          <ul className="mt-2 list-inside list-disc text-sm">
            {auditEntries.map((e, i) => (
              <li key={i} className="text-foreground/80">
                {e.filter_id ?? "规则"}
                {e.rule ? `：${e.rule}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

export function Scorecard({ detail }: { detail: Detail }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>JD {detail.jd_code} · 规则 {detail.rule_version}</CardTitle>
        <div className="flex items-center gap-2">
          <span className="text-2xl font-semibold">{detail.total_score}</span>
          <Badge>{detail.grade}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <HardFilterBlock result={detail.hard_filter_result} />
        <DimensionBlock title="规则维度" dims={detail.rule_dimensions} />
        <DimensionBlock title="评委维度" dims={detail.judge_dimensions} />
      </CardContent>
    </Card>
  );
}
