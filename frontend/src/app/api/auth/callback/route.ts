import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { proxyJson } from "@/lib/server/api";
import { LoginResult } from "@/lib/schemas";
import { createSession, SESSION_COOKIE } from "@/lib/server/session";

export async function POST(req: Request): Promise<NextResponse> {
  const { auth_code } = (await req.json().catch(() => ({}))) as { auth_code?: string };
  if (!auth_code) return NextResponse.json({ code: "bad_request", message: "缺少 auth_code" }, { status: 400 });
  const res = await proxyJson("/auth/dingtalk/login", { method: "POST", body: { auth_code } });
  if (res.status !== 200) return NextResponse.json(res.body, { status: res.status });
  const parsed = LoginResult.safeParse(res.body);
  if (!parsed.success) return NextResponse.json({ code: "bad_upstream_response", message: "登录响应无法解析" }, { status: 502 });
  const cookie = await createSession({ token: parsed.data.token, displayName: parsed.data.display_name, role: parsed.data.role });
  const out = NextResponse.json({ displayName: parsed.data.display_name, role: parsed.data.role });
  (await cookies()).set(SESSION_COOKIE, cookie, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  return out;
}
