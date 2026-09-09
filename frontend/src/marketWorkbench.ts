/** Display and export contracts for the public market workbench. */
export interface FxSource {
  mnemonic: string;
  source_id: string;
  source_url: string | null;
  raw_unit: string;
  fetched_at: string | null;
  raw: Record<string, unknown>;
}

export interface FxRow {
  pair: string;
  base_currency: string;
  quote_currency: string;
  value: number | null;
  as_of: string | null;
  status: string;
  evidence_status: string;
  unit: string;
  change_1obs_pct: number | null;
  change_5obs_pct: number | null;
  change_20obs_pct: number | null;
  change_60obs_pct: number | null;
  realized_vol_20obs_pct: number | null;
  observation_count: number;
  sources: FxSource[];
  reason: string | null;
}

export interface HistoryPoint { date: string; value: number }
export interface ChinaObservation {
  series_id: string;
  label: string;
  value: number | null;
  unit: string;
  period_end: string;
  period_start: string;
  released_at: string | null;
  collected_at: string | null;
  accepted_at: string | null;
  evidence_url: string | null;
  source_id: string;
  market_channels: string[];
  revision: unknown;
  raw: Record<string, unknown>;
}
export interface ChinaChannel {
  id: string; title: string; reading: string; series_ids: string[]; available_series: number;
}
export interface WorkbenchSelection {
  base: string; quote: string; days: number; provider?: "h10" | "ecb"; china_series?: string | null;
}
export interface MarketWorkbenchData {
  schema: "seiche.market-workbench.v1";
  generated_at: string;
  selection: WorkbenchSelection;
  forex: {
    base_currency: string;
    quote_currency: string;
    quote_convention: string;
    currencies: string[];
    rows: FxRow[];
    history: HistoryPoint[];
    coverage: { declared_pairs: number; available_pairs: number; missing_pairs: number; returned_observations: number };
    methodology: string[];
  };
  china: {
    status: string;
    reason: string | null;
    economic_context: Record<string, unknown> | null;
    series: ChinaObservation[];
    selected_series: string | null;
    history: ChinaObservation[];
    channels: ChinaChannel[];
    gaps: Array<{ id: string; label: string; status: string; reason: string }>;
    fx: FxRow | null;
  };
  raw: Record<string, unknown>;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const CURRENCY = /^[A-Z]{3}$/;
const DECIMAL = /^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

function object(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`Invalid ${name} response`);
  return value as Record<string, unknown>;
}
function string(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function list(value: unknown, name: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`Missing ${name} records`);
  return value;
}
export function finiteValue(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === "number" ? value
    : typeof value === "string" && DECIMAL.test(value) ? Number(value) : NaN;
  if (!Number.isFinite(parsed)) throw new Error("Invalid numeric observation");
  return parsed;
}
function count(value: unknown): number {
  const parsed = finiteValue(value);
  if (parsed === null || !Number.isInteger(parsed) || parsed < 0) throw new Error("Invalid observation count");
  return parsed;
}
function date(value: unknown): string {
  if (typeof value !== "string" || !ISO_DATE.test(value) || !Number.isFinite(Date.parse(value))
    || new Date(value).toISOString().slice(0, 10) !== value) throw new Error("Invalid observation date");
  return value;
}
export function safeEvidenceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
function currency(value: unknown): string {
  if (typeof value !== "string" || !CURRENCY.test(value)) throw new Error("Invalid currency code");
  return value;
}
function fxRow(value: unknown): FxRow {
  const row = object(value, "currency");
  const base = currency(row.base_currency), quote = currency(row.quote_currency);
  const numeric = finiteValue(row.value);
  if (row.pair !== `${base}/${quote}` || row.unit !== `${quote} per ${base}` || base === quote) throw new Error("Currency label and quote convention disagree");
  if (numeric !== null && (numeric <= 0 || !row.as_of)) throw new Error("Invalid dated reference rate");
  return {
    pair: string(row.pair), base_currency: base, quote_currency: quote,
    value: numeric, as_of: row.as_of ? date(row.as_of) : null,
    status: string(row.status, "unavailable"), evidence_status: string(row.evidence_status, "unavailable"),
    unit: string(row.unit), change_1obs_pct: finiteValue(row.change_1obs_pct),
    change_5obs_pct: finiteValue(row.change_5obs_pct), change_20obs_pct: finiteValue(row.change_20obs_pct),
    change_60obs_pct: finiteValue(row.change_60obs_pct), realized_vol_20obs_pct: finiteValue(row.realized_vol_20obs_pct),
    observation_count: count(row.observation_count), reason: string(row.reason) || null,
    sources: list(row.sources, "currency source").map((value) => {
      const source = object(value, "currency source");
      return { mnemonic: string(source.mnemonic), source_id: string(source.source_id), source_url: safeEvidenceUrl(source.source_url), raw_unit: string(source.raw_unit), fetched_at: string(source.fetched_at) || null, raw: source };
    }),
  };
}
function chinaObservation(value: unknown): ChinaObservation {
  const row = object(value, "China observation");
  if (!string(row.series_id)) throw new Error("China observation has no series identity");
  return {
    series_id: string(row.series_id), label: string(row.label) || string(row.series_id).replace(/^cn\.wdi\./, "").replaceAll("_", " "),
    value: finiteValue(row.value), unit: string(row.unit), period_end: date(row.period_end),
    period_start: date(row.period_start), released_at: string(row.released_at) || null,
    collected_at: string(row.collected_at) || null, accepted_at: string(row.accepted_at) || null,
    evidence_url: safeEvidenceUrl(row.evidence_url), source_id: string(row.source_id),
    market_channels: strings(row.market_channels), revision: row.revision, raw: row,
  };
}

export function normalizeWorkbench(value: unknown, expected: WorkbenchSelection): MarketWorkbenchData {
  const raw = object(value, "workbench");
  if (raw.schema !== "seiche.market-workbench.v1") throw new Error("Unrecognized workbench schema");
  const selection = object(raw.selection, "selection");
  if (selection.provider !== (expected.provider ?? "h10")) throw new Error("The response does not match the selected reference-rate provider");
  if (selection.base !== expected.base || selection.quote !== expected.quote || selection.days !== expected.days) {
    throw new Error("The response does not match the selected currencies and window");
  }
  const forex = object(raw.forex, "forex");
  if (forex.base_currency !== expected.base || forex.quote_currency !== expected.quote || forex.reference_only !== true) {
    throw new Error("The response does not establish the selected reference-rate convention");
  }
  const rows = list(forex.rows, "currency").map(fxRow);
  if (rows.some((row) => row.base_currency !== expected.base)
    || new Set(rows.map((row) => row.quote_currency)).size !== rows.length) throw new Error("Ambiguous currency comparison");
  const history = list(forex.history, "FX history").map((value) => {
    const point = object(value, "FX history");
    const numeric = finiteValue(point.value);
    if (numeric === null || numeric <= 0) throw new Error("Invalid FX history value");
    return { date: date(point.date), value: numeric };
  }).sort((a, b) => a.date.localeCompare(b.date));
  if (new Set(history.map((row) => row.date)).size !== history.length) throw new Error("Duplicate FX history date");
  const coverage = object(forex.coverage, "coverage");
  const china = object(raw.china, "China context");
  const series = list(china.series, "China series").map(chinaObservation);
  const chinaHistory = list(china.history, "China history").map(chinaObservation).sort((a, b) => a.period_end.localeCompare(b.period_end));
  const selected = string(china.selected_series) || null;
  if (chinaHistory.some((row) => row.series_id !== selected)
    || new Set(chinaHistory.map((row) => row.period_end)).size !== chinaHistory.length
    || new Set(chinaHistory.map((row) => row.unit)).size > 1) throw new Error("Ambiguous China history series, period or unit");
  if (expected.china_series && selected !== expected.china_series && chinaHistory.length) throw new Error("The response does not match the selected China series");
  const candidate = china.economic_context ? object(china.economic_context, "accepted China context") : null;
  const context = candidate && Object.keys(candidate).length ? candidate : null;
  if ((series.length || chinaHistory.length) && (!context || context.context_only !== true || context.scoring_eligible !== false)) {
    throw new Error("China observations lack their structural evidence boundary");
  }
  return {
    schema: "seiche.market-workbench.v1", generated_at: string(raw.generated_at),
    selection: { base: expected.base, quote: expected.quote, days: expected.days, provider: expected.provider ?? "h10", china_series: string(selection.china_series) || null },
    forex: {
      base_currency: expected.base, quote_currency: expected.quote, quote_convention: string(forex.quote_convention),
      currencies: list(forex.currencies, "currency register").map(currency), rows, history,
      coverage: { declared_pairs: count(coverage.declared_pairs), available_pairs: count(coverage.available_pairs), missing_pairs: count(coverage.missing_pairs), returned_observations: count(coverage.returned_observations) },
      methodology: strings(forex.methodology),
    },
    china: {
      status: string(china.status, "unavailable"), reason: string(china.reason) || null, economic_context: context,
      series, selected_series: selected, history: chinaHistory,
      channels: list(china.channels, "China channel").map((value) => {
        const channel = object(value, "China channel");
        return { id: string(channel.id), title: string(channel.title), reading: string(channel.reading), series_ids: strings(channel.series_ids), available_series: count(channel.available_series) };
      }),
      gaps: list(china.gaps, "China coverage gap").map((value) => {
        const gap = object(value, "China gap");
        return { id: string(gap.id), label: string(gap.label), status: string(gap.status), reason: string(gap.reason) };
      }),
      fx: china.fx ? fxRow(china.fx) : null,
    }, raw,
  };
}

export function filterFxRows(rows: readonly FxRow[], query: string): FxRow[] {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter((row) => tokens.every((token) => [row.pair, row.unit, row.quote_currency, row.status, ...row.sources.map((source) => source.source_id)].join(" ").toLowerCase().includes(token)));
}

/** X coordinates use elapsed time: a source holiday must not become an invented observation. */
export function seriesPlot(points: readonly HistoryPoint[], width = 800) {
  const valid = points.filter((point) => Number.isFinite(point.value) && ISO_DATE.test(point.date) && Number.isFinite(Date.parse(point.date)))
    .slice().sort((a, b) => a.date.localeCompare(b.date));
  if (!valid.length) return null;
  const values = valid.map((point) => point.value);
  const low = Math.min(...values), high = Math.max(...values);
  const padding = high === low ? Math.max(Math.abs(low) * .01, .01) : (high - low) * .1;
  const min = low - padding, max = high + padding;
  const start = Date.parse(valid[0].date), end = Date.parse(valid[valid.length - 1].date);
  const plotted = valid.map((point) => ({ ...point,
    x: start === end ? (width + 40) / 2 : 80 + (Date.parse(point.date) - start) / (end - start) * (width - 120),
    y: 22 + (max - point.value) / (max - min) * 200,
  }));
  return { min, max, points: plotted, path: plotted.map((point, i) => `${i ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ") };
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let text = typeof value === "object" ? JSON.stringify(value) : String(value);
  // Preserve negative numeric values while protecting spreadsheet text cells.
  if (typeof value !== "number" && /^[\s]*[=+@-]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
function csv(headers: string[], rows: unknown[][]): string {
  return [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
export function fxHistoryCsv(data: MarketWorkbenchData): string {
  const row = data.forex.rows.find((row) => row.quote_currency === data.selection.quote);
  return csv(["date", "value", "base_currency", "quote_currency", "provider", "unit", "evidence_status", "generated_at", "sources", "quote_convention"],
    data.forex.history.map((point) => [point.date, point.value, data.selection.base, data.selection.quote, data.selection.provider, row?.unit, row?.evidence_status, data.generated_at, row?.sources.map((source) => source.raw), data.forex.quote_convention]));
}
export function chinaHistoryCsv(data: MarketWorkbenchData): string {
  return csv(["series_id", "period_start", "period_end", "value", "unit", "released_at", "collected_at", "accepted_at", "source_id", "evidence_url", "revision", "metadata"],
    data.china.history.map((row) => [row.series_id, row.period_start, row.period_end, row.value, row.unit, row.released_at, row.collected_at, row.accepted_at, row.source_id, row.evidence_url, row.revision, row.raw]));
}
