"use client";

import Link from "next/link";

import { AppSidebar } from "@/components/app-sidebar";
import { AppSessionProvider, useAppSession } from "@/components/app-session-context";
import { MobileAppNav } from "@/components/mobile-app-nav";
import { LogoutButton } from "@/components/logout-button";

function ShellHeader() {
  const { displayName, role, header } = useAppSession();

  return (
    <header className="sticky top-0 z-40 flex min-h-14 items-center justify-between gap-3 border-b bg-background px-4">
      <div className="flex items-center gap-3">
        <MobileAppNav role={role} />
        <nav aria-label="面包屑" className="text-sm">
          <ol className="flex items-center gap-1">
            {header.breadcrumbs.length === 0 ? (
              <li className="font-medium">SmartScreen</li>
            ) : (
              header.breadcrumbs.map((crumb, index) => (
                <li key={`${crumb.label}-${index}`} className="flex items-center gap-1">
                  {index > 0 && <span className="text-muted-foreground">/</span>}
                  {crumb.href ? (
                    <Link href={crumb.href} className="hover:underline">
                      {crumb.label}
                    </Link>
                  ) : (
                    <span className="font-medium">{crumb.label}</span>
                  )}
                </li>
              ))
            )}
          </ol>
        </nav>
      </div>
      <div className="flex items-center gap-3 text-sm">
        {header.lastRefreshedAt && (
          <span
            className="hidden text-muted-foreground sm:inline"
            data-testid="last-refreshed"
          >
            更新于 {header.lastRefreshedAt}
          </span>
        )}
        <span className="text-muted-foreground">
          {displayName}（{role}）
        </span>
        <LogoutButton />
      </div>
    </header>
  );
}

export function AppShell({
  displayName,
  role,
  children,
}: {
  displayName: string;
  role: string;
  children: React.ReactNode;
}) {
  return (
    <AppSessionProvider displayName={displayName} role={role}>
      <div className="flex min-h-dvh">
        <AppSidebar role={role} />
        <div className="flex min-w-0 flex-1 flex-col">
          <ShellHeader />
          {/* Fluid width with a readable ceiling — reports need room to breathe. */}
          <main className="w-full flex-1 p-4 lg:p-6">
            <div className="mx-auto max-w-[90rem]">{children}</div>
          </main>
        </div>
      </div>
    </AppSessionProvider>
  );
}
