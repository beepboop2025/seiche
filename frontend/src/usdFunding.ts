type Row = Record<string, unknown>;
export type FundingCard = {
  id: string; label: string; value: number | null; unit: string;
  asof: string | null; cadence: string; source: string;
  origin: "live" | "published" | "missing"; status: string;
};
const definitions = [
  ["policy.sofr", "SOFR", "%", "Daily", "US.NYFED.SOFR_MEDIAN"],
  ["policy.iorb", "IORB", "%", "Daily", "US.FED.IORB_DIRECT"],
  ["policy.sofr_minus_iorb", "SOFR − IORB", "bp", "Same-date daily spread", "US.NYFED.SOFR_MEDIAN_MINUS_US.FED.IORB_DIRECT"],
  ["liquidity.reserves", "Reserve balances", "$B", "Weekly average", "US.FED.RESERVE_BALANCES"],
  ["policy.effr", "Effective federal funds rate", "%", "Daily", "US.NYFED.EFFR_MEDIAN"],
  ["liquidity.tga", "Treasury General Account", "$B", "Daily opening balance", ""],
  ["liquidity.on_rrp", "ON RRP take-up", "$B", "Daily", ""],
  ["liquidity.srf", "Standing Repo Facility", "$B", "Daily", ""],
] as const;
const record = (value: unknown): Row | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : null;
const rows = (value: unknown): Row[] => Array.isArray(value)
  ? value.map(record).filter((row): row is Row => row !== null) : [];
const text = (value: unknown) => typeof value === "string" ? value : "";
function observationDate(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? value : null;
}
function eligible(row: Row | undefined, live: boolean, derived = false): boolean {
  if (!row || typeof row.value !== "number" || !Number.isFinite(row.value) || !observationDate(row.asof)) return false;
  if (live) return row.availability === "AVAILABLE" && (row.redistribution_status === "allowed" || derived);
  return String(row.status).toLowerCase() === "available";
}
export function usdMarket(atlas: unknown): Row | null {
  const root = record(atlas);
  if (root?.schema !== "seiche.global-money-markets.v1") return null;
  return rows(root.markets).find(row => row.market_id === "US-USD") ?? null;
}
/** Compare observation dates per series; never derive a spread from mismatched dates. */
export function usdFundingCards(snapshot: unknown, market: unknown): FundingCard[] {
  const engines = record(record(snapshot)?.engines);
  const desk = record(engines?.money_market);
  const published = rows(desk?.sections).flatMap(section => rows(section.metrics));
  const current = record(market);
  const live = rows(current?.metrics);
  const spread = record(current?.policy_relative_spread);
  const legIds = ["US.NYFED.SOFR_MEDIAN", "US.FED.IORB_DIRECT"];
  const lineage = rows(spread?.input_lineage);
  const qualifiedSpread = spread?.semantic_role === "POLICY_RELATIVE_SPREAD"
    && spread.formula_version === "mm.atlas.policy-spread.v1"
    && !spread.redistribution_status
    && lineage.length === 2
    && legIds.every(id => live.some(row => row.id === id && eligible(row, true))
      && lineage.some(row => row.instrument_id === id && text(row.event_time).slice(0, 10) === spread.asof));
  if (spread && qualifiedSpread) live.push(spread);
  return definitions.map(([id, label, unit, cadence, liveId]) => {
    const prior = published.find(row => row.id === id && row.unit === unit && eligible(row, false));
    const next = live.find(row => row.id === liveId && eligible(row, true, row === spread && qualifiedSpread)
      && (row.unit === unit || (unit === "$B" && row.unit === "local_currency_millions")));
    const useLive = Boolean(next && (!prior || String(next.asof) >= String(prior.asof)));
    const chosen = useLive ? next : prior;
    const divisor = useLive && next?.unit === "local_currency_millions" ? 1000 : 1;
    return {
      id, label, unit, cadence,
      value: chosen ? Number(chosen.value) / divisor : null,
      asof: observationDate(chosen?.asof),
      source: text(chosen?.source).replaceAll("_", " ") || "Source not supplied",
      origin: chosen ? useLive ? "live" : "published" : "missing",
      status: text(chosen?.freshness || chosen?.status),
    };
  });
}
export function fundingValue(card: FundingCard): string {
  if (card.value === null) return "Unavailable";
  const digits = card.unit === "bp" ? 2 : card.unit === "$B" && Math.abs(card.value) < 1 ? 3 : 2;
  const value = card.value.toLocaleString("en-US", {minimumFractionDigits: card.unit === "%" ? 2 : 0, maximumFractionDigits: digits});
  return card.unit === "$B" ? "$" + value + "B"
    : (card.unit === "bp" && card.value > 0 ? "+" : "") + value + (card.unit === "bp" ? " bp" : "%");
}

/** The reserve-demand model averages four weekly observations; it is not today's fixing. */
export function clarifyFundingEditorial(snapshot: unknown, editorial: unknown): Record<string, unknown> {
  const original = record(editorial) ?? {};
  const kink = record(record(record(snapshot)?.engines)?.kink);
  if (kink?.ok !== true || typeof kink.observed_spread_now_bp !== "number"
      || !Number.isFinite(kink.observed_spread_now_bp) || !observationDate(kink.asof)) return original;
  const mean = kink.observed_spread_now_bp;
  const average = (mean > 0 ? "+" : "") + mean.toFixed(1) + " bp";
  const daily = usdFundingCards(snapshot, null).find(card => card.id === "policy.sofr_minus_iorb");
  const explanation = "The reserve-demand model uses a four-week mean SOFR − IORB spread of " + average + " through " + kink.asof + "."
    + (daily?.value !== null && daily?.asof ? " The latest daily spread is " + fundingValue(daily) + " on " + daily.asof + "." : "");
  return {
    ...original,
    evidence: rows(original.evidence).map(row => row.engine === "kink"
      ? {...row, claim: explanation + (typeof kink.kink_reserves_b === "number" && Number.isFinite(kink.kink_reserves_b)
        ? " The fitted reserve-demand kink is $" + kink.kink_reserves_b.toLocaleString("en-US", {maximumFractionDigits:0}) + "B." : "")} : row),
    countercase: rows(original.countercase).map(row =>
      row.source === "NY Fed secured rates and Federal Reserve IORB" && row.asof === kink.asof
      && text(row.claim).startsWith("SOFR is ") ? {...row, claim: explanation} : row),
  };
}
