"use client";

import { AlertTriangle, CheckCircle2, OctagonAlert } from "lucide-react";

import { cny, ratioPercent } from "@/components/operations/metric-rail";
import type { OperationsSummary } from "@/lib/wp7-schemas";

type Snapshot = OperationsSummary["budgets"][number];

// Text AND icon, never colour alone — the state has to survive a greyscale
// screen and a colour-blind reader.
const STATE = {
  normal: { label: "正常", Icon: CheckCircle2, className: "text-emerald-700" },
  warning: { label: "接近预算", Icon: AlertTriangle, className: "text-amber-700" },
  exceeded: { label: "已超预算", Icon: OctagonAlert, className: "text-red-700" },
} as const;

export function BudgetStatus({ budgets }: { budgets: Snapshot[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {budgets.map((budget) => {
        const { label, Icon, className } = STATE[budget.state];
        return (
          <div key={budget.scope} className="rounded-md border p-3">
            <div className="flex items-center justify-between">
              <p className="font-medium">
                {budget.scope === "daily" ? "当日预算" : "当月预算"}
              </p>
              <span className={`flex items-center gap-1 text-sm ${className}`}>
                <Icon className="size-4" aria-hidden />
                {label}
              </span>
            </div>
            <p className="mt-1 text-sm tabular-nums">
              {cny(budget.spend_cny)} / {cny(budget.budget_cny)}（
              {ratioPercent(budget.ratio)}）
            </p>
            {budget.unknown_cost_count > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                {budget.unknown_cost_count} 次调用用量未知，未计入金额
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
