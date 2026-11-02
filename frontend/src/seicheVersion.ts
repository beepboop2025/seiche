/**
 * The running Seiche release, read from /api/health at runtime so the page never
 * carries a hand-maintained version number. "0.16.11 estuary" reads as "0.16.11".
 */
export function releaseVersion(health: unknown): string | null {
  if (!health || typeof health !== "object" || Array.isArray(health)) return null;
  const version = (health as Record<string, unknown>).version;
  if (typeof version !== "string") return null;
  const match = /^(\d+\.\d+\.\d+)(?:\s|$)/.exec(version.trim());
  return match ? match[1] : null;
}
