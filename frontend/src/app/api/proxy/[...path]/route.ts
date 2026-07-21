import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { proxyJson } from "@/lib/server/api";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { safeUpstreamPath } from "@/lib/server/upstream-path";

async function handle(req: Request, path: string[], method: string): Promise<NextResponse> {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) return NextResponse.json({ code: "unauthorized", message: "会话已失效，请重新登录" }, { status: 401 });
  const upstreamPath = safeUpstreamPath(path);
  if (!upstreamPath) {
    return NextResponse.json({ code: "forbidden_path", message: "非法请求路径" }, { status: 403 });
  }
  const url = new URL(req.url);
  const query: Record<string, string> = {};
  url.searchParams.forEach((v, k) => (query[k] = v));
  const body = method === "GET" ? undefined : await req.json().catch(() => undefined);
  const res = await proxyJson(upstreamPath, { method, token: session.token, query, body });
  if (res.status === 401) {
    // upstream rejected the token: clear the session
    const out = NextResponse.json({ code: "unauthorized", message: "会话已失效，请重新登录" }, { status: 401 });
    out.cookies.delete(SESSION_COOKIE);
    return out;
  }
  return NextResponse.json(res.body, { status: res.status });
}

export async function GET(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(req, (await ctx.params).path, "GET");
}
export async function POST(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(req, (await ctx.params).path, "POST");
}
