import { cookies } from "next/headers";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { QualityReleasesView } from "@/components/quality/quality-release-view";

export const dynamic = "force-dynamic";

export default async function QualityReleasesPage() {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  // Creating a release freezes a permanent record, so it is a lead/admin action.
  const canCreate = session?.role === "hr_lead" || session?.role === "admin";
  return <QualityReleasesView canCreate={canCreate} />;
}
