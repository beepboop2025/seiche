import {dateOnly, record} from "./core";
import {qualifiedPolicySpread} from "../usdFunding";

/**
 * The Funding Observatory's four headline cards. Each reads one direct row from the
 * money-market atlas's US pack, shown only when the atlas marks it AVAILABLE with
 * redistribution allowed and a valid date. FRED copies are never read here, and a
 * card without a value says why instead of looking empty.
 */
export type AtlasRead = {status: "loading"} | {status: "failed"} | {status: "ready"; market: unknown};
export type CardState = "live" | "loading" | "withheld" | "unavailable";
export type ObservatoryCard = {label: string; value: string; note: string; state: CardState};

export const RESERVES_DIRECT_ID = "US.FED.RESERVE_BALANCES_DIRECT";
export const ON_RRP_DIRECT_ID = "US.NYFED.ON_RRP_TAKEUP";
const SOFR_ID = "US.NYFED.SOFR_MEDIAN";

type Row = Record<string, unknown>;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const text = (value: unknown): string => typeof value === "string" ? value : "";
const format = (value: number, digits: number) => new Intl.NumberFormat("en-US", {maximumFractionDigits: digits}).format(value);
const FAILED_NOTE = "The money-market atlas could not be read. Retry to check; no earlier value is carried forward.";

/** The US pack of a money-market atlas response, or null when the contract does not match. */
export function atlasUsMarket(atlas: unknown): Row | null {
  if (!record(atlas) || atlas.schema !== "seiche.global-money-markets.v1" || !Array.isArray(atlas.markets)) return null;
  const market = atlas.markets.find(row => record(row) && row.market_id === "US-USD");
  return record(market) && Array.isArray(market.metrics) ? market : null;
}

function metric(market: unknown, id: string): Row | null {
  if (!record(market) || !Array.isArray(market.metrics)) return null;
  const row = market.metrics.find(item => record(item) && item.id === id);
  return record(row) ? row : null;
}

function publicRow(row: Row | null): row is Row & {value: number; asof: string} {
  return Boolean(row && row.availability === "AVAILABLE" && row.redistribution_status === "allowed"
    && finite(row.value) && dateOnly(row.asof));
}

function servedWithout(row: Row): string {
  const rights = text(row.redistribution_status);
  const state = rights && rights !== "allowed" ? rights.replaceAll("_", " ")
    : row.availability !== "AVAILABLE" ? text(row.availability).toLowerCase().replaceAll("_", " ") || "availability not stated"
      : !rights ? "redistribution not stated" : "no dated value";
  return `is served without a public value (${state})`;
}

function sofrCard(read: AtlasRead): ObservatoryCard {
  const label = "SOFR";
  if (read.status === "loading") return {label, value: "Loading", note: "Reading the New York Fed observation", state: "loading"};
  if (read.status === "failed") return {label, value: "Unavailable", note: FAILED_NOTE, state: "unavailable"};
  const row = metric(read.market, SOFR_ID);
  if (publicRow(row) && row.unit === "%") {
    return {label, value: `${format(row.value, 2)}%`, note: `${row.asof} · New York Fed`, state: "live"};
  }
  if (row && !publicRow(row)) return {label, value: "Withheld", note: `The New York Fed SOFR row ${servedWithout(row)}.`, state: "withheld"};
  return {label, value: "Unavailable", note: "The atlas returned no SOFR observation in percent.", state: "unavailable"};
}

function spreadCard(read: AtlasRead): ObservatoryCard {
  const label = "SOFR − IORB";
  if (read.status === "loading") return {label, value: "Loading", note: "Reading the same-date spread", state: "loading"};
  if (read.status === "failed") return {label, value: "Unavailable", note: FAILED_NOTE, state: "unavailable"};
  // The server's spread, only when both legs are public on its exact date; never mismatched latest values.
  const spread = qualifiedPolicySpread(read.market);
  if (spread && finite(spread.value) && dateOnly(spread.asof) && spread.unit === "bp") {
    return {label, value: `${spread.value > 0 ? "+" : ""}${format(spread.value, 2)} bp`,
      note: `${spread.asof} · same date, New York Fed and Federal Reserve Board`, state: "live"};
  }
  return {label, value: "Unavailable", note: "No same-date spread: SOFR and IORB are not both public on one date.", state: "unavailable"};
}

type DirectStock = {label: string; id: string; publisher: string; attribution: string; coming: string; loading: string};
const RESERVES: DirectStock = {
  label: "Reserve balances", id: RESERVES_DIRECT_ID, publisher: "Federal Reserve H.4.1",
  attribution: "Source: Board of Governors of the Federal Reserve System, H.4.1",
  coming: "The FRED copy is not cleared for republication; the Federal Reserve's own H.4.1 series arrives in the next release.",
  loading: "Checking the Federal Reserve's own H.4.1 series",
};
const ON_RRP: DirectStock = {
  label: "ON RRP", id: ON_RRP_DIRECT_ID, publisher: "New York Fed reverse repo",
  attribution: "Source: Federal Reserve Bank of New York, reverse repo operation results",
  coming: "The FRED copy is not cleared for republication; the New York Fed's own reverse repo series arrives in the next release.",
  loading: "Checking the New York Fed's own reverse repo series",
};

/** A direct dollar stock in USD millions, shown in billions; zero is an observation. */
function directStockCard(read: AtlasRead, stock: DirectStock): ObservatoryCard {
  const label = stock.label;
  if (read.status === "loading") return {label, value: "Loading", note: stock.loading, state: "loading"};
  if (read.status === "failed") return {label, value: "Unavailable", note: FAILED_NOTE, state: "unavailable"};
  const row = metric(read.market, stock.id);
  if (!row) return {label, value: "Withheld", note: stock.coming, state: "withheld"};
  if (publicRow(row) && row.unit === "local_currency_millions") {
    const billions = row.value / 1000;
    return {label, value: `$${format(billions, Math.abs(billions) < 1 ? 3 : 1)}bn`,
      note: `${row.asof} · ${text(row.attribution) || stock.attribution}`, state: "live"};
  }
  if (publicRow(row)) {
    return {label, value: "Unavailable", note: `The ${stock.publisher} series arrived in an unexpected unit (${text(row.unit) || "none"}), so no value is shown.`, state: "unavailable"};
  }
  return {label, value: "Withheld", note: `The ${stock.publisher} series ${servedWithout(row)}. The FRED copy is not cleared for republication.`, state: "withheld"};
}

export function observatoryCards(read: AtlasRead): ObservatoryCard[] {
  return [sofrCard(read), spreadCard(read), directStockCard(read, RESERVES), directStockCard(read, ON_RRP)];
}
