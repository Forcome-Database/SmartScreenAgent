import "server-only";
import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/server/session";

/** 401 response that also clears the session cookie — used whenever the
 *  upstream FastAPI rejects the forwarded token, so the client redirects to
 *  /login instead of silently retrying with a dead session. */
export function sessionExpiredResponse(): NextResponse {
  const out = NextResponse.json(
    { code: "unauthorized", message: "会话已失效，请重新登录" },
    { status: 401 },
  );
  out.cookies.delete(SESSION_COOKIE);
  return out;
}
