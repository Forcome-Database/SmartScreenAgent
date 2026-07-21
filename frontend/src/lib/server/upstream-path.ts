const ALLOWED_PREFIXES = ["/api/v1/"];

/**
 * Resolve catch-all proxy segments to a safe upstream path, or null if the
 * request must be rejected (403). Rejects any empty/"."/".." segment BEFORE
 * joining (so a traversal like ../ can't collapse past the allowlist once
 * new URL() normalizes it), and requires an allowed prefix.
 */
export function safeUpstreamPath(segments: string[]): string | null {
  if (segments.length === 0) return null;
  if (segments.some((s) => s === "" || s === "." || s === "..")) return null;
  const path = "/" + segments.join("/");
  if (!ALLOWED_PREFIXES.some((p) => path.startsWith(p))) return null;
  return path;
}
