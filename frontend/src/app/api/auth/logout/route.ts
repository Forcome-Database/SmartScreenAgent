import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/server/session";

export async function POST(): Promise<NextResponse> {
  (await cookies()).delete(SESSION_COOKIE);
  return NextResponse.json({ ok: true });
}
