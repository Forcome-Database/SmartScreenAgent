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
function fromB64url(s: string): Uint8Array<ArrayBuffer> {
  // Node's Buffer is a Uint8Array<ArrayBufferLike> (ArrayBufferLike includes
  // SharedArrayBuffer), which TS 5.7+'s stricter BufferSource typing rejects
  // for crypto.subtle.*. Copy into a fresh Uint8Array<ArrayBuffer> instead.
  const buf = Buffer.from(s, "base64url");
  const out = new Uint8Array(buf.length);
  out.set(buf);
  return out;
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
