import Link from "next/link";
import { LogoutButton } from "@/components/logout-button";

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
    <div className="min-h-dvh">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <nav className="flex items-center gap-4">
          <Link href="/candidates" className="font-semibold">
            候选人
          </Link>
          <Link href="/upload" className="text-muted-foreground hover:text-foreground">
            上传
          </Link>
        </nav>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">
            {displayName}（{role}）
          </span>
          <LogoutButton />
        </div>
      </header>
      <main className="mx-auto max-w-6xl p-4">{children}</main>
    </div>
  );
}
