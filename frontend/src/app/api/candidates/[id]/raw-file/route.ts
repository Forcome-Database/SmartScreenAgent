import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { z } from "zod";
import { proxyJson } from "@/lib/server/api";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { sessionExpiredResponse } from "@/lib/server/proxy-response";

const RawFileLink = z.object({ url: z.string(), expires_in_seconds: z.number() });

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) return NextResponse.json({ code: "unauthorized", message: "会话已失效" }, { status: 401 });
  const { id } = await ctx.params;
  const res = await proxyJson(`/api/v1/candidates/${id}/raw-file`, { method: "GET", token: session.token });
  if (res.status === 401) return sessionExpiredResponse();
  if (res.status !== 200) return NextResponse.json(res.body, { status: res.status });
  const parsed = RawFileLink.safeParse(res.body);
  if (!parsed.success) {
    return NextResponse.json({ code: "bad_upstream_response", message: "下载链接无法解析" }, { status: 502 });
  }
  // Redirect the browser straight to the presigned URL; it never enters client JS state.
  return NextResponse.redirect(parsed.data.url, { status: 302 });
}
