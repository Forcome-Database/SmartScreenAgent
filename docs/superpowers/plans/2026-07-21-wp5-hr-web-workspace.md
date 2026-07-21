# WP5 HR Web Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Next.js HR web workspace that completes the golden path — DingTalk login, resume upload (single + batch), job/batch status monitoring, ranked and flat candidate lists, candidate detail with authorized PII, scorecard with evidence, raw-file download, and re-score — over the frozen WP1/WP3/WP4 HTTP surface.

**Architecture:** A Next.js 15 App Router app in `frontend/` using a Backend-for-Frontend (BFF) pattern: the browser talks only to same-origin Next.js Route Handlers, which hold the JWT in an httpOnly cookie and proxy each call to FastAPI with a server-injected `Authorization: Bearer` header. Client server-state via TanStack Query; UI via shadcn/ui (Radix) + Tailwind v4; forms via react-hook-form + zod, with zod also validating upstream responses at the BFF boundary.

**Tech Stack:** Next.js 15 (App Router, React 19), TypeScript, Tailwind CSS v4, shadcn/ui (latest), TanStack Query v5, react-hook-form + zod, Vitest + React Testing Library, Playwright + axe-core.

## Global Constraints

- **BFF only:** the JWT lives ONLY in an httpOnly + Secure + SameSite=Lax cookie set server-side; it is NEVER written to a client-readable cookie, `localStorage`, or any response body. All FastAPI calls go through Next.js Route Handlers under `/api/*`; client components never call FastAPI directly.
- **No leak:** no token, presigned URL, object key, ciphertext, or provider body appears in client JavaScript, client-readable responses, or `console`/logs. The BFF forwards only explicitly-allowed fields.
- **PII discipline:** candidate LIST responses carry no PII and are rendered without any PII. PII appears only on the candidate detail view, reached by an explicit click; the detail fetch (`GET /api/v1/candidates/{id}`) writes exactly one `pii_decrypt` audit row per view (backend behavior — do not call it speculatively / on hover / prefetch).
- **Backend prefixes (unchanged, do not modify backend):** auth `/auth`; WP3 write/status `/api/v1/candidates` (`/upload`, `/batch`, `/jobs/{id}`, `/batches/{id}`, `/{id}/score`); WP4 reads `/api/v1` (`/candidates`, `/candidates/{id}`, `/candidates/{id}/scores/{sid}`, `/candidates/{id}/raw-file`, `/jds/{code}/candidates`).
- **Pagination:** offset-based `page` (1-based) + `page_size`; list envelope `{items, page, page_size, total}`.
- **Locale:** UI copy is zh-CN. Single locale; do not build multi-locale machinery.
- **Location:** all frontend code under `frontend/`. Do not touch `backend/`.
- **Gates before each commit:** `npm run lint`, `npx tsc --noEmit`, and the task's Vitest/Playwright tests must pass. Run commands from `frontend/`.
- **Server-only env:** `API_BASE_URL`, `SESSION_COOKIE_SECRET`, `DINGTALK_CLIENT_ID`, `DINGTALK_REDIRECT_URI`, `DINGTALK_AUTHORIZE_URL`. Only `DINGTALK_CLIENT_ID`/`DINGTALK_AUTHORIZE_URL`/`DINGTALK_REDIRECT_URI` may be exposed client-side (public), via `NEXT_PUBLIC_*` where a client component needs them; secrets stay server-only.

---

## File Structure

**Create (all under `frontend/`):**
- `package.json`, `tsconfig.json`, `next.config.ts`, `postcss.config.mjs`, `components.json`, `.eslintrc`/`eslint.config.mjs`, `.prettierrc`, `vitest.config.ts`, `vitest.setup.ts`, `playwright.config.ts`, `.env.example`, `Dockerfile`, `.dockerignore`.
- `src/app/` — App Router tree: `layout.tsx`, `globals.css`, `login/page.tsx`, `auth/callback/page.tsx`, `(app)/layout.tsx` (protected shell), `(app)/upload/page.tsx`, `(app)/candidates/page.tsx`, `(app)/candidates/[id]/page.tsx`, `(app)/candidates/[id]/scores/[sid]/page.tsx`, `(app)/jds/[code]/page.tsx`.
- `src/app/api/` — Route Handlers: `auth/callback/route.ts`, `auth/logout/route.ts`, `proxy/[...path]/route.ts` (generic authenticated proxy for reads/writes), `candidates/[id]/raw-file/route.ts` (special: presigned redirect).
- `src/lib/server/` — `env.ts`, `session.ts` (cookie encrypt/decrypt), `api.ts` (upstream fetch helper), `guards.ts`.
- `src/lib/` — `schemas.ts` (zod response models), `api-client.ts` (typed client-side fetchers hitting the BFF), `query.tsx` (QueryClient provider — `.tsx`, contains JSX), `utils.ts` (shadcn cn), `format.ts`.
- `src/components/ui/` — shadcn components (generated). `src/components/` — app components: `app-shell.tsx`, `paginated-list.tsx`, `data-state.tsx` (loading/empty/error), `candidate-table.tsx`, `ranked-table.tsx`, `scorecard.tsx`, `upload-dropzone.tsx`, `job-status.tsx`.
- `src/middleware.ts` — route guard.
- Tests: colocated `*.test.ts(x)` under `src/`, and `e2e/` for Playwright (`e2e/golden-path.spec.ts`, `e2e/a11y.spec.ts`).

**Modify (repo root):**
- `docker-compose.yml` (or a compose override) — add the `frontend` service.
- `README.md`, `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`, `docs/superpowers/plans/README.md` — WP5 status.

---

## Task 1: Scaffold the app, tooling, and test harness

**Files:** everything in `frontend/` config; a smoke test.

**Interfaces:**
- Produces: a Next.js 15 + Tailwind v4 + shadcn/ui app under `frontend/` with Vitest, Playwright, ESLint, Prettier wired; `@/*` path alias → `src/*`.

- [ ] **Step 1: Scaffold Next.js + Tailwind v4**

Run from the repo root:

```bash
npx create-next-app@latest frontend --ts --app --tailwind --eslint --src-dir --import-alias "@/*" --use-npm --no-turbopack
cd frontend
```

Accept defaults. This scaffolds Next 15 (React 19), Tailwind CSS v4 (`@tailwindcss/postcss`, `@import "tailwindcss"` in `src/app/globals.css`), and the `@/*` → `src/*` alias.

- [ ] **Step 2: Initialize shadcn/ui**

```bash
npx shadcn@latest init -d
npx shadcn@latest add button input label table card badge dropdown-menu sonner skeleton dialog select
```

`-d` accepts defaults (new-york style, CSS variables, neutral base). This writes `components.json`, updates `globals.css` with theme variables, and creates `src/lib/utils.ts` (`cn`).

- [ ] **Step 3: Add test + data deps**

```bash
npm i @tanstack/react-query zod react-hook-form @hookform/resolvers
npm i -D vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event @playwright/test @axe-core/playwright
npx playwright install --with-deps chromium
```

- [ ] **Step 4: Write Vitest config and setup**

```ts
// frontend/vitest.config.ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
});
```

```ts
// frontend/vitest.setup.ts
import "@testing-library/jest-dom/vitest";
```

Add scripts to `package.json`:

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest",
    "e2e": "playwright test"
  }
}
```

- [ ] **Step 5: Write the smoke test**

```tsx
// frontend/src/lib/utils.test.ts
import { describe, expect, it } from "vitest";
import { cn } from "@/lib/utils";

describe("cn", () => {
  it("merges class names and dedupes tailwind conflicts", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
    expect(cn("text-sm", false && "hidden", "font-bold")).toBe("text-sm font-bold");
  });
});
```

- [ ] **Step 6: Verify gates**

Run: `npm run test && npm run typecheck && npm run lint`
Expected: test passes; tsc clean; lint clean.

- [ ] **Step 7: Commit**

```bash
git add frontend
git commit -m "feat(wp5): scaffold Next.js 15 + Tailwind v4 + shadcn/ui frontend with test harness"
```

---

## Task 2: BFF upstream helper, env, and zod response schemas

**Files:**
- Create: `src/lib/server/env.ts`, `src/lib/server/api.ts`, `src/lib/schemas.ts`
- Test: `src/lib/server/api.test.ts`

**Interfaces:**
- Produces: `getServerEnv()`; `upstream(path, { method, token, query, body }) -> Promise<Response>`; `proxyJson(...) -> { status, body }` returning upstream JSON or a normalized `{ code, message }`; zod schemas `PageEnvelope`, `RankedCandidate`, `CandidateListItem`, `CandidateDetail`, `ScoreDetail`, `JobStatus`, `BatchStatus`, `LoginResult`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/lib/server/api.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { proxyJson } from "@/lib/server/api";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("proxyJson", () => {
  it("forwards bearer + query and returns upstream json", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toContain("/api/v1/candidates?page=2");
      expect((init.headers as Record<string, string>).Authorization).toBe("Bearer t0");
      return new Response(JSON.stringify({ items: [], page: 2, page_size: 20, total: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    const res = await proxyJson("/api/v1/candidates", {
      method: "GET",
      token: "t0",
      query: { page: "2" },
    });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ items: [], page: 2, page_size: 20, total: 0 });
  });

  it("normalizes an upstream error body to {code,message}", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ detail: { code: "not_found", message: "JD not found" } }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;
    const res = await proxyJson("/api/v1/jds/NOPE/candidates", { method: "GET", token: "t0" });
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ code: "not_found", message: "JD not found" });
  });

  it("maps a network failure to 502 upstream_unavailable", async () => {
    global.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const res = await proxyJson("/api/v1/candidates", { method: "GET", token: "t0" });
    expect(res.status).toBe(502);
    expect(res.body).toEqual({ code: "upstream_unavailable", message: expect.any(String) });
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `npm run test -- src/lib/server/api.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement env + api helper**

```ts
// frontend/src/lib/server/env.ts
import "server-only";

export interface ServerEnv {
  apiBaseUrl: string;
  sessionSecret: string;
  dingtalkClientId: string;
  dingtalkRedirectUri: string;
  dingtalkAuthorizeUrl: string;
}

export function getServerEnv(): ServerEnv {
  const apiBaseUrl = process.env.API_BASE_URL;
  const sessionSecret = process.env.SESSION_COOKIE_SECRET;
  const dingtalkClientId = process.env.DINGTALK_CLIENT_ID;
  const dingtalkRedirectUri = process.env.DINGTALK_REDIRECT_URI;
  const dingtalkAuthorizeUrl =
    process.env.DINGTALK_AUTHORIZE_URL ?? "https://login.dingtalk.com/oauth2/auth";
  if (!apiBaseUrl || !sessionSecret || !dingtalkClientId || !dingtalkRedirectUri) {
    throw new Error("Missing required server env (API_BASE_URL, SESSION_COOKIE_SECRET, DINGTALK_CLIENT_ID, DINGTALK_REDIRECT_URI)");
  }
  return { apiBaseUrl, sessionSecret, dingtalkClientId, dingtalkRedirectUri, dingtalkAuthorizeUrl };
}
```

```ts
// frontend/src/lib/server/api.ts
import "server-only";
import { getServerEnv } from "@/lib/server/env";

export interface UpstreamOptions {
  method: string;
  token?: string;
  query?: Record<string, string | undefined>;
  body?: unknown;
  headers?: Record<string, string>;
}

export interface ProxyResult {
  status: number;
  body: unknown;
}

function buildUrl(path: string, query?: Record<string, string | undefined>): string {
  const { apiBaseUrl } = getServerEnv();
  const url = new URL(path, apiBaseUrl);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== "") url.searchParams.set(k, v);
    }
  }
  return url.toString();
}

export async function upstream(path: string, opts: UpstreamOptions): Promise<Response> {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    if (opts.body instanceof FormData) {
      body = opts.body;
    } else {
      headers["content-type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
  }
  return fetch(buildUrl(path, opts.query), { method: opts.method, headers, body, cache: "no-store" });
}

export async function proxyJson(path: string, opts: UpstreamOptions): Promise<ProxyResult> {
  let res: Response;
  try {
    res = await upstream(path, opts);
  } catch {
    return { status: 502, body: { code: "upstream_unavailable", message: "后端服务暂不可用，请稍后重试" } };
  }
  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { code: "bad_upstream_response", message: "后端返回无法解析" };
      return { status: 502, body: parsed };
    }
  }
  if (res.ok) return { status: res.status, body: parsed };
  // Normalize FastAPI error shapes: {detail:{code,message}} | {detail:"msg"} | {code,message}
  const normalized = normalizeError(parsed, res.status);
  return { status: res.status, body: normalized };
}

function normalizeError(parsed: unknown, status: number): { code: string; message: string } {
  const fallback = { code: `http_${status}`, message: "请求失败" };
  if (!parsed || typeof parsed !== "object") return fallback;
  const obj = parsed as Record<string, unknown>;
  const detail = obj.detail;
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (typeof d.code === "string" && typeof d.message === "string") return { code: d.code, message: d.message };
  }
  if (typeof detail === "string") return { code: fallback.code, message: detail };
  if (typeof obj.code === "string" && typeof obj.message === "string") return { code: obj.code, message: obj.message };
  return fallback;
}
```

- [ ] **Step 4: Implement zod schemas**

```ts
// frontend/src/lib/schemas.ts
import { z } from "zod";

export const LoginResult = z.object({ token: z.string(), display_name: z.string(), role: z.string() });

export const RankedCandidate = z.object({
  candidate_id: z.number(),
  score_id: z.number(),
  total_score: z.number(),
  grade: z.string(),
  rule_version: z.string(),
  scored_at: z.string(),
});
export const CandidateListItem = z.object({
  candidate_id: z.number(),
  created_at: z.string(),
  latest_state: z.string().nullable(),
  scored_jd_codes: z.array(z.string()),
});
export const CandidateScoreSummary = z.object({
  score_id: z.number(),
  jd_code: z.string(),
  total_score: z.number(),
  grade: z.string(),
  rule_version: z.string(),
});
export const CandidateDetail = z.object({
  candidate_id: z.number(),
  name: z.string(),
  phone: z.string().nullable(),
  email: z.string().nullable(),
  age: z.number().nullable(),
  education: z.string().nullable(),
  experiences: z.array(z.record(z.string(), z.unknown())),
  source: z.string(),
  created_at: z.string(),
  scores: z.array(CandidateScoreSummary),
});
export const ScoreDetail = z.object({
  score_id: z.number(),
  candidate_id: z.number(),
  jd_code: z.string(),
  rule_version: z.string(),
  total_score: z.number(),
  grade: z.string(),
  hard_filter_result: z.record(z.string(), z.unknown()),
  rule_dimensions: z.record(z.string(), z.unknown()),
  judge_dimensions: z.record(z.string(), z.unknown()).nullable(),
});
export const JobStatus = z.object({
  job_id: z.string(),
  state: z.string(),
  candidate_id: z.number().nullable().optional(),
  last_error_code: z.string().nullable().optional(),
});
export const BatchStatus = z.object({
  batch_id: z.string(),
  jobs: z.array(z.object({ job_id: z.string(), state: z.string(), filename: z.string().optional() })).optional(),
});

export function pageEnvelope<T extends z.ZodTypeAny>(item: T) {
  return z.object({ items: z.array(item), page: z.number(), page_size: z.number(), total: z.number() });
}

const TERMINAL_JOB_STATES = new Set(["ready", "completed", "retryable_failed", "terminal_failed", "deleted"]);
export function isTerminalJobState(state: string): boolean {
  return TERMINAL_JOB_STATES.has(state);
}
```

- [ ] **Step 5: Run tests (pass)** — `npm run test -- src/lib/server/api.test.ts` → PASS.

- [ ] **Step 6: Gates + commit**

```bash
npm run typecheck && npm run lint
git add frontend/src/lib
git commit -m "feat(wp5): BFF upstream proxy helper, server env, and zod response schemas"
```

---

## Task 3: Session cookie, auth Route Handlers, and middleware guard

**Files:**
- Create: `src/lib/server/session.ts`, `src/app/api/auth/callback/route.ts`, `src/app/api/auth/logout/route.ts`, `src/middleware.ts`, `src/app/login/page.tsx`, `src/app/auth/callback/page.tsx`
- Test: `src/lib/server/session.test.ts`

**Interfaces:**
- Consumes: `proxyJson` (Task 2), `LoginResult` schema, `getServerEnv`.
- Produces: `createSession(payload) -> string` (signed cookie value), `readSession(value) -> Session | null`, `SESSION_COOKIE` name, `getSession()` (reads cookie in a server context); `Session = { token, displayName, role }`. The callback route sets the cookie; middleware redirects unauthenticated app routes to `/login`.

- [ ] **Step 1: Write the failing session test**

```ts
// frontend/src/lib/server/session.test.ts
import { describe, expect, it, beforeEach } from "vitest";
import { createSession, readSession } from "@/lib/server/session";

beforeEach(() => {
  process.env.SESSION_COOKIE_SECRET = "test-secret-test-secret-test-secret-xx";
});

describe("session cookie", () => {
  it("round-trips a signed session and rejects tampering", async () => {
    const value = await createSession({ token: "jwt123", displayName: "张三", role: "hr" });
    const back = await readSession(value);
    expect(back).toEqual({ token: "jwt123", displayName: "张三", role: "hr" });
    // tamper the payload
    const tampered = value.replace(/.$/, (c) => (c === "a" ? "b" : "a"));
    expect(await readSession(tampered)).toBeNull();
  });

  it("returns null for garbage", async () => {
    expect(await readSession("not-a-cookie")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it (fails)** — `npm run test -- src/lib/server/session.test.ts` → FAIL.

- [ ] **Step 3: Implement the signed session (HMAC via Web Crypto)**

```ts
// frontend/src/lib/server/session.ts
import "server-only";
import { getServerEnv } from "@/lib/server/env";

export const SESSION_COOKIE = "ssa_session";
export interface Session {
  token: string;
  displayName: string;
  role: string;
}

function b64url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("base64url");
}
function fromB64url(s: string): Uint8Array {
  return new Uint8Array(Buffer.from(s, "base64url"));
}

async function hmacKey(): Promise<CryptoKey> {
  const secret = getServerEnv().sessionSecret;
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function createSession(session: Session): Promise<string> {
  const payload = b64url(new TextEncoder().encode(JSON.stringify(session)));
  const key = await hmacKey();
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return `${payload}.${b64url(new Uint8Array(sig))}`;
}

export async function readSession(value: string | undefined | null): Promise<Session | null> {
  if (!value || !value.includes(".")) return null;
  const [payload, sig] = value.split(".");
  try {
    const key = await hmacKey();
    const ok = await crypto.subtle.verify("HMAC", key, fromB64url(sig), new TextEncoder().encode(payload));
    if (!ok) return null;
    const parsed = JSON.parse(new TextDecoder().decode(fromB64url(payload)));
    if (typeof parsed.token === "string" && typeof parsed.displayName === "string" && typeof parsed.role === "string") {
      return parsed as Session;
    }
    return null;
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Run tests (pass)** — PASS.

- [ ] **Step 5: Implement the callback + logout Route Handlers**

```ts
// frontend/src/app/api/auth/callback/route.ts
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
```

```ts
// frontend/src/app/api/auth/logout/route.ts
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/server/session";

export async function POST(): Promise<NextResponse> {
  (await cookies()).delete(SESSION_COOKIE);
  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 6: Implement middleware guard**

```ts
// frontend/src/middleware.ts
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/server/session";

export function middleware(req: NextRequest) {
  const hasSession = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  const { pathname } = req.nextUrl;
  const isPublic = pathname.startsWith("/login") || pathname.startsWith("/auth/callback") || pathname.startsWith("/api/auth");
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
```

Note: middleware only checks cookie PRESENCE (Edge runtime — no HMAC verify here for cheapness); the server routes/pages verify the signature via `readSession`. This is defense-in-depth, not the authority.

- [ ] **Step 7: Implement the login + callback pages**

```tsx
// frontend/src/app/login/page.tsx
import { getServerEnv } from "@/lib/server/env";
import { Button } from "@/components/ui/button";

export default function LoginPage() {
  const env = getServerEnv();
  const authorize = new URL(env.dingtalkAuthorizeUrl);
  authorize.searchParams.set("redirect_uri", env.dingtalkRedirectUri);
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("client_id", env.dingtalkClientId);
  authorize.searchParams.set("scope", "openid");
  authorize.searchParams.set("prompt", "consent");
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 p-6">
      <h1 className="text-2xl font-semibold">智能简历筛选 · HR 工作台</h1>
      <p className="text-muted-foreground">请使用钉钉登录</p>
      <Button asChild size="lg">
        <a href={authorize.toString()}>使用钉钉登录</a>
      </Button>
    </main>
  );
}
```

```tsx
// frontend/src/app/auth/callback/page.tsx
"use client";
import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

export default function CallbackPage() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const code = params.get("code");
    if (!code) {
      setError("缺少授权码");
      return;
    }
    void fetch("/api/auth/callback", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ auth_code: code }),
    }).then(async (r) => {
      if (r.ok) router.replace("/candidates");
      else {
        const body = (await r.json().catch(() => ({}))) as { message?: string };
        setError(body.message ?? "登录失败");
      }
    });
  }, [params, router]);
  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      {error ? <p className="text-destructive">{error}</p> : <p className="text-muted-foreground">登录中…</p>}
    </main>
  );
}
```

- [ ] **Step 8: Gates + commit**

```bash
npm run typecheck && npm run lint && npm run test -- src/lib/server/session.test.ts
git add frontend/src
git commit -m "feat(wp5): signed session cookie, DingTalk auth callback/logout routes, and middleware guard"
```

---

## Task 4: App shell, generic proxy route, query provider, and client API

**Files:**
- Create: `src/app/api/proxy/[...path]/route.ts`, `src/lib/query.ts`, `src/lib/api-client.ts`, `src/components/app-shell.tsx`, `src/components/data-state.tsx`, `src/app/(app)/layout.tsx`, `src/app/layout.tsx` (providers)
- Test: `src/lib/api-client.test.ts`

**Interfaces:**
- Consumes: session (Task 3), `proxyJson`/schemas (Task 2).
- Produces: authenticated generic proxy `GET|POST /api/proxy/<upstream-path>?<query>`; `apiGet(path, query, schema)` / `apiPost(path, body, schema)` client fetchers that call the proxy and zod-parse; `<AppShell>`, `<DataState>` (loading/empty/error wrapper), `<Providers>` (QueryClientProvider + Toaster).

- [ ] **Step 1: Write the failing client test**

```ts
// frontend/src/lib/api-client.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { apiGet, ApiError } from "@/lib/api-client";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

describe("apiGet", () => {
  it("hits the proxy with the encoded upstream path and zod-parses", async () => {
    global.fetch = vi.fn(async (url: string) => {
      expect(url).toBe("/api/proxy/api/v1/candidates?page=1");
      return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), { status: 200 });
    }) as unknown as typeof fetch;
    const schema = z.object({ total: z.number() });
    const out = await apiGet("/api/v1/candidates", { page: "1" }, schema);
    expect(out.total).toBe(0);
  });

  it("throws ApiError with {code,message} on non-2xx", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ code: "not_found", message: "JD not found" }), { status: 404 }),
    ) as unknown as typeof fetch;
    await expect(apiGet("/api/v1/jds/X/candidates", {}, z.unknown())).rejects.toMatchObject({
      code: "not_found",
      status: 404,
    });
  });
});
```

- [ ] **Step 2: Run it (fails)** — FAIL.

- [ ] **Step 3: Implement the generic proxy route**

```ts
// frontend/src/app/api/proxy/[...path]/route.ts
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { proxyJson } from "@/lib/server/api";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";

const ALLOWED_PREFIXES = ["/auth/", "/api/v1/"];

async function handle(req: Request, path: string[], method: string): Promise<NextResponse> {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) return NextResponse.json({ code: "unauthorized", message: "会话已失效，请重新登录" }, { status: 401 });
  const upstreamPath = "/" + path.join("/");
  if (!ALLOWED_PREFIXES.some((p) => upstreamPath.startsWith(p))) {
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
```

- [ ] **Step 4: Implement the client fetchers + query client**

```ts
// frontend/src/lib/api-client.ts
import type { z } from "zod";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

function toQuery(query?: Record<string, string | undefined>): string {
  if (!query) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== "") sp.set(k, v);
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function parseOrThrow<T>(res: Response, schema: z.ZodType<T>): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = body as { code?: string; message?: string };
    throw new ApiError(e.code ?? `http_${res.status}`, e.message ?? "请求失败", res.status);
  }
  return schema.parse(body);
}

export async function apiGet<T>(upstreamPath: string, query: Record<string, string | undefined>, schema: z.ZodType<T>): Promise<T> {
  const res = await fetch(`/api/proxy${upstreamPath}${toQuery(query)}`, { method: "GET" });
  return parseOrThrow(res, schema);
}

export async function apiPost<T>(upstreamPath: string, body: unknown, schema: z.ZodType<T>): Promise<T> {
  const res = await fetch(`/api/proxy${upstreamPath}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return parseOrThrow(res, schema);
}
```

```tsx
// frontend/src/lib/query.tsx
"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```

- [ ] **Step 5: Run tests (pass)** — PASS.

- [ ] **Step 6: Implement `<DataState>`, `<AppShell>`, layouts**

```tsx
// frontend/src/components/data-state.tsx
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";

export function DataState({
  isLoading,
  error,
  isEmpty,
  emptyText = "暂无数据",
  onRetry,
  children,
}: {
  isLoading: boolean;
  error?: { message: string } | null;
  isEmpty?: boolean;
  emptyText?: string;
  onRetry?: () => void;
  children: React.ReactNode;
}) {
  if (isLoading)
    return (
      <div className="space-y-2" aria-busy="true">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  if (error)
    return (
      <div role="alert" className="flex flex-col items-start gap-2 rounded-md border border-destructive/40 p-4">
        <p className="text-destructive">{error.message}</p>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            重试
          </Button>
        ) : null}
      </div>
    );
  if (isEmpty) return <p className="text-muted-foreground p-4">{emptyText}</p>;
  return <>{children}</>;
}
```

```tsx
// frontend/src/components/app-shell.tsx
import Link from "next/link";
import { LogoutButton } from "@/components/logout-button";

export function AppShell({ displayName, role, children }: { displayName: string; role: string; children: React.ReactNode }) {
  return (
    <div className="min-h-dvh">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <nav className="flex items-center gap-4">
          <Link href="/candidates" className="font-semibold">候选人</Link>
          <Link href="/upload" className="text-muted-foreground hover:text-foreground">上传</Link>
        </nav>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">{displayName}（{role}）</span>
          <LogoutButton />
        </div>
      </header>
      <main className="mx-auto max-w-6xl p-4">{children}</main>
    </div>
  );
}
```

```tsx
// frontend/src/components/logout-button.tsx
"use client";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";

export function LogoutButton() {
  const router = useRouter();
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={async () => {
        await fetch("/api/auth/logout", { method: "POST" });
        router.replace("/login");
      }}
    >
      退出
    </Button>
  );
}
```

```tsx
// frontend/src/app/layout.tsx  (root: providers + toaster + globals)
import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/lib/query";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = { title: "HR 工作台", description: "智能简历筛选 HR 工作台" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
        <Toaster />
      </body>
    </html>
  );
}
```

```tsx
// frontend/src/app/(app)/layout.tsx  (protected shell — verifies session server-side)
import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";
import { AppShell } from "@/components/app-shell";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) redirect("/login");
  return <AppShell displayName={session.displayName} role={session.role}>{children}</AppShell>;
}
```

- [ ] **Step 7: Gates + commit**

```bash
npm run typecheck && npm run lint && npm run test -- src/lib/api-client.test.ts
git add frontend/src
git commit -m "feat(wp5): authenticated proxy route, query provider, typed client, and app shell"
```

---

## Task 5: Candidate lists (flat + JD-ranked) with a shared paginated table

**Files:**
- Create: `src/components/paginated-list.tsx`, `src/components/candidate-table.tsx`, `src/components/ranked-table.tsx`, `src/app/(app)/candidates/page.tsx`, `src/app/(app)/jds/[code]/page.tsx`
- Test: `src/components/candidate-table.test.tsx`, `src/components/paginated-list.test.tsx`

**Interfaces:**
- Consumes: `apiGet`, schemas, `pageEnvelope`, `DataState`, TanStack Query.
- Produces: `usePaginatedQuery(key, upstreamPath, query, itemSchema)`; `<PaginatedList>` (pager + page-size, reads/writes `page`/`page_size` in the URL); `<CandidateTable>`, `<RankedTable>` (PII-free renderers).

- [ ] **Step 1: Write the failing table test (asserts NO PII columns)**

```tsx
// frontend/src/components/candidate-table.test.tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CandidateTable } from "@/components/candidate-table";

describe("CandidateTable", () => {
  const rows = [
    { candidate_id: 7, created_at: "2026-07-21T00:00:00Z", latest_state: "ready", scored_jd_codes: ["FT"] },
  ];
  it("renders id, state, and jd codes but no PII", () => {
    render(<CandidateTable rows={rows} />);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("FT")).toBeInTheDocument();
    // no PII column headers
    expect(screen.queryByText(/姓名|手机|邮箱|name|phone|email/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run it (fails)** — FAIL.

- [ ] **Step 3: Implement the shared paginated hook + list control**

```tsx
// frontend/src/components/paginated-list.tsx
"use client";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { z } from "zod";
import { apiGet } from "@/lib/api-client";
import { pageEnvelope } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { Button } from "@/components/ui/button";

export function usePageParams() {
  const params = useSearchParams();
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const pageSize = Math.min(100, Math.max(1, Number(params.get("page_size") ?? "20") || 20));
  return { page, pageSize };
}

export function PaginatedList<T>({
  queryKey,
  upstreamPath,
  extraQuery,
  itemSchema,
  render,
  emptyText,
}: {
  queryKey: unknown[];
  upstreamPath: string;
  extraQuery?: Record<string, string | undefined>;
  itemSchema: z.ZodType<T>;
  render: (rows: T[]) => React.ReactNode;
  emptyText?: string;
}) {
  const { page, pageSize } = usePageParams();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const envelope = pageEnvelope(itemSchema);
  const query = useQuery({
    queryKey: [...queryKey, page, pageSize, extraQuery],
    queryFn: () => apiGet(upstreamPath, { ...extraQuery, page: String(page), page_size: String(pageSize) }, envelope),
  });

  function goTo(nextPage: number) {
    const sp = new URLSearchParams(params.toString());
    sp.set("page", String(nextPage));
    router.push(`${pathname}?${sp.toString()}`);
  }

  const total = query.data?.total ?? 0;
  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="space-y-4">
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        isEmpty={query.data?.items.length === 0}
        emptyText={emptyText}
        onRetry={() => query.refetch()}
      >
        {query.data ? render(query.data.items) : null}
      </DataState>
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">共 {total} 条 · 第 {page}/{maxPage} 页</span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => goTo(page - 1)}>上一页</Button>
          <Button variant="outline" size="sm" disabled={page >= maxPage} onClick={() => goTo(page + 1)}>下一页</Button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement the two PII-free table renderers**

```tsx
// frontend/src/components/candidate-table.tsx
import Link from "next/link";
import { z } from "zod";
import { CandidateListItem } from "@/lib/schemas";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

type Row = z.infer<typeof CandidateListItem>;

export function CandidateTable({ rows }: { rows: Row[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>候选人 ID</TableHead>
          <TableHead>创建时间</TableHead>
          <TableHead>最新状态</TableHead>
          <TableHead>已评 JD</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.candidate_id}>
            <TableCell>
              <Link className="underline" href={`/candidates/${r.candidate_id}`}>{r.candidate_id}</Link>
            </TableCell>
            <TableCell>{new Date(r.created_at).toLocaleString("zh-CN")}</TableCell>
            <TableCell>{r.latest_state ?? "—"}</TableCell>
            <TableCell className="flex flex-wrap gap-1">
              {r.scored_jd_codes.map((c) => (
                <Badge key={c} variant="secondary">{c}</Badge>
              ))}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

```tsx
// frontend/src/components/ranked-table.tsx
import Link from "next/link";
import { z } from "zod";
import { RankedCandidate } from "@/lib/schemas";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

type Row = z.infer<typeof RankedCandidate>;

export function RankedTable({ rows }: { rows: Row[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>排名</TableHead>
          <TableHead>候选人 ID</TableHead>
          <TableHead>总分</TableHead>
          <TableHead>等级</TableHead>
          <TableHead>规则版本</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={r.score_id}>
            <TableCell>{i + 1}</TableCell>
            <TableCell>
              <Link className="underline" href={`/candidates/${r.candidate_id}`}>{r.candidate_id}</Link>
            </TableCell>
            <TableCell>{r.total_score.toFixed(2)}</TableCell>
            <TableCell><Badge>{r.grade}</Badge></TableCell>
            <TableCell>{r.rule_version}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 5: Implement the two list pages**

```tsx
// frontend/src/app/(app)/candidates/page.tsx
"use client";
import { Suspense } from "react";
import { PaginatedList } from "@/components/paginated-list";
import { CandidateListItem } from "@/lib/schemas";
import { CandidateTable } from "@/components/candidate-table";

export default function CandidatesPage() {
  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">候选人列表</h1>
      <Suspense>
        <PaginatedList
          queryKey={["candidates"]}
          upstreamPath="/api/v1/candidates"
          itemSchema={CandidateListItem}
          emptyText="还没有候选人，去上传简历"
          render={(rows) => <CandidateTable rows={rows} />}
        />
      </Suspense>
    </section>
  );
}
```

```tsx
// frontend/src/app/(app)/jds/[code]/page.tsx
"use client";
import { Suspense, use } from "react";
import { PaginatedList } from "@/components/paginated-list";
import { RankedCandidate } from "@/lib/schemas";
import { RankedTable } from "@/components/ranked-table";

export default function JdRankedPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">JD {code} · 候选人排名</h1>
      <Suspense>
        <PaginatedList
          queryKey={["jd-ranked", code]}
          upstreamPath={`/api/v1/jds/${code}/candidates`}
          itemSchema={RankedCandidate}
          emptyText="该 JD 暂无已评分候选人"
          render={(rows) => <RankedTable rows={rows} />}
        />
      </Suspense>
    </section>
  );
}
```

- [ ] **Step 6: Run tests (pass)** — `npm run test -- src/components/candidate-table.test.tsx` → PASS.

- [ ] **Step 7: Gates + commit**

```bash
npm run typecheck && npm run lint && npm run test
git add frontend/src
git commit -m "feat(wp5): flat and JD-ranked candidate lists with shared paginated PII-free tables"
```

---

## Task 6: Candidate detail, scorecard, raw-file download, and re-score

**Files:**
- Create: `src/app/(app)/candidates/[id]/page.tsx`, `src/app/(app)/candidates/[id]/scores/[sid]/page.tsx`, `src/components/scorecard.tsx`, `src/components/rescore-button.tsx`, `src/app/api/candidates/[id]/raw-file/route.ts`
- Test: `src/components/scorecard.test.tsx`, `src/components/rescore-button.test.tsx`

**Interfaces:**
- Consumes: `apiGet`/`apiPost`, `CandidateDetail`/`ScoreDetail` schemas, session/proxy.
- Produces: candidate detail (authorized PII, fetched only on this view), scorecard renderer with evidence, `<RescoreButton>` (POSTs then invalidates), and a raw-file Route Handler that fetches the presigned URL server-side and 302-redirects the browser to it (URL never enters client JS state).

- [ ] **Step 1: Write the failing scorecard test**

```tsx
// frontend/src/components/scorecard.test.tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Scorecard } from "@/components/scorecard";

describe("Scorecard", () => {
  it("renders grade, total, and judge evidence quotes", () => {
    render(
      <Scorecard
        detail={{
          score_id: 1,
          candidate_id: 7,
          jd_code: "FT",
          rule_version: "v1",
          total_score: 82,
          grade: "L4",
          hard_filter_result: { passed: true },
          rule_dimensions: { trade: { score: 20 } },
          judge_dimensions: {
            independence: { score: 8, evidence_quotes: ["独立负责北美大客户开发"], reasoning: "强" },
          },
        }}
      />,
    );
    expect(screen.getByText("L4")).toBeInTheDocument();
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getByText("独立负责北美大客户开发")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it (fails)** — FAIL.

- [ ] **Step 3: Implement the scorecard renderer**

```tsx
// frontend/src/components/scorecard.tsx
import { z } from "zod";
import { ScoreDetail } from "@/lib/schemas";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

type Detail = z.infer<typeof ScoreDetail>;

function DimensionBlock({ title, dims }: { title: string; dims: Record<string, unknown> | null }) {
  if (!dims) return null;
  return (
    <div className="space-y-2">
      <h3 className="font-medium">{title}</h3>
      {Object.entries(dims).map(([key, raw]) => {
        const d = (raw ?? {}) as { score?: number; evidence_quotes?: string[]; reasoning?: string };
        return (
          <div key={key} className="rounded-md border p-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{key}</span>
              {typeof d.score === "number" ? <Badge variant="secondary">{d.score}</Badge> : null}
            </div>
            {d.reasoning ? <p className="text-muted-foreground mt-1 text-sm">{d.reasoning}</p> : null}
            {d.evidence_quotes?.length ? (
              <ul className="mt-2 list-inside list-disc text-sm">
                {d.evidence_quotes.map((q, i) => (
                  <li key={i} className="text-foreground/80">“{q}”</li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function Scorecard({ detail }: { detail: Detail }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>JD {detail.jd_code} · 规则 {detail.rule_version}</CardTitle>
        <div className="flex items-center gap-2">
          <span className="text-2xl font-semibold">{detail.total_score}</span>
          <Badge>{detail.grade}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <DimensionBlock title="规则维度" dims={detail.rule_dimensions} />
        <DimensionBlock title="评委维度" dims={detail.judge_dimensions} />
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Run scorecard test (pass)** — PASS.

- [ ] **Step 5: Write + run the re-score button test**

```tsx
// frontend/src/components/rescore-button.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RescoreButton } from "@/components/rescore-button";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

function wrap(ui: React.ReactNode) {
  const client = new QueryClient();
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("RescoreButton", () => {
  it("POSTs the re-score endpoint through the proxy", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe("/api/proxy/api/v1/candidates/7/score");
      expect(init.method).toBe("POST");
      return new Response(JSON.stringify({ score_id: 9 }), { status: 200 });
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    render(wrap(<RescoreButton candidateId={7} jdCode="FT" />));
    await userEvent.click(screen.getByRole("button", { name: /重新评分/ }));
    expect(fetchMock).toHaveBeenCalled();
  });
});
```

```tsx
// frontend/src/components/rescore-button.tsx
"use client";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiPost, ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";

export function RescoreButton({ candidateId, jdCode }: { candidateId: number; jdCode?: string }) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      apiPost(
        `/api/v1/candidates/${candidateId}/score`,
        jdCode ? { jd_code: jdCode } : {},
        z.object({ score_id: z.number() }),
      ),
    onSuccess: () => {
      toast.success("已重新评分");
      void qc.invalidateQueries();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "重新评分失败"),
  });
  return (
    <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
      {mutation.isPending ? "评分中…" : "重新评分"}
    </Button>
  );
}
```

Run: `npm run test -- src/components/rescore-button.test.tsx` → PASS.

- [ ] **Step 6: Implement the detail page + scorecard page + raw-file route**

```tsx
// frontend/src/app/(app)/candidates/[id]/page.tsx
"use client";
import { use } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { CandidateDetail } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { RescoreButton } from "@/components/rescore-button";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function CandidateDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useQuery({
    queryKey: ["candidate", id],
    queryFn: () => apiGet(`/api/v1/candidates/${id}`, {}, CandidateDetail),
  });
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">候选人 #{id}</h1>
        <div className="flex gap-2">
          <Button variant="outline" asChild>
            <a href={`/api/candidates/${id}/raw-file`}>下载原始简历</a>
          </Button>
          <RescoreButton candidateId={Number(id)} />
        </div>
      </div>
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        onRetry={() => query.refetch()}
      >
        {query.data ? (
          <Card>
            <CardHeader>
              <CardTitle>{query.data.name}</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2 text-sm">
              <div>手机：{query.data.phone ?? "—"}</div>
              <div>邮箱：{query.data.email ?? "—"}</div>
              <div>学历：{query.data.education ?? "—"}</div>
              <div>年龄：{query.data.age ?? "—"}</div>
              <div className="col-span-2 pt-2 font-medium">评分</div>
              {query.data.scores.map((s) => (
                <Link key={s.score_id} className="underline" href={`/candidates/${id}/scores/${s.score_id}`}>
                  {s.jd_code}：{s.total_score}（{s.grade}）
                </Link>
              ))}
            </CardContent>
          </Card>
        ) : null}
      </DataState>
    </section>
  );
}
```

```tsx
// frontend/src/app/(app)/candidates/[id]/scores/[sid]/page.tsx
"use client";
import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { ScoreDetail } from "@/lib/schemas";
import { DataState } from "@/components/data-state";
import { Scorecard } from "@/components/scorecard";

export default function ScorecardPage({ params }: { params: Promise<{ id: string; sid: string }> }) {
  const { id, sid } = use(params);
  const query = useQuery({
    queryKey: ["score", id, sid],
    queryFn: () => apiGet(`/api/v1/candidates/${id}/scores/${sid}`, {}, ScoreDetail),
  });
  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">评分卡</h1>
      <DataState
        isLoading={query.isLoading}
        error={query.error ? { message: (query.error as Error).message } : null}
        onRetry={() => query.refetch()}
      >
        {query.data ? <Scorecard detail={query.data} /> : null}
      </DataState>
    </section>
  );
}
```

```ts
// frontend/src/app/api/candidates/[id]/raw-file/route.ts
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { z } from "zod";
import { proxyJson } from "@/lib/server/api";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";

const RawFileLink = z.object({ url: z.string(), expires_in_seconds: z.number() });

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) return NextResponse.json({ code: "unauthorized", message: "会话已失效" }, { status: 401 });
  const { id } = await ctx.params;
  const res = await proxyJson(`/api/v1/candidates/${id}/raw-file`, { method: "GET", token: session.token });
  if (res.status !== 200) return NextResponse.json(res.body, { status: res.status });
  const parsed = RawFileLink.safeParse(res.body);
  if (!parsed.success) return NextResponse.json({ code: "bad_upstream_response", message: "下载链接无法解析" }, { status: 502 });
  // Redirect the browser straight to the presigned URL; it never enters client JS state.
  return NextResponse.redirect(parsed.data.url, { status: 302 });
}
```

- [ ] **Step 7: Gates + commit**

```bash
npm run typecheck && npm run lint && npm run test
git add frontend/src
git commit -m "feat(wp5): candidate detail (audited PII), scorecard with evidence, raw-file redirect, re-score"
```

---

## Task 7: Upload + job/batch status monitoring

**Files:**
- Create: `src/components/upload-dropzone.tsx`, `src/components/job-status.tsx`, `src/lib/upload.ts`, `src/app/(app)/upload/page.tsx`, `src/app/api/upload/route.ts`, `src/app/api/batch/route.ts`
- Test: `src/lib/upload.test.ts`, `src/components/job-status.test.tsx`

**Interfaces:**
- Consumes: proxy/session, `JobStatus`/`BatchStatus` schemas, `isTerminalJobState`.
- Produces: multipart upload Route Handlers (`/api/upload`, `/api/batch`) that forward `FormData` with the bearer; `<UploadDropzone>`; `useJobStatus(jobId)` polling until terminal; `<JobStatus>` renderer.

- [ ] **Step 1: Write the failing status-polling test**

```tsx
// frontend/src/components/job-status.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobStatusView } from "@/components/job-status";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

describe("JobStatusView", () => {
  it("polls until a terminal state then shows it", async () => {
    const states = ["parsing", "extracting", "ready"];
    let i = 0;
    global.fetch = vi.fn(async () => {
      const state = states[Math.min(i, states.length - 1)];
      i += 1;
      return new Response(JSON.stringify({ job_id: "j1", state }), { status: 200 });
    }) as unknown as typeof fetch;
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <JobStatusView jobId="j1" pollMs={10} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/ready/)).toBeInTheDocument(), { timeout: 2000 });
  });
});
```

- [ ] **Step 2: Run it (fails)** — FAIL.

- [ ] **Step 3: Implement the polling view**

```tsx
// frontend/src/components/job-status.tsx
"use client";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { JobStatus, isTerminalJobState } from "@/lib/schemas";
import { Badge } from "@/components/ui/badge";

export function JobStatusView({ jobId, pollMs = 2000 }: { jobId: string; pollMs?: number }) {
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => apiGet(`/api/v1/candidates/jobs/${jobId}`, {}, JobStatus),
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s && isTerminalJobState(s) ? false : pollMs;
    },
  });
  const state = query.data?.state ?? "…";
  const terminal = query.data ? isTerminalJobState(query.data.state) : false;
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">任务 {jobId.slice(0, 8)}</span>
      <Badge variant={terminal ? (state === "ready" || state === "completed" ? "default" : "destructive") : "secondary"}>
        {state}
      </Badge>
    </div>
  );
}
```

- [ ] **Step 4: Run status test (pass)** — PASS.

- [ ] **Step 5: Implement the upload state helper + its test**

```ts
// frontend/src/lib/upload.ts
export interface UploadItem {
  file: File;
  status: "pending" | "uploading" | "queued" | "failed";
  jobId?: string;
  error?: string;
}

export function summarize(items: UploadItem[]): { queued: number; failed: number; pending: number } {
  return {
    queued: items.filter((i) => i.status === "queued").length,
    failed: items.filter((i) => i.status === "failed").length,
    pending: items.filter((i) => i.status === "pending" || i.status === "uploading").length,
  };
}
```

```ts
// frontend/src/lib/upload.test.ts
import { describe, expect, it } from "vitest";
import { summarize, type UploadItem } from "@/lib/upload";

describe("summarize", () => {
  it("counts queued/failed/pending", () => {
    const items: UploadItem[] = [
      { file: new File([""], "a.pdf"), status: "queued", jobId: "j1" },
      { file: new File([""], "b.pdf"), status: "failed", error: "x" },
      { file: new File([""], "c.pdf"), status: "uploading" },
    ];
    expect(summarize(items)).toEqual({ queued: 1, failed: 1, pending: 1 });
  });
});
```

Run: `npm run test -- src/lib/upload.test.ts` → PASS.

- [ ] **Step 6: Implement upload Route Handlers, dropzone, and page**

```ts
// frontend/src/app/api/upload/route.ts
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { proxyJson } from "@/lib/server/api";
import { readSession, SESSION_COOKIE } from "@/lib/server/session";

export async function POST(req: Request): Promise<NextResponse> {
  const session = await readSession((await cookies()).get(SESSION_COOKIE)?.value);
  if (!session) return NextResponse.json({ code: "unauthorized", message: "会话已失效" }, { status: 401 });
  const form = await req.formData();
  const res = await proxyJson("/api/v1/candidates/upload", { method: "POST", token: session.token, body: form });
  return NextResponse.json(res.body, { status: res.status });
}
```

```ts
// frontend/src/app/api/batch/route.ts
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
```

```tsx
// frontend/src/components/upload-dropzone.tsx
"use client";
import { useState } from "react";
import { z } from "zod";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { JobStatusView } from "@/components/job-status";

const UploadResult = z.object({ job_id: z.string() });

export function UploadDropzone() {
  const [jobs, setJobs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function onFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    const newJobs: string[] = [];
    for (const file of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch("/api/upload", { method: "POST", body: fd });
        const body = await res.json();
        if (!res.ok) {
          toast.error(`${file.name}：${body.message ?? "上传失败"}`);
          continue;
        }
        const parsed = UploadResult.safeParse(body);
        if (parsed.success) newJobs.push(parsed.data.job_id);
      } catch {
        toast.error(`${file.name}：网络错误`);
      }
    }
    setJobs((prev) => [...newJobs, ...prev]);
    setBusy(false);
    if (newJobs.length) toast.success(`已提交 ${newJobs.length} 份简历`);
  }

  return (
    <div className="space-y-4">
      <label className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed p-8 text-center">
        <span className="text-muted-foreground">选择或拖入 PDF/Word 简历（可多选）</span>
        <Input type="file" multiple accept=".pdf,.doc,.docx" disabled={busy} onChange={(e) => onFiles(e.target.files)} />
      </label>
      {jobs.length > 0 ? (
        <div className="space-y-2">
          <h2 className="font-medium">处理进度</h2>
          {jobs.map((j) => (
            <JobStatusView key={j} jobId={j} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
```

```tsx
// frontend/src/app/(app)/upload/page.tsx
import { UploadDropzone } from "@/components/upload-dropzone";

export default function UploadPage() {
  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">上传简历</h1>
      <UploadDropzone />
    </section>
  );
}
```

- [ ] **Step 7: Gates + commit**

```bash
npm run typecheck && npm run lint && npm run test
git add frontend/src
git commit -m "feat(wp5): resume upload with per-file job status polling to terminal state"
```

---

## Task 8: Golden-path e2e, accessibility, responsive, Docker, and WP5 exit review

**Files:**
- Create: `e2e/golden-path.spec.ts`, `e2e/a11y.spec.ts`, `e2e/fixtures/stub-backend.ts`, `frontend/Dockerfile`, `frontend/.dockerignore`, `frontend/.env.example`
- Modify: `playwright.config.ts`, root `docker-compose.yml`, `README.md`, roadmap + plan index.

**Interfaces:**
- Produces: a Playwright golden-path e2e that stubs FastAPI at the network boundary (Playwright route interception on `**/api/proxy/**` and `/api/auth/**` — OR runs Next against a stub upstream), asserts the flow and that no token/URL leaks into the DOM; axe checks; a production Docker image and compose service.

- [ ] **Step 1: Configure Playwright**

```ts
// frontend/playwright.config.ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:3100", trace: "on-first-retry" },
  webServer: {
    command: "npm run build && npm run start -- -p 3100",
    url: "http://127.0.0.1:3100/login",
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    env: {
      API_BASE_URL: "http://127.0.0.1:9",
      SESSION_COOKIE_SECRET: "e2e-secret-e2e-secret-e2e-secret-xx",
      DINGTALK_CLIENT_ID: "e2e",
      DINGTALK_REDIRECT_URI: "http://127.0.0.1:3100/auth/callback",
    },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
```

- [ ] **Step 2: Write the golden-path e2e (stubs the BFF's upstream via route interception)**

```ts
// frontend/e2e/golden-path.spec.ts
import { test, expect } from "@playwright/test";

// Stub every BFF call the browser makes (/api/proxy/**, /api/auth/**, /api/upload, /api/candidates/**/raw-file)
test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/callback", (r) =>
    r.fulfill({ status: 200, json: { displayName: "测试HR", role: "hr" }, headers: { "set-cookie": "ssa_session=stub; Path=/; HttpOnly" } }),
  );
  await page.route("**/api/proxy/api/v1/candidates?**", (r) =>
    r.fulfill({ status: 200, json: { items: [{ candidate_id: 7, created_at: "2026-07-21T00:00:00Z", latest_state: "ready", scored_jd_codes: ["FT"] }], page: 1, page_size: 20, total: 1 } }),
  );
  await page.route("**/api/proxy/api/v1/candidates/7", (r) =>
    r.fulfill({ status: 200, json: { candidate_id: 7, name: "张三", phone: "138", email: "a@b.c", age: 30, education: "本科", experiences: [], source: "upload", created_at: "2026-07-21T00:00:00Z", scores: [{ score_id: 9, jd_code: "FT", total_score: 82, grade: "L4", rule_version: "v1" }] } }),
  );
  await page.route("**/api/proxy/api/v1/candidates/7/scores/9", (r) =>
    r.fulfill({ status: 200, json: { score_id: 9, candidate_id: 7, jd_code: "FT", rule_version: "v1", total_score: 82, grade: "L4", hard_filter_result: { passed: true }, rule_dimensions: {}, judge_dimensions: { independence: { score: 8, evidence_quotes: ["独立负责北美客户"], reasoning: "强" } } } }),
  );
});

test("HR can log in and reach a scorecard with evidence, no token leak", async ({ page, context }) => {
  await context.addCookies([{ name: "ssa_session", value: "stub", url: "http://127.0.0.1:3100" }]);
  await page.goto("/candidates");
  await expect(page.getByText("候选人列表")).toBeVisible();
  await page.getByRole("link", { name: "7" }).click();
  await expect(page.getByText("张三")).toBeVisible();
  await page.getByRole("link", { name: /FT：82/ }).click();
  await expect(page.getByText("独立负责北美客户")).toBeVisible();
  // no bearer token or presigned URL leaked into the DOM
  const html = await page.content();
  expect(html).not.toMatch(/Bearer /);
  expect(html).not.toMatch(/X-Amz-Signature|presigned/i);
});
```

- [ ] **Step 3: Write the a11y e2e**

```ts
// frontend/e2e/a11y.spec.ts
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/proxy/api/v1/candidates?**", (r) =>
    r.fulfill({ status: 200, json: { items: [], page: 1, page_size: 20, total: 0 } }),
  );
});

test("candidate list has no serious/critical a11y violations", async ({ page, context }) => {
  await context.addCookies([{ name: "ssa_session", value: "stub", url: "http://127.0.0.1:3100" }]);
  await page.goto("/candidates");
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
});
```

- [ ] **Step 4: Run e2e**

Run: `npm run e2e`
Expected: desktop + mobile projects pass. (If cookie-presence middleware blocks `/candidates` before the stub cookie is set, the test adds the cookie via `context.addCookies` first — adjust the middleware matcher or the stub if needed.)

- [ ] **Step 5: Dockerfile + compose service**

```dockerfile
# frontend/Dockerfile
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

Set `output: "standalone"` in `next.config.ts`. Add a `frontend` service to the root `docker-compose.yml` depending on the backend, with the server-only env vars. Create `frontend/.env.example` documenting all env keys (no secrets).

- [ ] **Step 6: Full local gate**

```bash
cd frontend
npm run lint && npm run typecheck && npm run test && npm run e2e && npm run build
```

Expected: all green; the production build emits `.next/standalone`.

- [ ] **Step 7: Docs + roadmap**

Update `README.md` (frontend section: stack, `frontend/` dev/build/test commands, BFF auth model, env vars). In the roadmap and plan index mark WP5 **In progress** (NOT Complete); do not mark WP6 Ready until the gate passes.

- [ ] **Step 8: Commit**

```bash
git add frontend README.md docker-compose.yml docs/
git commit -m "test(wp5): golden-path e2e, a11y + responsive checks, Docker image, and docs"
```

---

## Task 9: Push, CI wiring (optional), and WP5 exit review

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin codex/wp5-hr-web-workspace
gh pr create --title "WP5: HR web workspace" --body "<summary + exit evidence>"
```

- [ ] **Step 2: If a frontend CI job is wired**, require it green (lint, typecheck, vitest, playwright, build). Otherwise record the full local gate output as the exit evidence.

- [ ] **Step 3: Record completion evidence** (commit range, test counts, build output) in this plan and the roadmap.

- [ ] **Step 4: Mark WP5 Complete and WP6 Ready for planning** only after every exit criterion passes.

---

## Self-Review

**Spec coverage:**
- §5 architecture/stack → Task 1 (scaffold), Task 2 (BFF), Task 4 (proxy/shell). §6 screens/routes → Tasks 3 (login/callback), 5 (lists), 6 (detail/scorecard/raw-file/re-score), 7 (upload/status). §7 auth/session → Task 3. §8 data flow/states → Tasks 4–7 (`DataState`, TanStack Query, polling, pagination URL sync). §9 PII/leak safety → Task 2 (BFF forwards only allowed fields), Task 5 (PII-free tables + test), Task 6 (detail is the only PII fetch; raw-file redirect keeps the URL server-side), Task 8 (e2e asserts no token/URL in DOM). §10 responsive/a11y → Task 8 (axe + mobile project). §11 testing → Tasks 1–8. §12 deployment → Task 8 (Docker + compose). §14 exit criteria → Tasks 8–9.

**Placeholder scan:** every code step contains real, current-version code. The one area an implementer must adapt at runtime is exact shadcn component file names and the Playwright/middleware cookie interplay in Task 8 Step 4 (noted inline), because those depend on the generated scaffold — flagged, not hand-waved.

**Type consistency:** schema names (`CandidateListItem`, `RankedCandidate`, `CandidateDetail`, `ScoreDetail`, `JobStatus`) and helpers (`apiGet`/`apiPost`/`ApiError`, `pageEnvelope`, `isTerminalJobState`, `proxyJson`, `readSession`/`createSession`/`SESSION_COOKIE`) are defined once (Tasks 2–4) and consumed consistently in Tasks 5–7. The BFF path convention (`/api/proxy` + upstream path) is uniform across `apiGet`/`apiPost` and the proxy route.
