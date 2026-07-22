import { cookies } from "next/headers";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { GoldenSetView } from "@/components/golden-set-view";

export const dynamic = "force-dynamic";

export default async function GoldenSetPage() {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  const canImport = session?.role === "hr_lead" || session?.role === "admin";
  return <GoldenSetView canImport={canImport} />;
}
