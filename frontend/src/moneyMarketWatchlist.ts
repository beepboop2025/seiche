/** Local research preferences only. No observations, source clocks or scores are stored here. */
export const MONEY_MARKET_WATCH_KEY = "seiche.money-market-watch.v1";
export const MAX_WATCHED_MARKETS = 12;

export interface MarketWatchState {
  ids: string[];
  remembered: boolean;
  notice: string;
}

export interface WatchStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export type WatchStorageAccess = () => WatchStorage;

export function validMarketWatchId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Z0-9][A-Z0-9._-]{0,79}$/.test(value);
}

export function parseMarketWatch(raw: string | null): string[] | null {
  if (raw === null || raw.length > 2048) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || Array.isArray(value) || Object.keys(value).length !== 2
      || value.schema !== MONEY_MARKET_WATCH_KEY || !Array.isArray(value.ids)
      || value.ids.length > MAX_WATCHED_MARKETS || !value.ids.every(validMarketWatchId)
      || new Set(value.ids).size !== value.ids.length) return null;
    return value.ids;
  } catch { return null; }
}

function encode(ids: string[]): string {
  const raw = JSON.stringify({ schema: MONEY_MARKET_WATCH_KEY, ids });
  if (parseMarketWatch(raw) === null) throw new TypeError("Invalid market watch choices");
  return raw;
}

export function readMarketWatch(storage: WatchStorageAccess): MarketWatchState {
  try {
    const raw = storage().getItem(MONEY_MARKET_WATCH_KEY), ids = parseMarketWatch(raw);
    return { ids: ids || [], remembered: ids !== null,
      notice: raw !== null && ids === null ? "Saved choices could not be read. Choose markets again; no market values were loaded."
        : ids !== null ? "Saved market choices loaded. Evidence comes from the current atlas."
          : "Choices stay in this tab unless you choose to remember them." };
  } catch {
    return { ids: [], remembered: false, notice: "Browser storage is unavailable. You can still watch markets in this tab." };
  }
}

/** Apply an explicit action to the latest saved list so another tab's newer choices survive. */
export function changeMarketWatch(state: MarketWatchState, id: string, shouldWatch: boolean,
  storage: WatchStorageAccess): MarketWatchState {
  if (!validMarketWatchId(id)) return { ...state, notice: "This market ID cannot be saved." };
  let ids = state.ids, remembered = state.remembered;
  if (remembered) {
    try {
      const latest = parseMarketWatch(storage().getItem(MONEY_MARKET_WATCH_KEY));
      ids = latest || []; remembered = latest !== null;
    } catch { return { ...state, notice: "The browser could not read saved choices. Your watchlist was kept as it was." }; }
  }
  const next = shouldWatch ? ids.includes(id) ? ids : [...ids, id] : ids.filter(item => item !== id);
  if (next.length > MAX_WATCHED_MARKETS) return { ...state, notice: `Watch up to ${MAX_WATCHED_MARKETS} markets. Remove a market to add another.` };
  if (remembered) {
    try { storage().setItem(MONEY_MARKET_WATCH_KEY, encode(next)); }
    catch { return { ...state, notice: "The browser could not save that change. Your watchlist was kept as it was." }; }
  }
  return { ids: next, remembered, notice: remembered ? "Market choices saved on this device."
    : state.remembered ? "Saving was turned off in another tab. These choices now stay in this tab only."
      : "Market choices updated in this tab. Choose remembering to keep them for your next visit." };
}

export function rememberMarketWatch(state: MarketWatchState, storage: WatchStorageAccess): MarketWatchState {
  try {
    storage().setItem(MONEY_MARKET_WATCH_KEY, encode(state.ids));
    return { ...state, remembered: true, notice: "Only market IDs are saved on this device. No background checks or notifications are added." };
  } catch { return { ...state, notice: "The browser could not save these choices. Your current watchlist remains in this tab." }; }
}

/** A denied storage deletion never prevents clearing this tab or turning off further saving. */
export function forgetMarketWatch(state: MarketWatchState, storage: WatchStorageAccess, clear = false): MarketWatchState {
  let removed = true;
  try { storage().removeItem(MONEY_MARKET_WATCH_KEY); } catch { removed = false; }
  return { ids: clear ? [] : state.ids, remembered: false,
    notice: removed ? clear ? "Watchlist cleared from this tab and this device."
      : "Saved choices removed. Your current watchlist stays in this tab."
      : "Saving is off in this tab. The browser could not remove saved choices; clear this site's storage in browser settings to remove them." };
}

export function filterWatchedMarkets<T extends { market_id: string; region: string }>(
  markets: T[], ids: string[], watchedOnly: boolean, region = "ALL",
): T[] {
  const selected = new Set(ids);
  return markets.filter(market => (region === "ALL" || market.region === region)
    && (!watchedOnly || selected.has(market.market_id)));
}

export function missingWatchedMarkets(markets: Array<{ market_id: string }>, ids: string[]): string[] {
  const present = new Set(markets.map(market => market.market_id));
  return ids.filter(id => !present.has(id));
}
