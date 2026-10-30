import {record} from "./core";
import {seriesModel, type EconomicSeries} from "./seriesModel";

const chartColumns = [
  ["SOFR", "policy", "sofr_pct", "%", 1, "daily"],
  ["EFFR", "policy", "effr_pct", "%", 1, "daily"],
  ["IORB", "policy", "iorb_pct", "%", 1, "daily"],
  ["TGCR", "repo_rates", "tgcr_pct", "%", 1, "daily"],
  ["BGCR", "repo_rates", "bgcr_pct", "%", 1, "daily"],
  ["WRESBAL", "liquidity", "reserves_b", "$M", 1000, "weekly average"],
  ["TGA_LONG", "liquidity", "tga_b", "$M", 1000, "daily opening balance"],
  ["RRPONTSYD", "liquidity", "on_rrp_b", "$B", 1, "daily"],
  ["DGS3M", "bills", "treasury_3m_pct", "%", 1, "daily"],
  ["CP_FIN_3M_MINUS_DGS3M", "unsecured", "financial_cp_minus_treasury_bp", "bp", 1, "daily"],
  ["CP_NONFIN_3M_MINUS_DGS3M", "unsecured", "nonfinancial_cp_minus_treasury_bp", "bp", 1, "daily"],
  ["MMF_REPO_TOT", "mmf", "total_repo_b", "$B", 1, "monthly"],
  ["MMF_REPO_FED", "mmf", "fed_repo_b", "$B", 1, "monthly"],
  ["MMF_REPO_FICC", "mmf", "ficc_repo_b", "$B", 1, "monthly"],
] as const;

function model(key: string, unit: string, points: unknown[], source: string, remote: string, state: string, cadence: string, sourceUrl: string): EconomicSeries | null {
  const last = points.at(-1);
  if (!Array.isArray(last)) return null;
  try {
    return {...seriesModel({provenance:{mnemonic:key, unit, asof:last[0], staleness:state, source, remote_id:remote}, points},
      {available:true, mnemonic:key, unit, json:sourceUrl, label:key, cadence, native_lag:"Native observation dates"}), sourceUrl};
  } catch {return null;}
}
/** Read only named observed chart columns; gaps and invalid histories stay absent. */
export function publishedFundingModels(snapshot: unknown): Map<string, EconomicSeries> {
  const result = new Map<string, EconomicSeries>();
  if (!record(snapshot) || !record(snapshot.engines)) return result;
  const desk = snapshot.engines.money_market;
  if (!record(desk) || desk.schema !== "seiche.money-market-desk.v1" || !record(desk.charts)) return result;
  for (const [key, name, column, unit, scale, cadence] of chartColumns) {
    const chart = desk.charts[name];
    if (!record(chart) || !Array.isArray(chart.columns) || !Array.isArray(chart.rows) || chart.rows.length > 2000) continue;
    const columns = chart.columns;
    const index = columns.indexOf(column);
    if (columns[0] !== "date" || index < 1 || columns.indexOf(column, index + 1) !== -1) continue;
    if (chart.rows.some(row => !Array.isArray(row) || row.length !== columns.length)) continue;
    const points = chart.rows.filter(row => row[index] !== null)
      .map(row => [row[0], typeof row[index] === "number" ? row[index] * scale : row[index]]);
    const item = model(key, unit, points, "published_board", "engines.money_market.charts." + name + "." + column,
      "published observation", cadence, "/data/overview.json");
    if (item) result.set(key, item);
  }

  if (record(snapshot.headline)) {
    const headlineKeys = {SOFR:"sofr_pct", EFFR:"effr_pct", IORB:"iorb_pct", WRESBAL:"reserves_b", TGA_LONG:"tga_b", RRPONTSYD:"rrp_b"};
    for (const [key, field] of Object.entries(headlineKeys)) {
      const prior = result.get(key), latest = snapshot.headline[field];
      if (!prior || !record(latest) || typeof latest.value !== "number" || !Number.isFinite(latest.value)
          || typeof latest.asof !== "string" || latest.asof <= prior.asOf) continue;
      const points = [...prior.points.map(point => [point.date, point.value]),
        [latest.asof, latest.value * (prior.unit === "$M" ? 1000 : 1)]];
      const updated = model(key, prior.unit, points, "published_board",
        prior.remote + " + headline." + field, "published observation", prior.cadence, "/data/overview.json");
      if (updated) result.set(key, updated);
    }
  }

  const ledger = snapshot.engines.ledger;
  if (record(ledger) && ledger.ok === true && Array.isArray(ledger.series) && ledger.series.length <= 2000) {
    // ledger.py's emitted series: date, assets_b, currency_b, reserves_b, ...
    const item = model("WALCL", "$M", ledger.series.map(row => Array.isArray(row) && row.length === 8
      ? [row[0], typeof row[1] === "number" ? row[1] * 1000 : row[1]] : []),
      "published_board", "engines.ledger.series.assets_b", "published observation", "weekly", "/data/overview.json");
    if (item) result.set("WALCL", item);
  }
  return result;
}
export function atlasFundingModels(atlas: unknown): Map<string, EconomicSeries> {
  const result = new Map<string, EconomicSeries>();
  if (!record(atlas) || atlas.schema !== "seiche.global-money-markets.v1" || !Array.isArray(atlas.markets)) return result;
  const market = atlas.markets.find(row => record(row) && row.market_id === "US-USD");
  if (!record(market) || !Array.isArray(market.metrics)) return result;
  const ids = {SOFR:"US.NYFED.SOFR_MEDIAN", EFFR:"US.NYFED.EFFR_MEDIAN", IORB:"US.FED.IORB_DIRECT", TGCR:"US.NYFED.TGCR_MEDIAN", BGCR:"US.NYFED.BGCR_MEDIAN"};
  for (const [key, id] of Object.entries(ids)) {
    const row = market.metrics.find(item => record(item) && item.id === id);
    if (!record(row) || row.availability !== "AVAILABLE" || row.redistribution_status !== "allowed" || row.unit !== "%" || !Array.isArray(row.history)) continue;
    const item = model(key, "%", row.history, String(row.source), id, String(row.status), String(row.cadence), "https://api.seiche.info/api/v2/money-markets");
    if (item && item.asOf === row.asof) result.set(key, item);
  }
  return result;
}
/** A failed, empty or older refresh cannot erase a dated history. Sources are not spliced. */
export function mergeFundingModels(previous: Map<string, EconomicSeries>, incoming: Map<string, EconomicSeries>): Map<string, EconomicSeries> {
  const merged = new Map(previous);
  for (const [key, item] of incoming) {
    const before = merged.get(key);
    if (!before || (before.unit === item.unit && item.asOf >= before.asOf)) merged.set(key, item);
  }
  return merged;
}
