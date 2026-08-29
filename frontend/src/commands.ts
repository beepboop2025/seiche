// The command grammar. Two lanes, checked in order (the Bloomberg pattern:
// a mnemonic is an address, search is a fallback):
//   1. function codes — `FCT`, `SWELL`, `ASOF 2019-09-16` — exact, uppercase
//   2. free text — fuzzy-ranked against tab names, engine names, keywords
// Codes are editorial and live here, one row per destination.

export type Command =
  | { type: "tab"; tab: string }
  | { type: "asof"; date: string }
  | { type: "href"; url: string }
  | { type: "depth"; level: "glance" | "desk" | "deep" };

export interface Entry {
  code: string;        // the canonical function code
  title: string;
  hint: string;
  keywords: string;    // aliases + engine names that should land here
  run: Command;
}

export const ENTRIES: Entry[] = [
  { code: "TOD", title: "TODAY", hint: "the argument · evidence · countercase · live relay", keywords: "today thesis editorial front page live current", run: { type: "tab", tab: "TODAY" } },
  { code: "INV", title: "ARTICLES", hint: "daily analysis · investigations · charts · counter-cases", keywords: "investigative journalism stories analysis articles longform", run: { type: "href", url: "/articles/" } },
  { code: "DIS", title: "DISPATCHES", hint: "the daily letter", keywords: "letter desk note daily", run: { type: "tab", tab: "DISPATCHES" } },
  { code: "BRD", title: "BOARD", hint: "composite index · decomposition · ask", keywords: "dive index regime composite ask kink weather", run: { type: "tab", tab: "BOARD" } },
  { code: "MM", title: "MONEY MARKETS", hint: "clearing ladder · policy anchors · repo · liquidity", keywords: "money markets cash rates repo sofr effr iorb global benchmark policy corridor funding", run: { type: "tab", tab: "MONEY MARKETS" } },
  { code: "DATA", title: "CORPUS", hint: "structured evidence · rights · clocks · API · MCP", keywords: "corpus datasets structured data evidence provenance license rights bis api mcp", run: { type: "tab", tab: "CORPUS" } },
  { code: "WLD", title: "WORLD MARKETS ATLAS", hint: "money · forex · capital · China metadata · citation", keywords: "world markets forex capital China macro NBS metadata provenance sources citation evidence atlas", run: { type: "href", url: "/markets/" } },
  { code: "FCT", title: "FORECAST", hint: "swell curve · stack ensemble · analogs", keywords: "swell stack tide tables analogs ml lab odds", run: { type: "tab", tab: "FORECAST" } },
  { code: "PHY", title: "PHYSICS", hint: "bathymetry · merian · gyre · rogue wave", keywords: "bathymetry merian gyre rogue langevin", run: { type: "tab", tab: "PHYSICS" } },
  { code: "HLM", title: "HELM", hint: "the Book — paper positions, walk-forward P&L", keywords: "book positions pnl sharpe", run: { type: "tab", tab: "HELM" } },
  { code: "MKT", title: "MARKET", hint: "the Tell — market-priced stress", keywords: "tell vix spreads price", run: { type: "tab", tab: "MARKET" } },
  { code: "GLO", title: "GLOBAL", hint: "basin coupling · swap lines · stablecoins", keywords: "basins swap lines moorings stablecoin crypto btc", run: { type: "tab", tab: "GLOBAL" } },
  { code: "EST", title: "FX×MATERIALS", hint: "the Estuary · Passage gap · physical cash", keywords: "forex fx currencies commodities materials estuary passage transmission settlement copper gas wheat", run: { type: "tab", tab: "FX×MATERIALS" } },
  { code: "OIL", title: "OIL×FUNDING", hint: "carry · cargo credit · margin · India liquidity", keywords: "oil crude wti brent carry contango cargo margin petrodollar india rbi omc commercial paper", run: { type: "tab", tab: "OIL×FUNDING" } },
  { code: "CAL", title: "CALENDAR", hint: "forcing calendar — auctions, tax dates, turns", keywords: "auctions tax turn dates", run: { type: "tab", tab: "CALENDAR" } },
  { code: "POS", title: "POSITIONING", hint: "CFTC crowding · RV X-Ray", keywords: "cot crowding rvxray basis leverage", run: { type: "tab", tab: "POSITIONING" } },
  { code: "UTW", title: "UNDERTOW", hint: "the markets layer \u00b7 exit costs \u00b7 sealed record", keywords: "undertow exit liquidity market terminal fleet", run: { type: "href", url: "https://liquilens-undertow.com" } },
  { code: "RES", title: "RESONANCE", hint: "calendar-forcing amplification · undertow", keywords: "undertow slowing resonance", run: { type: "tab", tab: "RESONANCE" } },
  { code: "TM", title: "TIME MACHINE", hint: "construction-PIT reconstruction for a date — or `ASOF 2019-09-12`", keywords: "asof replay history rewind construction pit", run: { type: "tab", tab: "TIME MACHINE" } },
  { code: "PRF", title: "PROOF", hint: "historical diagnostic · evidence boundary · wrecks", keywords: "proof diagnostic evidence scoreboard wrecks episodes record", run: { type: "tab", tab: "PROOF" } },
  { code: "REF", title: "REFEREE", hint: "global liquidity's claims, tested on public data", keywords: "referee global liquidity g3 net liquidity claims lead lag cycle", run: { type: "tab", tab: "REFEREE" } },
  { code: "SYS", title: "SYSTEM", hint: "feed health · faults", keywords: "health feeds faults sources status", run: { type: "tab", tab: "SYSTEM" } },
  { code: "ACC", title: "ACCOUNT", hint: "email alerts", keywords: "alerts email login account", run: { type: "tab", tab: "ACCOUNT" } },
  { code: "GLANCE", title: "GLANCE", hint: "sounding: the verdict and the Tell, nothing else", keywords: "depth skim summary simple overview zoom out", run: { type: "depth", level: "glance" } },
  { code: "DESK", title: "DESK", hint: "sounding: the working board (default depth)", keywords: "depth normal default working", run: { type: "depth", level: "desk" } },
  { code: "DEEP", title: "DEEP", hint: "sounding: full fathom — every method note surfaces", keywords: "depth methods internals detail expert fathom", run: { type: "depth", level: "deep" } },
  { code: "GUIDE", title: "GUIDE", hint: "how to read this terminal", keywords: "help onboarding manual docs", run: { type: "href", url: "/guide" } },
  { code: "SUP", title: "SUPPORT", hint: "keep Seiche free", keywords: "donate support crypto", run: { type: "href", url: "/support" } },
];

// Engine-name codes: typing the engine gets you to the tab that owns it.
const ALIAS_CODES: Record<string, string> = {
  HOME: "TOD", NOW: "TOD", THESIS: "TOD",
  ARTICLE: "INV", ARTICLES: "INV", STORIES: "INV",
  DIVE: "BRD", KINK: "BRD", WEATHER: "BRD", ASK: "BRD",
  MONEY: "MM", MMKT: "MM", REPO: "MM", SOFR: "MM", EFFR: "MM", IORB: "MM",
  CORPUS: "DATA", DATASETS: "DATA", EVIDENCE: "DATA",
  SWELL: "FCT", TIDE: "FCT", STACK: "FCT", ANALOGS: "FCT",
  BATHY: "PHY", GYRE: "PHY", ROGUE: "PHY", MERIAN: "PHY",
  BOOK: "HLM", TELL: "MKT", BASINS: "GLO", MOORINGS: "GLO",
  FX: "EST", FOREX: "EST", CURRENCIES: "EST", COMMODITIES: "EST", MATERIALS: "EST", ESTUARY: "EST", PASSAGE: "EST",
  CRUDE: "OIL", CARRY: "OIL", CONTANGO: "OIL", PETRODOLLAR: "OIL", OMC: "OIL",
  COT: "POS", RVX: "POS", UNDERTOW: "RES",
  ASOF: "TM", REPLAY: "TM", WRECKS: "PRF", PROOF: "PRF",
  FEEDS: "SYS", HEALTH: "SYS", ALERTS: "ACC", HELP: "GUIDE",
  SKIM: "GLANCE", FATHOM: "DEEP", SOUNDING: "DESK",
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
