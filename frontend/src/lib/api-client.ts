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

export async function apiGet<T>(
  upstreamPath: string,
  query: Record<string, string | undefined>,
  schema: z.ZodType<T>,
): Promise<T> {
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
