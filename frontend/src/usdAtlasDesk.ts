/**
 * The USD funding desk, rebuilt from the global atlas's US pack.
 *
 * The overview engine that used to fill this desk is withheld with the legacy
 * funding board. A row shows a value only when the atlas marks it AVAILABLE,
 * allows redistribution and dates it; the server's same-date SOFR minus IORB
 * spread joins it only when both legs qualify. Every other declared instrument
 * stays listed as a gap, without a value.
 */
import { qualifiedPolicySpread } from "./usdFunding";

type Row = Record<string, unknown>;

export interface UsdDeskSection {
  id: string;
  label: string;
  plain: string;
  metrics: Row[];
}

export interface UsdDeskSource {
  source: string;
  host: string | null;
  rows: number;
  latest: string | null;
}

export interface UsdChartPane {
  key: string;
  unit: string;
  labels: string[];
  rows: (string | number | null)[][];
  sources: string[];
  asof: string | null;
}

export interface UsdAtlasDesk {
  sections: UsdDeskSection[];
  /** Declared instruments without a public dated value. Never carries a value. */
  gaps: Array<{ id: string; label: string; role: string; cadence: string; source: string; state: string }>;
  publicCount: number;
  declaredCount: number;
  latestObservation: string | null;
  sources: UsdDeskSource[];
}

const record = (value: unknown): Row | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : null;
const rows = (value: unknown): Row[] => Array.isArray(value)
  ? value.map(record).filter((row): row is Row => row !== null) : [];
const text = (value: unknown): string => typeof value === "string" ? value : "";
const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

function observationDate(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? value : null;
}

/** AVAILABLE, redistribution allowed, a finite value and a valid observation date. */
export function publicUsdObservation(metric: unknown): boolean {
  const row = record(metric);
  return Boolean(row && row.availability === "AVAILABLE" && row.redistribution_status === "allowed"
    && finite(row.value) !== null && observationDate(row.asof));
}

const SPREAD_ID = "US.NYFED.SOFR_MEDIAN_MINUS_US.FED.IORB_DIRECT";
const SECTIONS: Array<{ id: string; label: string; plain: string; test: (id: string) => boolean }> = [
  {
    id: "policy_corridor", label: "Policy corridor",
    plain: "Interest on reserve balances and the server's SOFR minus IORB spread, computed on exact common dates rather than from mismatched latest values.",
    test: (id) => id === "US.FED.IORB_DIRECT" || id === SPREAD_ID,
  },
  {
    id: "secured_repo", label: "Secured repo",
    plain: "SOFR, TGCR and BGCR: the volume-weighted median, published percentiles and volume of overnight Treasury repo.",
    test: (id) => /^US\.NYFED\.(SOFR|TGCR|BGCR)_(MEDIAN|P01|P25|P75|P99|VOLUME)$/.test(id),
  },
  {
    id: "unsecured_funding", label: "Unsecured funding",
    plain: "EFFR and OBFR: the volume-weighted median, published percentiles and volume of overnight unsecured borrowing. OBFR adds Eurodollar transactions to federal funds.",
    test: (id) => /^US\.NYFED\.(EFFR|OBFR)_(MEDIAN|P01|P25|P75|P99|VOLUME)$/.test(id),
  },
  {
    id: "sofr_averages", label: "SOFR averages and index",
    plain: "Compounded SOFR averages over rolling 30, 90 and 180 calendar day periods, and the SOFR Index.",
    test: (id) => /^US\.NYFED\.SOFR_(AVERAGE_\d+D|INDEX)$/.test(id),
  },
  {
    id: "cash_facilities", label: "Treasury cash and facilities",
    plain: "The Treasury General Account balance and Standing Repo Facility take-up, shown in billions of dollars.",
    test: (id) => id === "US.TREASURY.GOVERNMENT_CASH" || id === "US.NYFED.SRF_TAKEUP",
  },
];

function gapState(metric: Row): string {
  const availability = text(metric.availability).toUpperCase();
  const redistribution = text(metric.redistribution_status).toLowerCase().replaceAll("_", " ");
  if (availability === "RESTRICTED") return "withheld" + (redistribution ? ": " + redistribution : "");
  if (availability === "DERIVED_CONTEXT") return "raw value withheld: derived context only";
  if (availability === "AVAILABLE" && text(metric.redistribution_status) !== "allowed") {
    return "withheld" + (redistribution ? ": " + redistribution : ": redistribution not stated");
  }
  if (availability === "AVAILABLE") return "no dated public value";
  return availability ? availability.toLowerCase().replaceAll("_", " ") : "unavailable";
}

function host(value: unknown): string | null {
  try {
    const url = new URL(text(value));
    return url.protocol === "https:" || url.protocol === "http:" ? url.hostname : null;
  } catch {
    return null;
  }
}

export function usdAtlasDesk(market: unknown): UsdAtlasDesk | null {
  const current = record(market);
  if (!current || current.market_id !== "US-USD") return null;
  const metrics = rows(current.metrics);
  const visible = metrics.filter(publicUsdObservation);
  const spread = qualifiedPolicySpread(current);
  const shown = spread && finite(spread.value) !== null && observationDate(spread.asof) ? [...visible, spread] : visible;
  if (shown.length === 0) return null;
  const used = new Set<Row>();
  const sections: UsdDeskSection[] = [];
  for (const rule of SECTIONS) {
    const members = shown.filter((metric) => !used.has(metric) && rule.test(text(metric.id)));
    members.forEach((metric) => used.add(metric));
    if (members.length) sections.push({ id: rule.id, label: rule.label, plain: rule.plain, metrics: members.map(usdDisplayMetric) });
  }
  const others = shown.filter((metric) => !used.has(metric));
  if (others.length) {
    sections.push({
      id: "other_public", label: "Other public observations",
      plain: "Further US rows the atlas serves with redistribution allowed.",
      metrics: others.map(usdDisplayMetric),
    });
  }
  const bySource = new Map<string, UsdDeskSource>();
  for (const metric of shown) {
    const source = text(metric.source) || "source not supplied";
    const entry = bySource.get(source) ?? { source, host: host(metric.source_url), rows: 0, latest: null };
    const asof = observationDate(metric.asof);
    entry.rows += 1;
    if (asof && (!entry.latest || asof > entry.latest)) entry.latest = asof;
    bySource.set(source, entry);
  }
  const dates = shown.map((metric) => observationDate(metric.asof)).filter((date): date is string => date !== null).sort();
  return {
    sections,
    gaps: metrics.filter((metric) => !publicUsdObservation(metric)).map((metric) => ({
      id: text(metric.id), label: text(metric.label) || text(metric.id),
      role: text(metric.semantic_role).replaceAll("_", " ").toLowerCase(),
      cadence: text(metric.cadence), source: text(metric.source), state: gapState(metric),
    })),
    publicCount: visible.length,
    declaredCount: metrics.length,
    latestObservation: dates[dates.length - 1] ?? null,
    sources: [...bySource.values()],
  };
}

const MILLIONS = "local_currency_millions";
const CHANGE_FIELDS = [
  "change_1_observation", "change_5_observations", "change_20_observations", "change_1d", "change_5d",
  "change_20d", "change_1w", "change_4w", "change_13w", "change_1m", "change_3m", "change_12m",
];
const toBillions = (value: unknown): unknown => finite(value) === null ? value : (value as number) / 1000;

/** Dollar stocks arrive in millions; the desk shows billions. Ranks and z-scores are scale-free. */
export function usdDisplayMetric(metric: Row): Row {
  if (metric.unit !== MILLIONS) return metric;
  const scaled: Row = { ...metric, value: toBillions(metric.value), unit: "$B" };
  if (metric.change_unit === MILLIONS) {
    CHANGE_FIELDS.forEach((field) => { if (field in metric) scaled[field] = toBillions(metric[field]); });
    scaled.change_unit = "$B";
  }
  const volUnit = text(metric.change_vol_unit);
  if (volUnit.startsWith(MILLIONS)) {
    scaled.change_vol_20_annualized = toBillions(metric.change_vol_20_annualized);
    scaled.change_vol_unit = "$B" + volUnit.slice(MILLIONS.length);
  }
  if (Array.isArray(metric.history)) {
    scaled.history = metric.history.map((point) => Array.isArray(point) ? [point[0], toBillions(point[1]), ...point.slice(2)] : point);
  }
  return scaled;
}

const CHARTED_ROLES = /^(RATE_MEDIAN|POLICY_TARGET|POLICY_RELATIVE_SPREAD|GOVERNMENT_CASH_BALANCE|CENTRAL_BANK_FACILITY_TAKEUP|COMPOUNDED_OVERNIGHT_AVERAGE_\d+D)$/;

function points(metric: Row): Array<[string, number]> {
  const out: Array<[string, number]> = [];
  for (const point of Array.isArray(metric.history) ? metric.history : []) {
    if (!Array.isArray(point)) continue;
    const date = observationDate(point[0]);
    const value = finite(point[1]);
    if (date && value !== null) out.push([date, value]);
  }
  return out.sort(([left], [right]) => left.localeCompare(right));
}

/**
 * History panes for one section: rates and spreads share a pane per unit, each
 * dollar stock gets its own pane, and percentiles, volumes and the index stay in
 * the table. Rows use the outer date grid with null gaps; nothing is interpolated.
 */
export function usdChartPanes(metrics: Row[]): UsdChartPane[] {
  const groups = new Map<string, Row[]>();
  for (const metric of metrics) {
    if (!CHARTED_ROLES.test(text(metric.semantic_role)) || points(metric).length < 2) continue;
    const unit = text(metric.unit);
    const key = unit === "$B" ? unit + ":" + text(metric.id) : unit;
    groups.set(key, [...(groups.get(key) ?? []), metric]);
  }
  return [...groups.entries()].map(([key, members]) => {
    const byDate = new Map<string, Map<number, number>>();
    members.forEach((metric, index) => {
      for (const [date, value] of points(metric)) {
        const row = byDate.get(date) ?? new Map<number, number>();
        row.set(index, value);
        byDate.set(date, row);
      }
    });
    const dates = members.map((metric) => observationDate(metric.asof)).filter((date): date is string => date !== null).sort();
    return {
      key,
      unit: text(members[0].unit),
      labels: members.map((metric) => text(metric.label) || text(metric.id)),
      rows: [...byDate.entries()].sort(([left], [right]) => left.localeCompare(right))
        .map(([date, values]) => [date, ...members.map((_, index) => values.get(index) ?? null)]),
      sources: [...new Set(members.map((metric) => text(metric.source)).filter(Boolean))],
      asof: dates[dates.length - 1] ?? null,
    };
  });
}
