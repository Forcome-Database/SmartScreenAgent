const ALLOWED_PREFIXES = ["/api/v1/"];

/**
 * Resolve catch-all proxy segments to a safe upstream path, or null if the
 * request must be rejected (403). Rejects any empty/"."/".." segment, and
 * any segment containing a slash or backslash BEFORE joining — Next.js
 * hands catch-all segments already decodeURIComponent'd, so an encoded
 * slash (e.g. `..%2f..`) arrives as the single segment `"../.."`, which
 * contains a slash but isn't exactly `".."` and would otherwise slip past
 * the exact-match check and collapse past the allowlist once new URL()
 * normalizes it. Requires the normalized path to be under the allowlist.
 */
export function safeUpstreamPath(segments: string[]): string | null {
  if (segments.length === 0) return null;
  if (
    segments.some(
      (s) => s === "" || s === "." || s === ".." || s.includes("/") || s.includes("\\"),
    )
  ) {
    return null;
  }
  const path = "/" + segments.join("/");
  // Belt-and-suspenders: after URL normalization the path must still be under
  // the allowlist (defends against any ../ that slipped through).
  const normalized = new URL(path, "http://internal.invalid").pathname;
  if (!ALLOWED_PREFIXES.some((p) => normalized.startsWith(p))) return null;
  return path;
}
