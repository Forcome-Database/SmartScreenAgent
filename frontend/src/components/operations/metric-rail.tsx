"use client";

import { cn } from "@/lib/utils";

export type Metric = {
  label: string;
  value: string;
  hint?: string;
  tone?: "normal" | "warning" | "exceeded";
};

const TONE: Record<NonNullable<Metric["tone"]>, string> = {
  normal: "border-border",
  warning: "border-amber-500",
  exceeded: "border-red-600",
};

export function MetricRail({ metrics }: { metrics: Metric[] }) {
  return (
    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map((metric) => (
        <div
          key={metric.label}
          className={cn(
            "rounded-md border border-l-4 p-3",
            TONE[metric.tone ?? "normal"],
          )}
        >
          <dt className="text-sm text-muted-foreground">{metric.label}</dt>
          <dd className="text-2xl font-semibold tabular-nums">{metric.value}</dd>
          {metric.hint && (
            <p className="mt-1 text-xs text-muted-foreground">{metric.hint}</p>
          )}
        </div>
      ))}
    </dl>
  );
}

/** Money arrives as an exact decimal string; never round it through a float. */
export function cny(value: string | null): string {
  if (value === null) return "—";
  const [whole, fraction = ""] = value.split(".");
  const trimmed = fraction.replace(/0+$/, "").slice(0, 4);
  return `¥${whole}${trimmed ? `.${trimmed}` : ""}`;
}

export function ratioPercent(value: string | null): string {
  if (value === null) return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${Math.round(parsed * 100)}%` : "—";
}

export function signedPercent(value: string | null): string {
  if (value === null) return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  const rounded = Math.round(parsed * 10) / 10;
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}
