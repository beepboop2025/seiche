/**
 * ECB euro reference rates, crossed to the US dollar by Seiche's market workbench.
 *
 * The workbench serves these with redistribution allowed. They are reference
 * rates, not executable quotes, intraday data or the PBoC central parity.
 * Every window counts ECB observations, never calendar days.
 */
type Row = Record<string, unknown>;

/** One request supplies every USD cross plus the full USD/CNY history. */
export const ECB_USD_CNY_QUERY = "base=USD&quote=CNY&days=365&provider=ecb";

export interface EcbCross {
  pair: string;
  quote: string;
  /** Local currency units per one US dollar: up means a weaker local currency. */
  unit: string;
  value: number;
  asof: string;
  /** The workbench's reference-data age label: fresh, aging, stale or dead. */
  status: string;
  observations: number;
  /** Percent change over 1, 5, 20 and 60 observation intervals. */
  change1: number | null;
  change5: number | null;
  change20: number | null;
  change60: number | null;
  /** Annualized volatility of the last 20 log returns, in percent. */
  vol20: number | null;
  publisher: string;
}

export interface EcbCnyLeg extends EcbCross {
  /** Ascending [date, CNY per USD] observations ending on `asof`. */
  history: Array<[string, number]>;
  /** First date of the 60-interval change, set only when the history reproduces it. */
  change60From: string | null;
  /** First date of the 20-interval window, set only when the history reproduces it. */
  window20From: string | null;
}

export interface EcbUsdReferences {
  generatedAt: string | null;
  crosses: EcbCross[];
  cny: EcbCnyLeg | null;
}

const record = (value: unknown): Row | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : null;
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const text = (value: unknown): string => typeof value === "string" ? value : "";
const finiteNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export function observationDate(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? value : null;
}

function readCross(value: unknown): EcbCross | null {
  const row = record(value);
  if (!row) return null;
  const quote = text(row.quote_currency);
  if (!/^[A-Z]{3}$/.test(quote) || quote === "USD" || row.base_currency !== "USD"
    || row.pair !== `USD/${quote}` || row.unit !== `${quote} per USD`
    || (row.provider !== undefined && row.provider !== "ecb")) return null;
  const level = finiteNumber(row.value);
  const asof = observationDate(row.as_of);
  const observations = finiteNumber(row.observation_count);
  const sources = list(row.sources).map(record);
  // A cross has two legs; each one must carry its own redistribution permission.
  if (level === null || level <= 0 || !asof || observations === null
    || !Number.isInteger(observations) || observations < 1 || sources.length === 0
    || sources.some((source) => source?.redistribution_status !== "allowed")) return null;
  const publishers = [...new Set(sources.map((source) => text(source?.publisher)).filter(Boolean))];
  return {
    pair: `USD/${quote}`, quote, unit: `${quote} per USD`, value: level, asof,
    status: text(row.status).toLowerCase() || "unknown", observations,
    change1: finiteNumber(row.change_1obs_pct), change5: finiteNumber(row.change_5obs_pct),
    change20: finiteNumber(row.change_20obs_pct), change60: finiteNumber(row.change_60obs_pct),
    vol20: finiteNumber(row.realized_vol_20obs_pct),
    // The response was already checked to come from the ECB provider.
    publisher: publishers.join(", ") || "European Central Bank",
  };
}

function readHistory(value: unknown): Array<[string, number]> | null {
  const points: Array<[string, number]> = [];
  for (const item of list(value)) {
    const point = record(item);
    const date = observationDate(point?.date);
    const level = finiteNumber(point?.value);
    if (!date || level === null || level <= 0) return null;
    points.push([date, level]);
  }
  points.sort(([left], [right]) => left.localeCompare(right));
  for (let index = 1; index < points.length; index += 1) {
    if (points[index][0] === points[index - 1][0]) return null;
  }
  return points;
}

/** The start date of an n-interval change, only when the served history reproduces the served change. */
function windowStart(points: Array<[string, number]>, intervals: number, served: number | null): string | null {
  if (served === null || points.length <= intervals) return null;
  const start = points[points.length - 1 - intervals];
  const latest = points[points.length - 1][1];
  return Math.abs((latest / start[1] - 1) * 100 - served) <= 1e-6 ? start[0] : null;
}

export function readEcbUsdReferences(payload: unknown): EcbUsdReferences | null {
  const root = record(payload);
  const selection = record(root?.selection);
  const forex = record(root?.forex);
  if (root?.schema !== "seiche.market-workbench.v1" || selection?.provider !== "ecb"
    || selection.base !== "USD" || !forex || forex.reference_only !== true
    || forex.base_currency !== "USD" || (forex.provider !== undefined && forex.provider !== "ecb")) return null;
  const parsed = list(forex.rows).map(readCross).filter((row): row is EcbCross => row !== null);
  // A repeated pair is ambiguous, so neither copy is shown.
  const counts = new Map<string, number>();
  parsed.forEach((row) => counts.set(row.quote, (counts.get(row.quote) ?? 0) + 1));
  const crosses = parsed.filter((row) => counts.get(row.quote) === 1);
  const cnyRow = crosses.find((row) => row.quote === "CNY") ?? null;
  let cny: EcbCnyLeg | null = null;
  if (cnyRow) {
    const served = selection.quote === "CNY" && forex.quote_currency === "CNY" ? readHistory(forex.history) : null;
    const last = served?.[served.length - 1];
    // Chart and window dates use the history only when it ends on the row's own observation.
    const history = served && last && last[0] === cnyRow.asof
      && Math.abs(last[1] - cnyRow.value) <= 1e-9 * cnyRow.value ? served : [];
    cny = {
      ...cnyRow, history,
      change60From: windowStart(history, 60, cnyRow.change60),
      window20From: windowStart(history, 20, cnyRow.change20),
    };
  }
  return { generatedAt: text(root.generated_at) || null, crosses, cny };
}

export function signedPercent(value: number): string {
  return (value > 0 ? "+" : "") + value.toFixed(2) + "%";
}

/** Plain-language direction of a local-currency-per-USD move. */
export function currencyDirection(currency: string, change: number): string {
  if (change > 0) return `${currency} weaker against USD`;
  if (change < 0) return `${currency} stronger against USD`;
  return `${currency} unchanged against USD`;
}

interface MarketInputs<M> {
  market_id: string;
  currency: string;
  display_name: string;
  benchmark?: M | null;
  policy_anchor?: M | null;
}

export interface TransmissionInput<M> {
  marketId: string;
  currency: string;
  name: string;
  /** The public traded benchmark, else the public policy anchor. */
  localRate: M | null;
  cross: EcbCross | null;
}

/** A value may be shown only when the atlas marks it available, allows redistribution and dates it. */
export function publicDatedMetric(metric: unknown): boolean {
  const row = record(metric);
  return Boolean(row && row.availability === "AVAILABLE" && row.redistribution_status === "allowed"
    && finiteNumber(row.value) !== null && observationDate(row.asof));
}

/**
 * Rights-cleared inputs for the transmission check: each market's own local rate
 * and its ECB cross. They are listed side by side and never blended into a score.
 */
export function transmissionInputs<M>(
  markets: ReadonlyArray<MarketInputs<M>>,
  crosses: ReadonlyArray<EcbCross>,
  excluded: ReadonlyArray<string> = ["US-USD", "CN-CNY"],
): Array<TransmissionInput<M>> {
  const byQuote = new Map(crosses.map((row) => [row.quote, row]));
  return markets
    .filter((market) => !excluded.includes(market.market_id))
    .map((market) => ({
      marketId: market.market_id,
      currency: market.currency,
      name: market.display_name,
      localRate: [market.benchmark, market.policy_anchor].find((metric) => publicDatedMetric(metric)) ?? null,
      cross: byQuote.get(market.currency) ?? null,
    }))
    .filter((row) => row.localRate !== null || row.cross !== null);
}
