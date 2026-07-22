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
