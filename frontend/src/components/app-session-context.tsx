"use client";

import * as React from "react";

export type Breadcrumb = { label: string; href?: string };

type HeaderState = {
  breadcrumbs: Breadcrumb[];
  lastRefreshedAt: string | null;
};

type SessionValue = {
  displayName: string;
  role: string;
  header: HeaderState;
  setHeader: (next: HeaderState) => void;
};

const DEFAULT_HEADER: HeaderState = { breadcrumbs: [], lastRefreshedAt: null };

const AppSessionContext = React.createContext<SessionValue | null>(null);

export function AppSessionProvider({
  displayName,
  role,
  children,
}: {
  displayName: string;
  role: string;
  children: React.ReactNode;
}) {
  const [header, setHeader] = React.useState<HeaderState>(DEFAULT_HEADER);
  const value = React.useMemo(
    () => ({ displayName, role, header, setHeader }),
    [displayName, role, header],
  );
  return <AppSessionContext.Provider value={value}>{children}</AppSessionContext.Provider>;
}

export function useAppSession(): SessionValue {
  const value = React.useContext(AppSessionContext);
  if (!value) throw new Error("useAppSession must be used inside AppSessionProvider");
  return value;
}

/**
 * Register this page's breadcrumb and freshness stamp with the shell header.
 *
 * The cleanup restores the default so a page that unmounts can never leave a
 * stale "last refreshed" claim visible above the next page's content.
 */
export function useShellHeader({
  breadcrumbs,
  lastRefreshedAt,
}: {
  breadcrumbs: Breadcrumb[];
  lastRefreshedAt?: string | null;
}): void {
  const { setHeader } = useAppSession();
  const serialized = JSON.stringify(breadcrumbs);

  React.useEffect(() => {
    setHeader({
      breadcrumbs: JSON.parse(serialized) as Breadcrumb[],
      lastRefreshedAt: lastRefreshedAt ?? null,
    });
    return () => setHeader(DEFAULT_HEADER);
  }, [serialized, lastRefreshedAt, setHeader]);
}
