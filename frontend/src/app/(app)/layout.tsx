import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { AppShell } from "@/components/app-shell";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) redirect("/login");
  return (
    <AppShell displayName={session.displayName} role={session.role}>
      {children}
    </AppShell>
  );
}
