import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/server/session";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  // Never HTML-redirect API requests: client `fetch('/api/...')` must see a JSON
  // response (401 from the route handler's own session check), not a 307 to /login.
  // /api/auth/* also must stay reachable unauthenticated (login/callback/logout).
  if (pathname.startsWith("/api")) return NextResponse.next();

  const hasSession = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  const isPublic = pathname.startsWith("/login") || pathname.startsWith("/auth/callback");
  if (!hasSession && !isPublic) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
