/** Stable, crawler-visible routes for public share cards.
 *
 * SPA fragments remain navigation state only. Every builder here returns a
 * real server path that the publish-time social-card job owns, so a share
 * action cannot accidentally hand an unfurler `/#...`.
 */

export type WorldMarketView =
  | "summary"
  | "money-markets"
  | "forex"
  | "capital-markets"
  | "china-macro"
  | "sources"
  | "methodology"
  | "all";

/** Every finite public tab inherits one crawler-visible card route. Exact
 * series/market/card routes declared deeper in the DOM still win via
 * Element.closest(). The three excluded tabs are intentionally not shareable:
 * CORPUS is unbounded and rights-aware, TIME MACHINE is an arbitrary request-
 * time reconstruction, and ACCOUNT contains private viewer state. */
const UI_TAB_SHARE_PATHS: Readonly<Record<string, string>> = Object.freeze({
  TODAY: "/views/tabs/today/",
  DISPATCHES: "/views/tabs/dispatches/",
  BOARD: "/views/board/composite/",
  "MONEY MARKETS": "/views/money-markets/overview/",
  GLOBAL: "/views/world-markets/summary/",
  "FX×MATERIALS": "/views/tabs/fx-materials/",
  "OIL×FUNDING": "/views/tabs/oil-funding/",
  SCARCITY: "/views/tabs/scarcity/",
  SUPPLY: "/views/tabs/supply/",
  FORECAST: "/views/tabs/forecast/",
  PHYSICS: "/views/tabs/physics/",
  HELM: "/views/tabs/helm/",
  MARKET: "/views/tabs/market/",
  CALENDAR: "/views/tabs/calendar/",
  POSITIONING: "/views/tabs/positioning/",
  RESONANCE: "/views/tabs/resonance/",
  PROOF: "/views/tabs/proof/",
  REFEREE: "/views/tabs/referee/",
  SYSTEM: "/views/tabs/system/",
});

export const UNSHAREABLE_UI_TABS: Readonly<Record<string, string>> = Object.freeze({
  CORPUS: "unbounded rights-aware dataset registry",
  "TIME MACHINE": "arbitrary request-time historical reconstruction",
  ACCOUNT: "private viewer and credential state",
});

const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

function exactSegment(value: string, label: string): string {
  const segment = value.trim();
  if (!SAFE_SEGMENT.test(segment) || segment === "." || segment === "..") {
    throw new Error(`invalid ${label} share-route segment`);
  }
  return segment;
}

export function boardSharePath(): string {
  return "/views/board/composite/";
}

export function seriesSharePath(identifier: string): string {
  const slug = identifier
    .trim()
    .toLowerCase()
    .replace(/_/g, "-")
    .replace(/[^a-z0-9.-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `/views/series/${exactSegment(slug, "series")}/`;
}

export function moneyMarketSharePath(marketId = "overview"): string {
  return `/views/money-markets/${exactSegment(marketId, "money-market")}/`;
}

export function worldMarketSharePath(view: WorldMarketView): string {
  return `/views/world-markets/${view}/`;
}

export function dispatchSharePath(slug: string): string {
  return `/dispatches/${exactSegment(slug, "dispatch")}`;
}

export function articleSharePath(slug: string): string {
  return `/articles/${exactSegment(slug, "article")}/`;
}

export function tabSharePath(tab: string): string | null {
  const key = tab.trim().toUpperCase();
  return UI_TAB_SHARE_PATHS[key] ?? null;
}

export function stableShareUrl(
  path: string,
  origin = window.location.origin,
): string {
  if (!path.startsWith("/") || path.startsWith("//") || /[?#]/.test(path)) {
    throw new Error("stable share routes must be same-origin paths without query or fragment state");
  }
  const base = new URL("/", origin);
  const resolved = new URL(path, base);
  if (resolved.origin !== base.origin || resolved.hash || resolved.search) {
    throw new Error("stable share route escaped its publication origin");
  }
  return resolved.href;
}
