"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  ClipboardCheck,
  FileStack,
  GitCompareArrows,
  ListChecks,
  ScrollText,
  ShieldCheck,
  Upload,
  Users,
  Wallet,
} from "lucide-react";

import { cn } from "@/lib/utils";

export type NavLink = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Roles allowed to see the link; undefined means every authenticated role. */
  roles?: readonly string[];
};

export type NavGroup = { title: string; links: readonly NavLink[] };

const OPERATIONS_ROLES = ["hr_lead", "admin"] as const;

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    title: "招聘",
    links: [
      { href: "/candidates", label: "候选人", icon: Users },
      { href: "/upload", label: "上传", icon: Upload },
    ],
  },
  {
    title: "复核与规则",
    links: [
      { href: "/reports/feedback", label: "复核报告", icon: ClipboardCheck },
      { href: "/golden-set", label: "黄金集", icon: ListChecks },
      { href: "/reports/baseline", label: "基线指标", icon: BarChart3 },
    ],
  },
  {
    title: "运营与质量",
    links: [
      // Cost is a privileged view: it exposes spend across the whole tenant.
      { href: "/reports/operations", label: "运营成本", icon: Wallet, roles: OPERATIONS_ROLES },
      { href: "/reports/quality", label: "质量发布", icon: ShieldCheck },
      { href: "/reports/batch", label: "批量分析", icon: FileStack },
      { href: "/reports/cross-checks", label: "交叉校验", icon: GitCompareArrows },
    ],
  },
] as const;

export function visibleGroups(role: string): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    links: group.links.filter((link) => !link.roles || link.roles.includes(role)),
  })).filter((group) => group.links.length > 0);
}

export function NavLinks({
  role,
  onNavigate,
}: {
  role: string;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();

  return (
    <nav aria-label="主导航" className="flex flex-col gap-5">
      {visibleGroups(role).map((group) => (
        <div key={group.title} className="flex flex-col gap-1">
          <p className="px-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {group.title}
          </p>
          {group.links.map((link) => {
            const Icon = link.icon;
            const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <Link
                key={link.href}
                href={link.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-11 items-center gap-2 rounded-md px-2 text-sm transition-colors duration-150 motion-reduce:transition-none",
                  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                  active
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0" />
                {link.label}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

export function AppSidebar({ role }: { role: string }) {
  return (
    <aside className="hidden w-52 shrink-0 border-r lg:block">
      <div className="sticky top-0 flex h-dvh flex-col gap-6 overflow-y-auto p-3">
        <Link href="/candidates" className="flex items-center gap-2 px-2 font-semibold">
          <ScrollText className="size-4" />
          SmartScreen
        </Link>
        <NavLinks role={role} />
      </div>
    </aside>
  );
}
