import { cookies } from "next/headers";
import { RuleManagementView } from "@/components/rule-management-view";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";

export const dynamic = "force-dynamic";

export default async function RulesPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  const canManage = session?.role === "hr_lead" || session?.role === "admin";
  return <RuleManagementView code={code} canManage={canManage} />;
}
