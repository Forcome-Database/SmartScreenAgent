import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { proxyJson } from "@/lib/server/api";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";

export async function POST(req: Request): Promise<NextResponse> {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) return NextResponse.json({ code: "unauthorized", message: "会话已失效" }, { status: 401 });
  const form = await req.formData();
  const res = await proxyJson("/api/v1/candidates/batch", { method: "POST", token: session.token, body: form });
  return NextResponse.json(res.body, { status: res.status });
}
