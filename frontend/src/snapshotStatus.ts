export type MoneyMarketMode = "live" | "usd-fallback" | "unavailable";

/** Choose only validated evidence. A pending request cannot hide a dated fallback. */
export function moneyMarketDisplay<T>(liveAtlas: T | null, usdFallback: T | null): {
  atlas: T | null;
  mode: MoneyMarketMode;
} {
  if (liveAtlas) return { atlas: liveAtlas, mode: "live" };
  if (usdFallback) return { atlas: usdFallback, mode: "usd-fallback" };
  return { atlas: null, mode: "unavailable" };
}

export function apiCheckStatus(checkedAt: string | null, refreshError: string | null): string {
  const lastSuccess = checkedAt ? `${checkedAt.slice(0, 16).replace("T", " ")} UTC` : null;
  if (refreshError) return lastSuccess ? `Refresh failed; last success ${lastSuccess}` : "Refresh failed; no successful API check";
  return lastSuccess ? `Last success ${lastSuccess}` : "Refresh pending";
}
