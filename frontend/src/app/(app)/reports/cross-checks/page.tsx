import { cookies } from "next/headers";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { CrossCheckView } from "@/components/cross-check-view";

export const dynamic = "force-dynamic";

export default async function CrossChecksPage() {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  // Backfill spends real money on provider calls, so it stays admin-only.
  const canBackfill = session?.role === "admin";
  return <CrossCheckView canBackfill={canBackfill} />;
}
