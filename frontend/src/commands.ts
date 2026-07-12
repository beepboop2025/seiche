// The command grammar. Two lanes, checked in order (the Bloomberg pattern:
// a mnemonic is an address, search is a fallback):
//   1. function codes — `FCT`, `SWELL`, `ASOF 2019-09-16` — exact, uppercase
//   2. free text — fuzzy-ranked against tab names, engine names, keywords
// Codes are editorial and live here, one row per destination.

export type Command =
  | { type: "tab"; tab: string }
  | { type: "asof"; date: string }
  | { type: "href"; url: string };

export interface Entry {
  code: string;        // the canonical function code
  title: string;
  hint: string;
  keywords: string;    // aliases + engine names that should land here
  run: Command;
}

export const ENTRIES: Entry[] = [
  { code: "DIS", title: "DISPATCHES", hint: "the daily letter", keywords: "letter desk note daily", run: { type: "tab", tab: "DISPATCHES" } },
  { code: "BRD", title: "BOARD", hint: "composite index · decomposition · ask", keywords: "dive index regime composite ask kink weather", run: { type: "tab", tab: "BOARD" } },
  { code: "FCT", title: "FORECAST", hint: "swell curve · stack ensemble · analogs", keywords: "swell stack tide tables analogs ml lab odds", run: { type: "tab", tab: "FORECAST" } },
  { code: "PHY", title: "PHYSICS", hint: "bathymetry · merian · gyre · rogue wave", keywords: "bathymetry merian gyre rogue langevin", run: { type: "tab", tab: "PHYSICS" } },
  { code: "HLM", title: "HELM", hint: "the Book — paper positions, walk-forward P&L", keywords: "book positions pnl sharpe", run: { type: "tab", tab: "HELM" } },
  { code: "MKT", title: "MARKET", hint: "the Tell — market-priced stress", keywords: "tell vix spreads price", run: { type: "tab", tab: "MARKET" } },
  { code: "GLO", title: "GLOBAL", hint: "basin coupling · swap lines · stablecoins", keywords: "basins swap lines moorings stablecoin crypto btc", run: { type: "tab", tab: "GLOBAL" } },
  { code: "CAL", title: "CALENDAR", hint: "forcing calendar — auctions, tax dates, turns", keywords: "auctions tax turn dates", run: { type: "tab", tab: "CALENDAR" } },
  { code: "POS", title: "POSITIONING", hint: "CFTC crowding · RV X-Ray", keywords: "cot crowding rvxray basis leverage", run: { type: "tab", tab: "POSITIONING" } },
  { code: "RES", title: "RESONANCE", hint: "calendar-forcing amplification · undertow", keywords: "undertow slowing resonance", run: { type: "tab", tab: "RESONANCE" } },
  { code: "TM", title: "TIME MACHINE", hint: "replay the board as of any date — or `ASOF 2019-09-12`", keywords: "asof replay history rewind", run: { type: "tab", tab: "TIME MACHINE" } },
  { code: "PRF", title: "PROOF", hint: "the backtest scoreboard · wrecks", keywords: "proof backtest scoreboard wrecks episodes record", run: { type: "tab", tab: "PROOF" } },
  { code: "SYS", title: "SYSTEM", hint: "feed health · faults", keywords: "health feeds faults sources status", run: { type: "tab", tab: "SYSTEM" } },
  { code: "ACC", title: "ACCOUNT", hint: "email alerts", keywords: "alerts email login account", run: { type: "tab", tab: "ACCOUNT" } },
  { code: "GUIDE", title: "GUIDE", hint: "how to read this terminal", keywords: "help onboarding manual docs", run: { type: "href", url: "/guide.html" } },
  { code: "SUP", title: "SUPPORT", hint: "keep Seiche free", keywords: "donate support crypto", run: { type: "href", url: "/support.html" } },
];

// Engine-name codes: typing the engine gets you to the tab that owns it.
const ALIAS_CODES: Record<string, string> = {
  DIVE: "BRD", KINK: "BRD", WEATHER: "BRD", ASK: "BRD",
  SWELL: "FCT", TIDE: "FCT", STACK: "FCT", ANALOGS: "FCT",
  BATHY: "PHY", GYRE: "PHY", ROGUE: "PHY", MERIAN: "PHY",
  BOOK: "HLM", TELL: "MKT", BASINS: "GLO", MOORINGS: "GLO",
  COT: "POS", RVX: "POS", UNDERTOW: "RES",
  ASOF: "TM", REPLAY: "TM", WRECKS: "PRF", PROOF: "PRF",
  FEEDS: "SYS", HEALTH: "SYS", ALERTS: "ACC", HELP: "GUIDE",
};

const CODE_RE = /^[A-Z][A-Z0-9]*$/;
const DATE_RE = /^(\d{4}-\d{2}-\d{2})$/;

/** Lane 1: exact function-code / `ASOF <date>` resolution. Null → fall through to fuzzy. */
export function parseCode(raw: string): Command | null {
  const q = raw.trim().toUpperCase();
  const asof = q.match(/^(?:ASOF|TM|REPLAY)\s+(\d{4}-\d{2}-\d{2})$/) ?? q.match(DATE_RE);
  if (asof) return { type: "asof", date: asof[1] };
  if (!CODE_RE.test(q)) return null;
  const code = ALIAS_CODES[q] ?? q;
  const hit = ENTRIES.find((e) => e.code === code);
  return hit ? hit.run : null;
}

/** Lane 2: rank for fuzzy search — prefix beats word-start beats substring. */
export function score(q: string, e: Entry): number {
  if (!q) return 1;
  const title = e.title.toLowerCase();
  const hay = `${e.code} ${title} ${e.keywords}`.toLowerCase();
  if (e.code.toLowerCase().startsWith(q) || title.startsWith(q)) return 100 - title.length * 0.01;
  if (hay.split(/\s+/).some((w) => w.startsWith(q))) return 60;
  if (hay.includes(q)) return 30;
  return -1;
}
