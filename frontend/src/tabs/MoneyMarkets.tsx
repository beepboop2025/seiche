import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../apiBase";
import { authHeaders } from "../auth";
import Chart from "../Chart";
import { P } from "../palette";
import { moneyMarketSharePath } from "../shareRoutes";
import "../styles-money-markets.css";

type UnknownRecord = Record<string, unknown>;
type FetchMode = "live" | "usd-fallback" | "unavailable";
type DeskView = "briefing" | "world" | "lab" | "china" | "notes";

interface MoneyMarketMetric {
  id: string;
  mnemonic?: string | null;
  label: string;
  semantic_role?: string | null;
  availability?: string | null;
  value?: number | null;
  unit?: string | null;
  asof?: string | null;
  published_at?: string | null;
  cadence?: string | null;
  expected_next_update?: string | null;
  source?: string | null;
  source_url?: string | null;
  source_tier?: string | null;
  redistribution_status?: string | null;
  revision_status?: string | null;
  confidence?: string | null;
  status?: string | null;
  change_1_observation?: number | null;
  change_5_observations?: number | null;
  change_20_observations?: number | null;
  change_unit?: string | null;
  change_1d?: number | null;
  change_5d?: number | null;
  change_20d?: number | null;
  change_1w?: number | null;
  change_4w?: number | null;
  change_13w?: number | null;
  change_1m?: number | null;
  change_3m?: number | null;
  change_12m?: number | null;
  robust_z_1y?: number | null;
  change_vol_20_annualized?: number | null;
  change_vol_unit?: string | null;
  robust_z?: number | null;
  own_history_z?: number | null;
  percentile_3y?: number | null;
  percentile?: number | null;
  own_history_percentile?: number | null;
  n_observations?: number | null;
  day_count?: string | null;
  compounding?: string | null;
  formula?: string | null;
  formula_version?: string | null;
  alignment?: string | null;
  explanation?: string | null;
  unavailable_reason?: string | null;
  freshness?: string | null;
  age_days_vs_evaluation_asof?: number | null;
  history?: unknown;
}

interface MarketCoverage {
  declared_instruments?: number;
  public_available?: number;
  derived_context?: number;
  restricted?: number;
  unavailable?: number;
  coverage_pct?: number | null;
}

interface MarketAdapter {
  adapter_id?: string;
  classification?: string;
  redistribution_status?: string;
  expected_cadence?: string;
  source_url?: string | null;
  last_run_status?: string | null;
  last_finished_at?: string | null;
  next_due?: string | null;
  fault?: unknown;
}

interface MoneyMarket {
  market_id: string;
  monetary_area_id?: string;
  region: string;
  display_name: string;
  jurisdiction_codes?: string[];
  currency: string;
  timezone?: string;
  settlement_calendar?: string;
  policy_regime?: string;
  support_status?: string;
  status?: string;
  plain_language?: string;
  quant_read?: string;
  countercase?: unknown;
  benchmark?: MoneyMarketMetric | null;
  derived_benchmark?: MoneyMarketMetric | null;
  policy_anchor?: MoneyMarketMetric | null;
  policy_relative_spread?: MoneyMarketMetric | null;
  metrics: MoneyMarketMetric[];
  coverage: MarketCoverage;
  adapters?: MarketAdapter[];
  faults?: MarketAdapter[];
  events?: Array<{ event_id?: string; label?: string }>;
  known_gaps?: string[];
}

interface ExpansionRow {
  market_id?: string;
  region?: string;
  currency?: string;
  market?: string;
  benchmark?: string;
  benchmark_kind?: string;
  authority?: string;
  source_url?: string;
  access?: string;
  access_note?: string;
  confidence?: string;
  status?: string;
  verified_on?: string;
}

interface MoneyMarketAtlas {
  ok?: boolean;
  schema?: string;
  generated_at?: string;
  status?: string;
  plain_language?: string;
  quant_read?: string;
  strongest_divergence?: unknown;
  countercase?: unknown;
  coverage?: {
    declared_markets?: number;
    live_benchmarks?: number;
    available_benchmarks?: number;
    stale_benchmarks?: number;
    derived_context_benchmarks?: number;
    policy_only_markets?: number;
    planned_markets?: number;
    expansion_markets?: number;
    global_discovery_universe?: number;
    expansion_regions?: number;
    source_verified_candidates?: number;
    access_review_candidates?: number;
    methodology_review_candidates?: number;
    research_queue_candidates?: number;
    compliance_blocked_candidates?: number;
  };
  markets: MoneyMarket[];
  expansion_ledger?: ExpansionRow[];
  expansion_scope?: Record<string, unknown>;
  methodology?: Record<string, unknown>;
  caveats?: string[];
  legal_notices?: unknown;
}

interface UsdSection {
  id: string;
  label: string;
  plain_language?: string;
  status?: string;
  available_metrics?: number;
  total_metrics?: number;
  metrics: MoneyMarketMetric[];
}

interface UsdSource {
  id?: string;
  label?: string;
  publisher?: string;
  series?: string;
  cadence?: string;
  available?: boolean;
  observations?: number;
  coverage_start?: string | null;
  asof?: string | null;
  age_days_vs_desk_asof?: number | null;
  age_days_vs_evaluation_asof?: number | null;
  freshness?: string;
  source_url?: string | null;
}

interface UsdChart {
  id?: string;
  label?: string;
  cadence?: string;
  columns?: string[];
  rows?: unknown;
  sampling?: string;
  no_forward_fill?: boolean;
}

interface UsdMoneyMarketEngine {
  ok?: boolean;
  schema?: string;
  asof?: string;
  context_only?: boolean;
  regime?: {
    state?: string;
    worst_stress_percentile?: number | null;
    raw_worst_stress_percentile?: number | null;
    bonferroni_adjusted_worst_stress_percentile?: number | null;
    worst_indicator?: unknown;
    familywise_adjustment?: {
      method?: string;
      eligible_hypotheses?: number;
      headline_uses?: string;
    };
    minimum_history?: string;
    rule?: string;
    status?: string;
  };
  plain_language?: string;
  quant_read?: string;
  strongest_signal?: unknown;
  countercase?: unknown;
  coverage?: {
    available_metrics?: number;
    total_metrics?: number;
    coverage_pct?: number | null;
    available_sources?: number;
    total_sources?: number;
  };
  freshness?: {
    desk_asof?: string;
    evaluation_asof?: string;
    basis?: string;
    status_counts?: Record<string, number>;
  };
  sections: UsdSection[];
  charts?: Record<string, UsdChart>;
  methodology?: Record<string, unknown>;
  formulas?: Array<{ id?: string; expression?: string; alignment?: string; unit?: string }>;
  caveats?: string[];
  source_metadata?: UsdSource[];
  sources?: UsdSource[];
  legal_notices?: unknown;
}

interface LegalNotice {
  title: string;
  text: string;
  url: string | null;
}

interface Props {
  snap: {
    generated_at?: string;
    engines?: {
      money_market?: unknown;
      harbors?: unknown;
    };
  };
}

interface HarborReading {
  harbor?: string;
  cadence?: string;
  stress?: number | null;
  stress_coverage?: number | null;
  regime?: string | null;
  rate?: {
    label?: string;
    last_pct?: number | null;
    asof?: string | null;
    chg_6m_bp?: number | null;
    chg_1y_bp?: number | null;
    n_obs?: number | null;
  } | null;
  rate2?: {
    label?: string;
    last_pct?: number | null;
    asof?: string | null;
  } | null;
  fx?: {
    label?: string;
    last?: number | null;
    asof?: string | null;
    chg_60d_pct?: number | null;
    vol10_ann_pct?: number | null;
  } | null;
  note?: string | null;
}

interface HarborsEngine {
  ok?: boolean;
  asof?: string;
  harbors?: HarborReading[];
  rate_rows?: unknown;
  rate_labels?: string[];
  fx_rows?: unknown;
  fx_labels?: string[];
  caveats?: string[];
  method?: string;
}

const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const safeString = (value: unknown): string =>
  typeof value === "string" ? value : "";

function parseAtlas(value: unknown): MoneyMarketAtlas | null {
  if (!isRecord(value) || !Array.isArray(value.markets)) return null;
  const markets = value.markets.filter((item): item is MoneyMarket => {
    if (!isRecord(item)) return false;
    return typeof item.market_id === "string"
      && typeof item.display_name === "string"
      && typeof item.currency === "string"
      && typeof item.region === "string"
      && Array.isArray(item.metrics);
  });
  if (markets.length === 0) return null;
  return { ...(value as unknown as MoneyMarketAtlas), markets };
}

function parseUsdEngine(value: unknown): UsdMoneyMarketEngine | null {
  if (!isRecord(value) || !Array.isArray(value.sections)) return null;
  const sections = value.sections.filter((item): item is UsdSection =>
    isRecord(item)
    && typeof item.id === "string"
    && typeof item.label === "string"
    && Array.isArray(item.metrics));
  if (sections.length === 0) return null;
  return { ...(value as unknown as UsdMoneyMarketEngine), sections };
}

function prose(value: unknown): string {
  if (typeof value === "string") return value;
  if (!isRecord(value)) return "";
  const fields = ["reading", "explanation", "interpretation", "limit", "why_selected", "use"];
  return fields.map((key) => safeString(value[key])).filter(Boolean).join(" ");
}

function urlOrNull(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function readLegalNotices(value: unknown): LegalNotice[] {
  const notices: LegalNotice[] = [];
  const visit = (item: unknown, fallbackTitle = "Source terms") => {
    if (typeof item === "string" && item.trim()) {
      notices.push({ title: fallbackTitle, text: item.trim(), url: null });
      return;
    }
    if (Array.isArray(item)) {
      item.forEach((child) => visit(child, fallbackTitle));
      return;
    }
    if (!isRecord(item)) return;
    if (Array.isArray(item.notices)) visit(item.notices, fallbackTitle);
    const title = safeString(item.title)
      || safeString(item.label)
      || safeString(item.source)
      || fallbackTitle;
    const text = safeString(item.text)
      || safeString(item.notice)
      || safeString(item.statement)
      || safeString(item.body);
    const url = urlOrNull(item.url)
      || urlOrNull(item.terms_url)
      || urlOrNull(item.terms);
    if (text) {
      notices.push({ title, text, url });
      return;
    }
    for (const [key, child] of Object.entries(item)) {
      if (key !== "notices" && key !== "url" && key !== "terms_url" && key !== "terms") {
        visit(child, key.replaceAll("_", " "));
      }
    }
  };
  visit(value);
  return notices;
}

const NY_FED_FALLBACK_NOTICE: LegalNotice = {
  title: "NY Fed terms / independence",
  text: "Federal Reserve Bank of New York data are used under its published Terms of Use. Seiche is independent and is not affiliated with, sponsored by, or endorsed by the Federal Reserve Bank of New York or the Federal Reserve System.",
  url: "https://www.newyorkfed.org/privacy/termsofuse",
};

const GENERAL_INDEPENDENCE_NOTICE: LegalNotice = {
  title: "Source attribution is not endorsement",
  text: "Authority names identify the origin of public evidence. Seiche is independent; no listed central bank, treasury, statistical office or benchmark administrator sponsors or endorses this product or Seiche’s derived analysis.",
  url: null,
};

function isPublicValue(metric: MoneyMarketMetric | null | undefined): boolean {
  if (!metric) return false;
  const availability = (metric.availability || "AVAILABLE").toUpperCase();
  const redistribution = (metric.redistribution_status || "allowed").toLowerCase();
  return availability === "AVAILABLE"
    && redistribution !== "prohibited"
    && redistribution !== "derived_only"
    && finite(metric.value) !== null;
}

function isDerivedContext(metric: MoneyMarketMetric | null | undefined): boolean {
  if (!metric) return false;
  return (metric.availability || "").toUpperCase() === "DERIVED_CONTEXT";
}

function hasCurrentClock(metric: MoneyMarketMetric | null | undefined): boolean {
  if (!metric || !isPublicValue(metric)) return false;
  const clock = (metric.freshness || metric.status || "").toLowerCase();
  return clock === "fresh" || clock === "aging" || clock === "available" || clock === "live";
}

function mayShowStatistics(metric: MoneyMarketMetric | null | undefined): boolean {
  if (!metric) return false;
  const availability = (metric.availability || "AVAILABLE").toUpperCase();
  const redistribution = (metric.redistribution_status || "allowed").toLowerCase();
  return (availability === "AVAILABLE" || availability === "DERIVED_CONTEXT")
    && redistribution !== "prohibited";
}

function hasCurrentStatistics(metric: MoneyMarketMetric | null | undefined): boolean {
  if (!mayShowStatistics(metric)) return false;
  const clock = (metric?.freshness || metric?.status || "").toLowerCase();
  return clock === "fresh" || clock === "aging" || clock === "available" || clock === "live";
}

function comparisonBenchmark(market: MoneyMarket): MoneyMarketMetric | null {
  return market.benchmark || market.derived_benchmark || null;
}

function ownPercentile(metric: MoneyMarketMetric | null | undefined): number | null {
  if (!metric) return null;
  return finite(metric.own_history_percentile)
    ?? finite(metric.percentile)
    ?? finite(metric.percentile_3y);
}

function ownZ(metric: MoneyMarketMetric | null | undefined): number | null {
  if (!metric) return null;
  return finite(metric.own_history_z)
    ?? finite(metric.robust_z)
    ?? finite(metric.robust_z_1y);
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

function fmt(value: number | null | undefined, unit?: string | null, signed = false): string {
  const number = finite(value);
  if (number === null) return "—";
  const abs = Math.abs(number);
  const digits = abs >= 1000 ? 0 : abs >= 100 ? 1 : abs >= 10 ? 2 : 3;
  const prefix = signed && number > 0 ? "+" : "";
  const suffix = unit ? " " + unit : "";
  return prefix + number.toLocaleString(undefined, { maximumFractionDigits: digits }) + suffix;
}

function formatCadence(value: string | null | undefined): string {
  const cadence = (value || "").trim();
  const known: Record<string, string> = {
    P1D: "daily",
    P1W: "weekly",
    P1M: "monthly",
    P3M: "quarterly",
  };
  return known[cadence.toUpperCase()] || cadence || "clock unavailable";
}

function shortDate(value: string | null | undefined): string {
  if (!value) return "not published";
  return value.slice(0, 16).replace("T", " ");
}

function statusTone(value: string | null | undefined): string {
  const status = (value || "").toLowerCase();
  if (status.includes("derived")) return "restricted";
  if (status.includes("verified") || status.includes("fresh") || status.includes("success") || status.includes("live") || status === "normal") return "fresh";
  if (status.includes("review") || status.includes("research") || status.includes("aging") || status.includes("watch") || status.includes("partial")) return "aging";
  if (status.includes("stale") || status.includes("strain")) return "stale";
  if (status.includes("stress") || status.includes("fault") || status.includes("dead") || status.includes("fail")) return "dead";
  if (status.includes("block") || status.includes("compliance") || status.includes("restrict") || status.includes("withheld")) return "restricted";
  return "neutral";
}

function statusLabel(metric: MoneyMarketMetric | null | undefined): string {
  if (!metric) return "unavailable";
  if ((metric.availability || "").toUpperCase() === "DERIVED_CONTEXT") {
    const clock = (metric.freshness || metric.status || "").toLowerCase();
    if (clock.includes("stale") || clock.includes("dead")) return "stale · derived context";
    if (clock.includes("aging")) return "aging · derived context";
    return "derived context";
  }
  if ((metric.availability || "").toUpperCase() === "RESTRICTED") return "restricted";
  if ((metric.availability || "").toUpperCase() === "UNAVAILABLE") return "unavailable";
  if (metric.freshness) return metric.freshness.replaceAll("_", " ").toLowerCase();
  return (metric.status || metric.availability || "available").replaceAll("_", " ").toLowerCase();
}

function pressureLabel(percentile: number | null): string {
  if (percentile === null) return "history building";
  if (percentile >= 90) return "upper historical tail";
  if (percentile >= 75) return "above its usual range";
  if (percentile <= 10) return "lower historical tail";
  if (percentile <= 25) return "below its usual range";
  return "inside its usual range";
}

function coverageWidth(value: number | null | undefined): string {
  const number = finite(value);
  return String(clamp(number ?? 0, 0, 100)) + "%";
}

function historyRows(metric: MoneyMarketMetric | null | undefined): (string | number | null)[][] {
  if (!metric || !Array.isArray(metric.history) || !isPublicValue(metric)) return [];
  const rows: Array<[string, number]> = [];
  for (const item of metric.history) {
    if (!Array.isArray(item) || typeof item[0] !== "string") continue;
    const value = finite(item[1]);
    if (value === null || Number.isNaN(Date.parse(item[0]))) continue;
    rows.push([item[0], value]);
  }
  return rows.sort((left, right) => left[0].localeCompare(right[0]));
}

function publicAdapters(adapters: MarketAdapter[] | undefined): MarketAdapter[] {
  return (adapters || []).filter((adapter) => {
    const redistribution = (adapter.redistribution_status || "").toLowerCase();
    const classification = (adapter.classification || "").toLowerCase();
    return redistribution !== "prohibited" && classification !== "tenant_market_data";
  });
}

function nativeChange(metric: MoneyMarketMetric): { label: string; value: number | null } {
  const candidates: Array<[string, unknown]> = [
    ["1 obs", metric.change_1_observation],
    ["1d", metric.change_1d],
    ["1w", metric.change_1w],
    ["1m", metric.change_1m],
    ["5 obs", metric.change_5_observations],
    ["5d", metric.change_5d],
    ["4w", metric.change_4w],
    ["3m", metric.change_3m],
  ];
  for (const [label, value] of candidates) {
    const number = finite(value);
    if (number !== null) return { label, value: number };
  }
  return { label: "native", value: null };
}

function fallbackAtlas(engine: UsdMoneyMarketEngine, generatedAt?: string): MoneyMarketAtlas {
  const metrics = engine.sections.flatMap((section) =>
    section.metrics.map((metric) => ({
      ...metric,
      availability: (metric.status || "").toLowerCase() === "available" ? "AVAILABLE" : "UNAVAILABLE",
      redistribution_status: "allowed",
      history: [],
    })));
  const byId = new Map(metrics.map((metric) => [metric.id, metric]));
  const available = metrics.filter(isPublicValue).length;
  const total = metrics.length;
  const benchmark = byId.get("policy.sofr") || null;
  const anchor = byId.get("policy.iorb") || null;
  const spread = byId.get("policy.sofr_minus_iorb") || null;
  const benchmarkIsCurrent = hasCurrentClock(benchmark);
  return {
    ok: true,
    schema: "seiche.money-market.usd-fallback.v1",
    generated_at: engine.asof || generatedAt,
    status: "USD_FALLBACK",
    plain_language: engine.plain_language,
    quant_read: engine.quant_read,
    strongest_divergence: engine.strongest_signal,
    countercase: engine.countercase,
    coverage: { declared_markets: 1, live_benchmarks: benchmarkIsCurrent ? 1 : 0, planned_markets: 0 },
    markets: [{
      market_id: "US-USD",
      monetary_area_id: "US",
      region: "Americas",
      display_name: "United States dollar",
      jurisdiction_codes: ["US"],
      currency: "USD",
      timezone: "America/New_York",
      settlement_calendar: "US federal business days",
      policy_regime: "floor system",
      support_status: "supported",
      status: benchmarkIsCurrent
        ? "LIVE_REFERENCE"
        : isPublicValue(benchmark)
          ? "STALE_REFERENCE"
          : "DECLARED_UNAVAILABLE",
      plain_language: engine.plain_language,
      quant_read: engine.quant_read,
      countercase: engine.countercase,
      benchmark,
      derived_benchmark: null,
      policy_anchor: anchor,
      policy_relative_spread: spread,
      metrics,
      coverage: {
        declared_instruments: total,
        public_available: available,
        restricted: 0,
        unavailable: total - available,
        coverage_pct: total ? 100 * available / total : 0,
      },
      adapters: [],
      faults: [],
      known_gaps: metrics.filter((metric) => !isPublicValue(metric)).map((metric) => metric.label),
    }],
    expansion_ledger: [],
    methodology: engine.methodology,
    caveats: [
      "The global v2 atlas is unavailable. This view is limited to the USD desk carried in the overview snapshot.",
      ...(engine.caveats || []),
    ],
  };
}

function StatusPill({ value }: { value: string | null | undefined }) {
  return <span className={"mm-state mm-state--" + statusTone(value)}>{(value || "unknown").replaceAll("_", " ")}</span>;
}

function SectionHead({ eyebrow, title, note, titleId }: { eyebrow: string; title: string; note?: string; titleId?: string }) {
  return (
    <header className="mm-section-head">
      <div>
        <span>{eyebrow}</span>
        <h2 id={titleId}>{title}</h2>
      </div>
      {note && <p>{note}</p>}
    </header>
  );
}

function LegalNoticePanel({ notices, title = "Source terms and independence" }: { notices: LegalNotice[]; title?: string }) {
  return (
    <div className="mm-legal" aria-label={title}>
      <h3>{title}</h3>
      <div className="mm-legal__items">
        {notices.map((notice, index) => (
          <article key={notice.title + String(index)}>
            <b>{notice.title}</b>
            <p>{notice.text}</p>
            {notice.url && <small>Reference terms retained in Seiche’s evidence registry.</small>}
          </article>
        ))}
      </div>
    </div>
  );
}

function ClearingLadder({ market }: { market: MoneyMarket }) {
  const benchmark = market.benchmark;
  const contextBenchmark = comparisonBenchmark(market);
  const derivedOnly = !benchmark && isDerivedContext(contextBenchmark);
  const anchor = market.policy_anchor;
  const spread = market.policy_relative_spread;
  const percentile = mayShowStatistics(contextBenchmark) ? ownPercentile(contextBenchmark) : null;
  const z = mayShowStatistics(contextBenchmark) ? ownZ(contextBenchmark) : null;
  const markerBottom = percentile === null ? 50 : clamp(percentile, 2, 98);
  const rungs = [90, 75, 50, 25, 10];
  return (
    <figure className="mm-ladder" aria-labelledby="mm-ladder-caption">
      <figcaption id="mm-ladder-caption">
        <span>CLEARING LADDER / OWN HISTORY</span>
        <strong>{contextBenchmark?.label || "Local benchmark unavailable"}</strong>
        <small>
          Vertical position is this benchmark’s own-history percentile, never a cross-currency rate scale.
          {derivedOnly ? " The raw licensed quote and history are withheld." : ""}
        </small>
      </figcaption>
      <div className="mm-ladder__body">
        <div className="mm-ladder__rail" aria-hidden="true">
          {rungs.map((rung) => (
            <div className="mm-ladder__rung" key={rung} style={{ bottom: String(rung) + "%" }}>
              <i />
              <span>p{rung}</span>
            </div>
          ))}
          {percentile !== null && (
            <div className="mm-ladder__marker" style={{ bottom: String(markerBottom) + "%" }}>
              <b>{market.currency}</b>
              <span>p{percentile.toFixed(1)}</span>
            </div>
          )}
          {percentile === null && <div className="mm-ladder__ghost">history<br />building</div>}
        </div>
        <div className="mm-ladder__readout">
          <div>
            <span>LOCAL PRINT</span>
            <strong>{isPublicValue(benchmark) ? fmt(benchmark?.value, benchmark?.unit) : derivedOnly ? "WITHHELD" : "unavailable"}</strong>
            <small>{shortDate(contextBenchmark?.asof)} · {formatCadence(contextBenchmark?.cadence)}</small>
          </div>
          <div>
            <span>POLICY ANCHOR</span>
            <strong>{isPublicValue(anchor) ? fmt(anchor?.value, anchor?.unit) : "unavailable"}</strong>
            <small>{anchor?.label || "compatible anchor not available"}</small>
          </div>
          <div>
            <span>SAME-DATE GAP</span>
            <strong>{isPublicValue(spread) ? fmt(spread?.value, spread?.unit, true) : "unavailable"}</strong>
            <small>{spread?.alignment || "no compatible same-date intersection"}</small>
          </div>
        </div>
      </div>
      <dl className="mm-ladder__stats">
        <div><dt>own-history position</dt><dd>{percentile === null ? "insufficient history" : "p" + percentile.toFixed(1) + " · " + pressureLabel(percentile)}</dd></div>
        <div><dt>robust deviation</dt><dd>{z === null ? "insufficient history" : fmt(z, "z", true)}</dd></div>
        <div><dt>sample</dt><dd>{contextBenchmark?.n_observations ?? "—"} native observations</dd></div>
      </dl>
    </figure>
  );
}

function MarketTile({ market, selected, onSelect }: { market: MoneyMarket; selected: boolean; onSelect: () => void }) {
  const benchmark = market.benchmark;
  const contextBenchmark = comparisonBenchmark(market);
  const derivedOnly = !benchmark && isDerivedContext(contextBenchmark);
  const percentile = mayShowStatistics(contextBenchmark) ? ownPercentile(contextBenchmark) : null;
  const width = percentile === null ? "0%" : String(clamp(percentile, 0, 100)) + "%";
  return (
    <button
      type="button"
      className={"mm-market-tile" + (selected ? " is-selected" : "")}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <span className="mm-market-tile__top">
        <b>{market.currency}</b>
        <StatusPill value={contextBenchmark ? statusLabel(contextBenchmark) : "unavailable"} />
      </span>
      <strong>{market.display_name}</strong>
      <span className="mm-market-tile__scale" aria-hidden="true"><i style={{ width }} /></span>
      <span className="mm-market-tile__read">
        {percentile === null ? "own-history rank unavailable" : "own history p" + percentile.toFixed(1)}
      </span>
      <small>
        {isPublicValue(benchmark) ? fmt(benchmark?.value, benchmark?.unit) + " local quote" : derivedOnly ? "derived context · raw withheld" : "benchmark gap"}
        {" · "}{formatCadence(contextBenchmark?.cadence)}
      </small>
    </button>
  );
}

function MetricTable({ metrics, caption }: { metrics: MoneyMarketMetric[]; caption: string }) {
  return (
    <div className="mm-table-wrap">
      <table className="mm-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Instrument / meaning</th>
            <th scope="col">Latest</th>
            <th scope="col">Native change</th>
            <th scope="col">Own history</th>
            <th scope="col">Clock / evidence</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric) => {
            const publicValue = isPublicValue(metric);
            const derivedContext = isDerivedContext(metric);
            const statisticsVisible = mayShowStatistics(metric);
            const percentile = statisticsVisible ? ownPercentile(metric) : null;
            const z = statisticsVisible ? ownZ(metric) : null;
            const change = nativeChange(metric);
            const redistribution = (metric.redistribution_status || "allowed").toLowerCase();
            const sourceVisible = redistribution !== "prohibited";
            const ageAtEvaluation = finite(metric.age_days_vs_evaluation_asof);
            return (
              <tr key={metric.id}>
                <th scope="row">
                  <b>{metric.label}</b>
                  <span>{metric.explanation || metric.semantic_role?.replaceAll("_", " ").toLowerCase() || "Official local observation."}</span>
                  {metric.formula && <small>ƒ {metric.formula}</small>}
                </th>
                <td>
                  <strong>
                    {publicValue
                      ? fmt(metric.value, metric.unit)
                      : derivedContext
                        ? "raw withheld"
                        : (metric.availability || "").toUpperCase() === "RESTRICTED"
                          ? "withheld"
                          : "—"}
                  </strong>
                  <StatusPill value={statusLabel(metric)} />
                </td>
                <td>
                  <strong>{publicValue ? fmt(change.value, metric.change_unit || metric.unit, true) : "—"}</strong>
                  <span>{change.label}</span>
                </td>
                <td>
                  <strong>{percentile === null ? "—" : "p" + percentile.toFixed(1)}</strong>
                  <span>{z === null ? "z unavailable" : fmt(z, "z", true)}</span>
                </td>
                <td>
                  <strong>{formatCadence(metric.cadence)}</strong>
                  <span>as of {shortDate(metric.asof)}</span>
                  {ageAtEvaluation !== null && <small>{ageAtEvaluation}d old when evaluated</small>}
                  {sourceVisible && <small>{metric.source || "source metadata unavailable"}</small>}
                  {derivedContext && <small>non-reversible statistic only · raw quote/history withheld</small>}
                  {!sourceVisible && <small>source detail withheld by the redistribution policy</small>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const CHART_COLORS = [P.calm, P.accentSoft, P.erosion, P.slate, P.stress, P.gold];

function chartRows(value: unknown): (string | number | null)[][] {
  if (!Array.isArray(value)) return [];
  const rows: (string | number | null)[][] = [];
  for (const item of value) {
    if (!Array.isArray(item) || typeof item[0] !== "string" || Number.isNaN(Date.parse(item[0]))) continue;
    rows.push([
      item[0],
      ...item.slice(1).map((cell) => cell === null ? null : finite(cell)),
    ]);
  }
  return rows.sort((left, right) => String(left[0]).localeCompare(String(right[0])));
}

function humanSeries(value: string): string {
  const known: Record<string, string> = {
    sofr_pct: "SOFR",
    effr_pct: "EFFR",
    iorb_pct: "IORB",
    bgcr_pct: "BGCR",
    tgcr_pct: "TGCR",
    dvp_pct: "DVP repo",
    tri_pct: "Tri-party repo",
    gcf_pct: "GCF repo",
    p01_pct: "SOFR p01",
    rate_pct: "SOFR",
    p99_pct: "SOFR p99",
    sofr_minus_iorb_bp: "SOFR − IORB",
    effr_minus_iorb_bp: "EFFR − IORB",
    p99_minus_rate_bp: "SOFR upper tail",
    displayed_total_b: "Comparable total",
    total_assets_b: "Total MMF assets",
    total_repo_b: "Total MMF repo",
    ficc_repo_b: "FICC repo",
    fed_repo_b: "Fed repo",
    other_repo_b: "Other repo",
  };
  return known[value] || value
    .replace(/_(pct|bp|b)$/i, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

type ChartFamily = "rate" | "spread" | "stock" | "other";

function chartFamily(column: string): ChartFamily {
  if (column.endsWith("_pct")) return "rate";
  if (column.endsWith("_bp")) return "spread";
  if (column.endsWith("_b")) return "stock";
  return "other";
}

function chartFamilyLabel(family: ChartFamily): string {
  if (family === "rate") return "rate (%)";
  if (family === "spread") return "spread (bp)";
  if (family === "stock") return "amount ($B)";
  return "native units";
}

function splitUsdChart(chart: UsdChart): Array<{
  key: string;
  rows: (string | number | null)[][];
  series: Array<{ label: string; color: string }>;
  yLabel: string;
}> {
  const columns = Array.isArray(chart.columns) ? chart.columns.slice(1) : [];
  const sourceRows = chartRows(chart.rows);
  const families = Array.from(new Set(columns.map(chartFamily)));
  return families.flatMap((family) => {
    const indexes = columns
      .map((column, index) => ({ column, index }))
      .filter((item) => chartFamily(item.column) === family);
    if (indexes.length === 0) return [];
    const rows = sourceRows.map((row) => [row[0], ...indexes.map((item) => row[item.index + 1] ?? null)]);
    if (!rows.some((row) => row.slice(1).some((value) => finite(value) !== null))) return [];
    return [{
      key: (chart.id || chart.label || "chart") + ":" + family,
      rows,
      series: indexes.map((item, index) => ({
        label: humanSeries(item.column),
        color: CHART_COLORS[index % CHART_COLORS.length],
      })),
      yLabel: chartFamilyLabel(family),
    }];
  });
}

const USD_SECTION_CHARTS: Record<string, string[]> = {
  policy_corridor: ["policy"],
  secured_distributions: ["sofr_distribution"],
  repo_segments: ["repo_rates", "repo_volumes"],
  unsecured_funding: ["unsecured"],
  bills_cash_curve: ["bills"],
  liquidity_buffers: ["liquidity"],
  mmf_plumbing: ["mmf"],
};

function UsdChartShelf({ engine, sectionId }: { engine: UsdMoneyMarketEngine; sectionId: string }) {
  const charts = engine.charts || {};
  const panes = (USD_SECTION_CHARTS[sectionId] || [])
    .flatMap((id) => {
      const chart = charts[id];
      return chart ? splitUsdChart(chart).map((pane) => ({ ...pane, chart })) : [];
    });
  if (panes.length === 0) return null;
  return (
    <div className="mm-usd-charts" aria-label="USD section charts">
      {panes.map((pane) => (
        <article key={pane.key}>
          <header>
            <span>{pane.chart.cadence || "native cadence"}</span>
            <h3>{pane.chart.label || "USD money-market history"}</h3>
          </header>
          <Chart
            rows={pane.rows}
            series={pane.series}
            height={220}
            yLabel={pane.yLabel}
            source="Seiche point-in-time USD desk"
            note={`${pane.chart.sampling || "native observations"}; units split into separate axes`}
          />
        </article>
      ))}
    </div>
  );
}

function benchmarkPercentile(market: MoneyMarket): number | null {
  const benchmark = comparisonBenchmark(market);
  return hasCurrentStatistics(benchmark) ? ownPercentile(benchmark) : null;
}

function pressureTone(percentile: number | null): string {
  if (percentile === null) return "unknown";
  if (percentile >= 90) return "stress";
  if (percentile >= 75) return "watch";
  if (percentile <= 25) return "low";
  return "normal";
}

function PressureSonar({
  markets,
  selectedId,
  onOpen,
}: {
  markets: MoneyMarket[];
  selectedId?: string;
  onOpen: (marketId: string) => void;
}) {
  const ranked = markets
    .filter((market) => benchmarkPercentile(market) !== null)
    .sort((left, right) => (benchmarkPercentile(right) || 0) - (benchmarkPercentile(left) || 0));
  const unrankedIds = markets
    .filter((market) => benchmarkPercentile(market) === null)
    .map((market) => market.market_id);
  return (
    <figure className="mm-sonar" aria-labelledby="mm-sonar-title">
      <figcaption>
        <div><span>GLOBAL CASH SONAR</span><h2 id="mm-sonar-title">Pressure against evidence coverage</h2></div>
        <p>Higher means dearer cash versus that market’s own history. Farther right means more of its declared instrument set is public.</p>
      </figcaption>
      <div className="mm-sonar__body">
        <div className="mm-sonar__plot" role="group" aria-label="Markets plotted by own-history percentile and public coverage">
          <div className="mm-sonar__bands" aria-hidden="true"><i /><i /><i /><i /></div>
          <span className="mm-sonar__axis mm-sonar__axis--y">own-history pressure ↑</span>
          <span className="mm-sonar__axis mm-sonar__axis--x">public evidence coverage →</span>
          <span className="mm-sonar__threshold mm-sonar__threshold--90" aria-hidden="true">p90</span>
          <span className="mm-sonar__threshold mm-sonar__threshold--75" aria-hidden="true">p75</span>
          <span className="mm-sonar__threshold mm-sonar__threshold--50" aria-hidden="true">p50</span>
          {markets.map((market, index) => {
            const percentile = benchmarkPercentile(market);
            const coverage = clamp(finite(market.coverage.coverage_pct) ?? 0, 0, 100);
            const baseLeft = 10 + coverage * .8;
            const unrankedIndex = unrankedIds.indexOf(market.market_id);
            const collisionX = percentile === null
              ? ((unrankedIndex % 6) - 2.5) * 2.6
              : ((index % 3) - 1) * 1.2;
            const collisionY = percentile === null
              ? Math.floor(unrankedIndex / 6) * 7
              : (((index * 2) % 5) - 2) * .65;
            const left = clamp(baseLeft + collisionX, 7, 93);
            const top = percentile === null
              ? 82 + collisionY
              : clamp(8 + (100 - clamp(percentile, 0, 100)) * .72 + collisionY, 7, 81);
            return (
              <button
                type="button"
                key={market.market_id}
                className={`mm-sonar__point mm-sonar__point--${pressureTone(percentile)}${market.market_id === selectedId ? " is-selected" : ""}`}
                style={{ left: left + "%", top: top + "%", zIndex: 20 + markets.length - index }}
                onClick={() => onOpen(market.market_id)}
                aria-label={`${market.display_name}: ${percentile === null ? "history building" : `own-history percentile ${percentile.toFixed(1)}`}, ${coverage.toFixed(1)} percent coverage`}
              >
                <b>{market.currency}</b><span>{percentile === null ? "n/a" : "p" + percentile.toFixed(0)}</span>
              </button>
            );
          })}
          <span className="mm-sonar__dock">history building / unavailable rank</span>
        </div>
        <ol className="mm-sonar__ranking" aria-label="Ranked market pressure">
          {ranked.slice(0, 6).map((market, index) => {
            const percentile = benchmarkPercentile(market);
            return (
              <li key={market.market_id}>
                <button type="button" onClick={() => onOpen(market.market_id)}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <b>{market.currency}</b>
                  <i>{percentile === null ? "—" : "p" + percentile.toFixed(1)}</i>
                  <small>{pressureLabel(percentile)}</small>
                </button>
              </li>
            );
          })}
          {ranked.length === 0 && <li className="mm-sonar__none">No non-stale benchmark has enough history to rank.</li>}
        </ol>
      </div>
    </figure>
  );
}

function CoverageLanes({ markets }: { markets: MoneyMarket[] }) {
  return (
    <section className="mm-lanes" aria-labelledby="mm-lanes-title">
      <header><span>EVIDENCE DEPTH</span><h2 id="mm-lanes-title">What Seiche can actually see</h2></header>
      <div>
        {markets.map((market) => {
          const total = Math.max(market.coverage.declared_instruments || market.metrics.length, 1);
          const available = market.coverage.public_available || 0;
          const derived = market.coverage.derived_context || 0;
          const restricted = (market.coverage.restricted || 0) + (market.coverage.unavailable || 0);
          return (
            <div className="mm-lane" key={market.market_id}>
              <b>{market.currency}</b>
              <div className="mm-lane__bar" aria-label={`${market.display_name}: ${available} public, ${derived} derived, ${restricted} missing or restricted`}>
                <i className="is-public" style={{ width: `${100 * available / total}%` }} />
                <i className="is-derived" style={{ width: `${100 * derived / total}%` }} />
                <i className="is-gap" style={{ width: `${100 * restricted / total}%` }} />
              </div>
              <span>{available}/{total}</span>
            </div>
          );
        })}
      </div>
      <footer><span><i className="is-public" /> public</span><span><i className="is-derived" /> derived context</span><span><i className="is-gap" /> restricted / unavailable</span></footer>
    </section>
  );
}

function parseHarbors(value: unknown): HarborsEngine | null {
  if (!isRecord(value)) return null;
  return value as unknown as HarborsEngine;
}

function HarborPressure({
  engine,
  excludedHarbors = [],
}: {
  engine: HarborsEngine | null;
  excludedHarbors?: string[];
}) {
  const harbors = (engine?.harbors || [])
    .filter((harbor) => !excludedHarbors.includes(harbor.harbor || ""))
    .filter((harbor) => finite(harbor.stress) !== null)
    .sort((left, right) => (finite(right.stress) || 0) - (finite(left.stress) || 0));
  return (
    <section className="mm-harbor-pressure" aria-labelledby="mm-harbor-pressure-title">
      <header><span>TRANSMISSION CHECK</span><h2 id="mm-harbor-pressure-title">Local tightening, FX and volatility</h2></header>
      {harbors.length > 0 ? (
        <div>
          {harbors.map((harbor) => {
            const stress = clamp(finite(harbor.stress) || 0, 0, 100);
            return (
              <article key={harbor.harbor}>
                <div><b>{harbor.harbor}</b><span>{harbor.regime || "history building"}</span></div>
                <div className="mm-harbor-pressure__bar"><i style={{ width: stress + "%" }} /></div>
                <strong>{stress.toFixed(0)}</strong>
                <small>{harbor.rate?.chg_6m_bp == null ? "rate change unavailable" : fmt(harbor.rate.chg_6m_bp, "bp", true)} · {harbor.fx?.chg_60d_pct == null ? "FX unavailable" : fmt(harbor.fx.chg_60d_pct, "%", true) + " FX / 60d"}</small>
              </article>
            );
          })}
        </div>
      ) : <p className="mm-inline-gap">The global transmission engine is still accruing enough local history.</p>}
      <footer>Each score is a blend of that harbor’s own FX volatility, FX direction and six-month rate change. Missing components reweight the blend; they never count as calm.</footer>
    </section>
  );
}

function joinedRateHistory(market: MoneyMarket): {
  rows: (string | number | null)[][];
  metrics: MoneyMarketMetric[];
} {
  const metrics = market.metrics
    .filter((metric) => metric.unit === "%" && historyRows(metric).length >= 2)
    .slice(0, 5);
  const byDate = new Map<string, Map<string, number>>();
  for (const metric of metrics) {
    for (const row of historyRows(metric)) {
      const date = String(row[0]);
      const value = finite(row[1]);
      if (value === null) continue;
      const values = byDate.get(date) || new Map<string, number>();
      values.set(metric.id, value);
      byDate.set(date, values);
    }
  }
  const rows = [...byDate.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([date, values]) => [date, ...metrics.map((metric) => values.get(metric.id) ?? null)]);
  return { rows, metrics };
}

function DistributionPlot({ metric }: { metric: MoneyMarketMetric | null | undefined }) {
  const rows = historyRows(metric);
  const values = rows.map((row) => finite(row[1])).filter((value): value is number => value !== null);
  if (values.length < 5) {
    return <div className="mm-chart-gap mm-chart-gap--compact"><b>Distribution still forming</b><span>At least five public native observations are needed for a shape read.</span></div>;
  }
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = Math.max(high - low, Math.abs(high || 1) * .001);
  const binCount = Math.min(14, Math.max(6, Math.round(Math.sqrt(values.length))));
  const bins = Array.from({ length: binCount }, () => 0);
  values.forEach((value) => {
    const index = Math.min(binCount - 1, Math.floor((value - low) / span * binCount));
    bins[index] += 1;
  });
  const maxBin = Math.max(...bins, 1);
  const latest = values[values.length - 1];
  const latestX = 34 + (latest - low) / span * 532;
  return (
    <figure className="mm-distribution" aria-labelledby="mm-distribution-title">
      <figcaption><span>EMPIRICAL SHAPE</span><h3 id="mm-distribution-title">Where the current print sits</h3><p>{values.length} public native observations; bars count observations, not probability.</p></figcaption>
      <svg viewBox="0 0 600 210" role="img" aria-label={`${metric?.label || "Benchmark"} distribution from ${fmt(low, metric?.unit)} to ${fmt(high, metric?.unit)}; latest ${fmt(latest, metric?.unit)}`}>
        <line x1="34" y1="176" x2="566" y2="176" className="mm-distribution__axis" />
        {bins.map((count, index) => {
          const width = 532 / binCount - 4;
          const height = 132 * count / maxBin;
          return <rect key={index} x={36 + index * (532 / binCount)} y={176 - height} width={width} height={height} rx="2" className="mm-distribution__bar" />;
        })}
        <line x1={latestX} y1="30" x2={latestX} y2="183" className="mm-distribution__latest" />
        <text x={latestX} y="20" textAnchor="middle" className="mm-distribution__label">LATEST</text>
        <text x="34" y="202" className="mm-distribution__tick">{fmt(low, metric?.unit)}</text>
        <text x="566" y="202" textAnchor="end" className="mm-distribution__tick">{fmt(high, metric?.unit)}</text>
      </svg>
    </figure>
  );
}

function MomentumTable({ market }: { market: MoneyMarket }) {
  const rows = market.metrics.filter((metric) => mayShowStatistics(metric));
  return (
    <div className="mm-momentum">
      <header><span>MULTI-HORIZON READ</span><h3>Level, movement and instability</h3></header>
      <div className="mm-table-wrap">
        <table className="mm-table">
          <caption>Momentum and distribution diagnostics for {market.display_name}</caption>
          <thead><tr><th scope="col">Instrument</th><th scope="col">1 obs</th><th scope="col">5 obs</th><th scope="col">20 obs</th><th scope="col">Robust z</th><th scope="col">Percentile</th><th scope="col">20-observation volatility</th></tr></thead>
          <tbody>
            {rows.map((metric) => (
              <tr key={metric.id}>
                <th scope="row"><b>{metric.label}</b><span>{formatCadence(metric.cadence)}</span></th>
                <td>{fmt(metric.change_1_observation, metric.change_unit || metric.unit, true)}</td>
                <td>{fmt(metric.change_5_observations, metric.change_unit || metric.unit, true)}</td>
                <td>{fmt(metric.change_20_observations, metric.change_unit || metric.unit, true)}</td>
                <td>{fmt(ownZ(metric), "z", true)}</td>
                <td>{ownPercentile(metric) === null ? "—" : "p" + ownPercentile(metric)?.toFixed(1)}</td>
                <td>{fmt(metric.change_vol_20_annualized, metric.change_vol_unit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function labeledSeriesRows(
  rowsValue: unknown,
  labels: string[] | undefined,
  label: string,
): (string | number | null)[][] {
  const rows = chartRows(rowsValue);
  const index = (labels || []).findIndex((item) => item.toUpperCase() === label.toUpperCase());
  if (index < 0) return [];
  return rows.map((row) => [row[0], row[index + 1] ?? null]);
}

function ChinaDesk({
  engine,
  market,
  onOpenLab,
}: {
  engine: HarborsEngine | null;
  market: MoneyMarket | null;
  onOpenLab: () => void;
}) {
  const china = engine?.harbors?.find((item) => item.harbor === "CHINA") || null;
  const fxRows = labeledSeriesRows(engine?.fx_rows, engine?.fx_labels, "CHINA");
  const benchmark = market ? comparisonBenchmark(market) : null;
  const unsecuredRate = market?.metrics.find((metric) => metric.id.includes("SHIBOR")) || null;
  const securedRate = market?.metrics.find((metric) => metric.id.includes("FDR007")) || null;
  const canonicalCoverage = clamp(finite(market?.coverage.coverage_pct) || 0, 0, 100);
  return (
    <div className="mm-china-desk">
      <section className="mm-china-thesis">
        <div>
          <span>CHINA CASH / SEICHE SPECIAL SITUATION</span>
          <h2>China is a three-valve system.</h2>
          <p>Read unsecured cash, secured funding and the currency together. No one line is allowed to impersonate the whole market.</p>
        </div>
        <div className="mm-china-thesis__mark" aria-hidden="true"><i /><i /><i /><b>CNY</b></div>
      </section>

      <section className="mm-china-pulse" aria-label="Latest China money-market readings">
        <article><span>UNSECURED CASH</span><strong>{isPublicValue(unsecuredRate) ? fmt(unsecuredRate?.value, unsecuredRate?.unit) : "WITHHELD"}</strong><p>{unsecuredRate?.label || "SHIBOR overnight"}</p><small>{statusLabel(unsecuredRate)}</small></article>
        <article><span>SECURED 7-DAY FIXING</span><strong>{isPublicValue(securedRate) ? fmt(securedRate?.value, securedRate?.unit) : "WITHHELD"}</strong><p>{securedRate?.label || "FDR007"}</p><small>{statusLabel(securedRate)}</small></article>
        <article><span>RATE EVIDENCE</span><strong>{[unsecuredRate, securedRate].filter((metric) => (metric?.availability || "").toUpperCase() === "RESTRICTED").length} / 2</strong><p>fixings rights-restricted</p><small>metadata only · never treated as calm</small></article>
        <article><span>CNY / 60 DAYS</span><strong>{fmt(china?.fx?.chg_60d_pct, "%", true)}</strong><p>{china?.fx?.vol10_ann_pct == null ? "volatility unavailable" : fmt(china.fx.vol10_ann_pct, "%") + " 10d annualized vol"}</p><small>{shortDate(china?.fx?.asof)}</small></article>
        <article><span>COMPOSITE PRESSURE</span><strong>WITHHELD</strong><p>rights-cleared projection pending</p><small>no legacy rate blend reused</small></article>
      </section>

      <div className="mm-china-chart-grid">
        <article className="mm-analysis-panel mm-analysis-panel--wide">
          <header><span>VALVE 01 / COMPOSITE PRESSURE</span><h3>A score needs a rights-cleared rate leg</h3><p>The legacy Harbor blend can incorporate restricted CFETS changes. Seiche does not reuse that blend here; this slot remains empty until the server publishes an approved projection.</p></header>
          <div className="mm-china-pressure-plot is-missing" role="img" aria-label={`China composite pressure is not published; canonical public raw coverage is ${canonicalCoverage.toFixed(0)} percent`}>
            <div className="mm-china-pressure-plot__scale"><i /><em className="at-25">25</em><em className="at-50">50</em><em className="at-75">75</em><strong>NOT PUBLISHED</strong></div>
            <div className="mm-china-pressure-plot__coverage"><span>CANONICAL RAW COVERAGE</span><i><b style={{ width: canonicalCoverage + "%" }} /></i><strong>{canonicalCoverage.toFixed(0)}%</strong></div>
            <p>Restricted evidence is not converted into an apparently calm score. The CNY valve below remains available because it comes from a separate public series.</p>
          </div>
        </article>
        <article className="mm-analysis-panel">
          <header><span>VALVE 02 / CURRENCY</span><h3>The currency pressure release</h3><p>CNY per USD indexed to 100 one year earlier. Up means a weaker renminbi.</p></header>
          {fxRows.length >= 2 ? <Chart rows={fxRows} series={[{ label: "CNY per USD, 1y ago = 100", color: P.erosion }]} height={230} yLabel="CNY index" refLine={{ value: 100, color: P.ghost, label: "one-year anchor" }} source="Seiche global FX cache" note="daily fixes; indexed only for direction and scale" /> : <div className="mm-chart-gap mm-chart-gap--compact"><b>FX history unavailable</b><span>No direction is inferred from a missing currency series.</span></div>}
        </article>
        <article className="mm-analysis-panel">
          <header><span>VALVE 03 / EVIDENCE RIGHTS</span><h3>The canonical China pack stays deliberately sparse</h3><p>Availability is a data fact. Restricted or missing instruments cannot be turned into a calm reading.</p></header>
          <div className="mm-rights-map" aria-label="China canonical instrument availability">
            {(market?.metrics || []).map((metric) => <div key={metric.id}><b>{metric.label}</b><i className={`is-${(metric.availability || "unavailable").toLowerCase()}`} /><span>{statusLabel(metric)}</span></div>)}
            {(market?.metrics.length || 0) === 0 && <p>No canonical China instrument register reached this snapshot.</p>}
          </div>
        </article>
      </div>

      <section className="mm-china-interpretation">
        <article><span>01 / PRICE</span><h3>Is unsecured cash repricing?</h3><p>{isPublicValue(unsecuredRate) ? `${unsecuredRate?.label || "The unsecured fixing"} is public in the canonical pack; its native changes appear in the Market Lab.` : "The exact fixing and its rate changes stay withheld under the canonical source terms, so Seiche leaves the direction open."}</p></article>
        <article><span>02 / COLLATERAL</span><h3>Does secured funding confirm it?</h3><p>{isPublicValue(securedRate) ? "The FDR007 fixing is public, but it still cannot be subtracted from a different-tenor unsecured contract as if the two matched." : "FDR007, a fixing calculated from underlying DR007 transactions, is registered as secured seven-day evidence, but its exact quote is rights-restricted. Missing confirmation is shown as missing—not as an easy collateral market."}</p></article>
        <article><span>03 / EXTERNAL VALVE</span><h3>Is pressure escaping through CNY?</h3><p>{china?.fx?.chg_60d_pct == null ? "The currency leg is unavailable." : `CNY moved ${fmt(china.fx.chg_60d_pct, "%", true)} versus USD over 60 days, with ${fmt(china.fx.vol10_ann_pct, "%")} annualized 10-day volatility.`}</p></article>
        <article className="mm-counter"><span>COUNTERCASE</span><h3>Policy and calendar can dominate.</h3><p>Tax dates, holiday liquidity operations, reserve requirements and benchmark changes can move one valve without producing broad funding stress.</p></article>
      </section>

      {market && (
        <section className="mm-china-atlas">
          <div><span>CANONICAL CNY PACK</span><h3>{market.display_name}</h3><p>{market.plain_language || "The canonical pack is still building a rights-cleared history."}</p></div>
          <div><strong>{benchmarkPercentile(market) === null ? "rank pending" : "p" + benchmarkPercentile(market)?.toFixed(1)}</strong><small>{fmt(market.coverage.coverage_pct, "%")} public raw coverage</small><button type="button" onClick={onOpenLab}>Open the CNY market lab →</button></div>
        </section>
      )}
    </div>
  );
}

function UsdDesk({ engine }: { engine: UsdMoneyMarketEngine }) {
  const [sectionId, setSectionId] = useState(engine.sections[0]?.id || "");
  useEffect(() => {
    if (!engine.sections.some((section) => section.id === sectionId)) {
      setSectionId(engine.sections[0]?.id || "");
    }
  }, [engine, sectionId]);
  const section = engine.sections.find((item) => item.id === sectionId) || engine.sections[0];
  if (!section) return null;
  const sources = engine.source_metadata || engine.sources || [];
  const counter = prose(engine.countercase);
  const suppliedNotices = readLegalNotices(engine.legal_notices);
  const legalNotices = suppliedNotices.length > 0 ? suppliedNotices : [NY_FED_FALLBACK_NOTICE];
  const deskAsOf = engine.freshness?.desk_asof || engine.asof;
  const evaluationAsOf = engine.freshness?.evaluation_asof;
  const regimeState = (engine.regime?.state || "CANNOT_ASSESS").replaceAll("_", " ");
  const staleMetrics = engine.sections.reduce(
    (count, item) => count + item.metrics.filter((metric) => metric.freshness?.toLowerCase() === "stale").length,
    0,
  );
  const adjustedPercentile = finite(engine.regime?.worst_stress_percentile);
  const rawPercentile = finite(engine.regime?.raw_worst_stress_percentile);
  const familySize = finite(engine.regime?.familywise_adjustment?.eligible_hypotheses);
  const regimeNote = adjustedPercentile === null
    ? staleMetrics > 0
      ? `${staleMetrics} stale ${staleMetrics === 1 ? "card stays" : "cards stay"} visible · excluded from regime`
      : "history threshold not met"
    : `family-wise adjusted p${adjustedPercentile.toFixed(1)}`
      + (rawPercentile === null ? "" : ` · raw max p${rawPercentile.toFixed(1)}`)
      + (familySize === null ? "" : ` · ${familySize} eligible channels`)
      + (staleMetrics > 0 ? " · stale cards excluded" : "");
  return (
    <section className="mm-usd-desk" aria-labelledby="mm-usd-heading">
      <SectionHead
        eyebrow="USD MICROSTRUCTURE / OVERVIEW ENGINE"
        title="Inside the dollar clearing stack"
        note="Descriptive context only: the regime uses a family-wise-adjusted rank; stale cards and slow policy-driven liquidity stocks stay visible but cannot set it."
        titleId="mm-usd-heading"
      />
      <div className="mm-usd-status">
        <div>
          <span>DESCRIPTIVE REGIME</span>
          <strong>{regimeState}</strong>
          <small>{regimeNote}</small>
        </div>
        <div>
          <span>METRIC COVERAGE</span>
          <strong>{fmt(engine.coverage?.coverage_pct, "%")}</strong>
          <small>{engine.coverage?.available_metrics ?? "—"} / {engine.coverage?.total_metrics ?? "—"} cards available</small>
        </div>
        <div>
          <span>DESK CLOCK</span>
          <strong>{shortDate(deskAsOf)}</strong>
          <small>evidence through · {evaluationAsOf ? `evaluated on ${shortDate(evaluationAsOf)}` : "evaluation date not supplied"}</small>
        </div>
      </div>
      <div className="mm-read-grid mm-read-grid--compact">
        <article><span>IN SIMPLE LANGUAGE</span><p>{engine.plain_language || "No plain-language reading is available."}</p></article>
        <article><span>QUANT READ</span><p>{engine.quant_read || "No scaled reading is available."}</p></article>
        <article className="mm-counter"><span>COUNTERCASE</span><p>{counter || "No independent counterweight has enough history."}</p></article>
      </div>
      <nav className="mm-section-tabs" aria-label="USD money-market sections">
        {engine.sections.map((item) => (
          <button
            key={item.id}
            type="button"
            className={item.id === section.id ? "is-active" : ""}
            aria-pressed={item.id === section.id}
            onClick={() => setSectionId(item.id)}
          >
            <span>{item.label}</span>
            <small>{item.available_metrics ?? "—"}/{item.total_metrics ?? item.metrics.length}</small>
          </button>
        ))}
      </nav>
      <div className="mm-subsection-intro">
        <div><StatusPill value={section.status} /><strong>{section.label}</strong></div>
        <p>{section.plain_language || "Each card keeps its native clock and exact-date alignment."}</p>
      </div>
      <UsdChartShelf engine={engine} sectionId={section.id} />
      <MetricTable metrics={section.metrics} caption={section.label + " metrics"} />
      <details className="mm-disclosure" open>
        <summary>USD source clock, formulas and guardrails</summary>
        <div className="mm-disclosure__grid">
          <div className="mm-table-wrap">
            <table className="mm-table mm-table--sources">
              <caption>USD source freshness</caption>
              <thead><tr><th scope="col">Source</th><th scope="col">Cadence</th><th scope="col">As of</th><th scope="col">Freshness</th></tr></thead>
              <tbody>
                {sources.map((source, index) => (
                  <tr key={source.id || String(index)}>
                    <th scope="row">
                      <b>{source.label || source.id || "source"}</b>
                      <span>{source.publisher || source.series || ""}</span>
                    </th>
                    <td>{formatCadence(source.cadence)}</td>
                    <td>{shortDate(source.asof)}</td>
                    <td>
                      <StatusPill value={source.freshness || (source.available ? "available" : "unavailable")} />
                      {finite(source.age_days_vs_evaluation_asof) !== null && <span>{finite(source.age_days_vs_evaluation_asof)}d old when evaluated</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mm-formulas">
            <h3>Formula register</h3>
            {(engine.formulas || []).map((formula, index) => (
              <div key={formula.id || String(index)}>
                <b>{formula.id || "formula"}</b>
                <code>{formula.expression || "not supplied"}</code>
                <small>{[formula.alignment, formula.unit].filter(Boolean).join(" · ")}</small>
              </div>
            ))}
          </div>
        </div>
        <ul className="mm-caveats">
          {(engine.caveats || []).map((caveat) => <li key={caveat}>{caveat}</li>)}
        </ul>
      </details>
      <LegalNoticePanel notices={legalNotices} title="USD source terms and independence" />
    </section>
  );
}

export default function MoneyMarkets({ snap }: Props) {
  const usdEngine = useMemo(() => parseUsdEngine(snap.engines?.money_market), [snap.engines?.money_market]);
  const harborsEngine = useMemo(() => parseHarbors(snap.engines?.harbors), [snap.engines?.harbors]);
  const usdFallback = useMemo(
    () => usdEngine ? fallbackAtlas(usdEngine, snap.generated_at) : null,
    [usdEngine, snap.generated_at],
  );
  const [atlas, setAtlas] = useState<MoneyMarketAtlas | null>(null);
  const [mode, setMode] = useState<FetchMode>("unavailable");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [view, setView] = useState<DeskView>("briefing");
  const [region, setRegion] = useState("ALL");
  const [marketId, setMarketId] = useState("");
  const [expansionQuery, setExpansionQuery] = useState("");
  const hasChinaDesk = mode === "live" && Boolean(atlas?.markets.some((market) => market.market_id === "CN-CNY"));

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 9000);
    let disposed = false;
    setLoading(true);
    setError(null);
    fetch(API_BASE + "/api/v2/money-markets", {
      headers: authHeaders(),
      credentials: "omit",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (response.status === 401) throw new Error("session expired");
        const contentType = response.headers.get("content-type") || "";
        if (!response.ok || !contentType.includes("json")) throw new Error("global atlas endpoint unavailable");
        const parsed = parseAtlas(await response.json());
        if (!parsed) throw new Error("global atlas returned an invalid contract");
        if (disposed) return;
        setAtlas(parsed);
        setMode("live");
      })
      .catch((reason: unknown) => {
        if (disposed) return;
        if (controller.signal.aborted && reason instanceof DOMException && reason.name === "AbortError") {
          setError("global atlas timed out");
        } else {
          setError(reason instanceof Error ? reason.message : "global atlas unavailable");
        }
        if (usdFallback) {
          setAtlas(usdFallback);
          setMode("usd-fallback");
        } else {
          setAtlas(null);
          setMode("unavailable");
        }
      })
      .finally(() => {
        window.clearTimeout(timeout);
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [retryKey, usdFallback]);

  useEffect(() => {
    if (!atlas?.markets.length) return;
    setMarketId((current) => {
      if (atlas.markets.some((market) => market.market_id === current)) return current;
      return atlas.markets.find((market) => market.market_id === "US-USD")?.market_id
        || atlas.markets.find((market) => comparisonBenchmark(market))?.market_id
        || atlas.markets[0].market_id;
    });
  }, [atlas]);

  useEffect(() => {
    if (atlas && region !== "ALL" && !atlas.markets.some((market) => market.region === region)) {
      setRegion("ALL");
    }
  }, [atlas, region]);

  useEffect(() => {
    if (view === "china" && !hasChinaDesk) setView("briefing");
  }, [hasChinaDesk, view]);

  const regions = useMemo(
    () => atlas ? Array.from(new Set(atlas.markets.map((market) => market.region))).sort() : [],
    [atlas],
  );
  const visibleMarkets = useMemo(
    () => atlas ? atlas.markets.filter((market) => region === "ALL" || market.region === region) : [],
    [atlas, region],
  );
  const selected = atlas?.markets.find((market) => market.market_id === marketId)
    || visibleMarkets[0]
    || atlas?.markets[0]
    || null;

  const selectRegion = (next: string) => {
    setRegion(next);
    if (!atlas || next === "ALL") return;
    const first = atlas.markets.find((market) => market.region === next);
    if (first && selected?.region !== next) setMarketId(first.market_id);
  };

  const selectedComparison = selected ? comparisonBenchmark(selected) : null;
  const selectedDerivedOnly = Boolean(selected && !selected.benchmark && isDerivedContext(selectedComparison));
  const rows = historyRows(selected?.benchmark);
  const methodologyEntries = Object.entries(atlas?.methodology || {});
  const adapters = publicAdapters(selected?.adapters);
  const selectedCountercase = prose(selected?.countercase);
  const globalCountercase = prose(atlas?.countercase);
  const strongest = prose(atlas?.strongest_divergence);
  const suppliedAtlasNotices = readLegalNotices(atlas?.legal_notices);
  const atlasLegalNotices = suppliedAtlasNotices.length > 0
    ? suppliedAtlasNotices
    : [GENERAL_INDEPENDENCE_NOTICE, NY_FED_FALLBACK_NOTICE];
  const visibleExpansion = useMemo(() => {
    const rows = atlas?.expansion_ledger || [];
    const query = expansionQuery.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter((row) => Object.values(row).some(
      (value) => typeof value === "string" && value.toLowerCase().includes(query),
    ));
  }, [atlas?.expansion_ledger, expansionQuery]);
  const expansionVerificationLabel = useMemo(() => {
    const dates = [...new Set((atlas?.expansion_ledger || [])
      .map((row) => row.verified_on)
      .filter((value): value is string => Boolean(value)))]
      .sort();
    if (dates.length === 0) return "verification date unavailable";
    if (dates.length === 1) return "verified " + dates[0];
    return "verification dates " + dates[0] + " to " + dates[dates.length - 1];
  }, [atlas?.expansion_ledger]);
  const spreadRows = historyRows(selected?.policy_relative_spread);
  const rateHistory = selected ? joinedRateHistory(selected) : { rows: [], metrics: [] };
  const chinaMarket = atlas?.markets.find((market) => market.market_id === "CN-CNY") || null;
  const openMarket = (nextMarketId: string) => {
    setRegion("ALL");
    setMarketId(nextMarketId);
    setView("lab");
  };
  const viewItems: Array<{ id: DeskView; label: string; note: string }> = [
    { id: "briefing", label: "Briefing", note: "what matters now" },
    { id: "world", label: "World map", note: "compare clearing systems" },
    { id: "lab", label: "Market lab", note: selected?.currency || "one market at a time" },
    ...(hasChinaDesk ? [{ id: "china" as DeskView, label: "China desk", note: "SHIBOR · FDR007 · CNY" }] : []),
    { id: "notes", label: "Data notes", note: "methods and coverage" },
  ];
  const declaredMarketCount = atlas?.coverage?.declared_markets ?? atlas?.markets.length;
  const clearingScope = declaredMarketCount === 1
    ? "One clearing system"
    : declaredMarketCount
      ? `${declaredMarketCount} clearing systems`
      : "Global clearing systems";

  return (
    <div
      className={`mm-shell mm-shell--${view}`}
      data-share-path={moneyMarketSharePath(selected?.market_id || "overview")}
    >
      <header className="mm-hero">
        <div className="mm-hero__index" aria-hidden="true">CASH / MM</div>
        <div className="mm-hero__copy">
          <span>SEICHE MONEY MARKETS / GLOBAL CASH INTELLIGENCE</span>
          <h1>Where cash is tightening—and why.</h1>
          <p>
            {clearingScope}, one evidence discipline. Seiche reads rates, collateral, liquidity and currency pressure in local conventions, then compares every market only with its own history.
          </p>
        </div>
        <div className="mm-hero__status">
          <StatusPill value={mode === "live" ? atlas?.status || "live" : mode === "usd-fallback" ? "USD fallback" : loading ? "loading" : "unavailable"} />
          <span>ATLAS CLOCK</span>
          <strong>{shortDate(atlas?.generated_at)}</strong>
          <small>
            {atlas?.coverage?.live_benchmarks ?? "—"} / {atlas?.coverage?.declared_markets ?? "—"} declared benchmarks live
            {(atlas?.coverage?.derived_context_benchmarks || 0) > 0 ? " · " + atlas?.coverage?.derived_context_benchmarks + " derived-only" : ""}
          </small>
        </div>
      </header>

      <nav className={`mm-view-nav mm-view-nav--${viewItems.length}`} aria-label="Money-market analysis views">
        {viewItems.map((item, index) => (
          <button
            type="button"
            key={item.id}
            className={view === item.id ? "is-active" : ""}
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => setView(item.id)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            <b>{item.label}</b>
            <small>{item.note}</small>
          </button>
        ))}
      </nav>

      {mode === "usd-fallback" && (
        <div className="mm-mode-note" role="status">
          <div><b>Global atlas unavailable.</b> Showing the last overview’s USD desk without inventing global calm.</div>
          <button type="button" onClick={() => setRetryKey((value) => value + 1)}>Retry global atlas</button>
        </div>
      )}

      {!atlas && (
        <section className="mm-empty" aria-live="polite">
          <span>{loading ? "SOUNDING GLOBAL CASH MARKETS" : "NO SAFE FALLBACK"}</span>
          <h2>{loading ? "Reading native publication clocks…" : "The atlas cannot be assessed."}</h2>
          <p>{loading ? "Official observations remain blank until the contract arrives." : (error || "Neither the global atlas nor the USD overview desk is available. Missing evidence is not rendered as calm.")}</p>
          {!loading && <button type="button" onClick={() => setRetryKey((value) => value + 1)}>Retry endpoint</button>}
        </section>
      )}

      {atlas && selected && (
        <>
          {view === "briefing" && (
            <div className="mm-view-stage" role="region" aria-label="Money-market briefing">
              <section className="mm-global-read" aria-labelledby="mm-global-title">
                <div>
                  <span>THE GLOBAL READ</span>
                  <h2 id="mm-global-title">{atlas.plain_language || "The declared markets remain visible, including their evidence gaps."}</h2>
                </div>
                <div className="mm-global-read__detail">
                  <article><b>QUANT BOUNDARY</b><p>{atlas.quant_read || "Comparisons use each benchmark’s own historical distribution."}</p></article>
                  <article><b>STRONGEST DIVERGENCE</b><p>{strongest || "No benchmark has enough native history for a scaled divergence."}</p></article>
                  <article className="mm-counter"><b>COUNTERCASE</b><p>{globalCountercase || "A policy move or calendar turn can explain a local outlier."}</p></article>
                </div>
              </section>
              <PressureSonar markets={atlas.markets} selectedId={selected.market_id} onOpen={openMarket} />
              <div className="mm-brief-grid">
                <CoverageLanes markets={atlas.markets} />
                {mode === "live" && <HarborPressure engine={harborsEngine} excludedHarbors={["CHINA"]} />}
              </div>
              <section className="mm-brief-actions" aria-label="Continue the analysis">
                <button type="button" onClick={() => setView("world")}><span>COMPARE</span><b>See every clearing system</b><small>Pressure, coverage and native clock →</small></button>
                <button type="button" onClick={() => openMarket(selected.market_id)}><span>DIAGNOSE</span><b>Open {selected.currency}</b><small>History, spread and distribution →</small></button>
                {hasChinaDesk && <button type="button" onClick={() => setView("china")}><span>SPECIAL SITUATION</span><b>Read China’s three valves</b><small>SHIBOR, FDR007 and CNY →</small></button>}
              </section>
            </div>
          )}

          {(view === "world" || view === "lab") && <section className={`mm-selector${view === "lab" ? " mm-selector--lab" : ""}`} aria-labelledby="mm-selector-title">
            <div className="mm-selector__lead">
              <span id="mm-selector-title">{view === "lab" ? "MARKET LAB SELECTOR" : "CHOOSE A CLEARING BASIN"}</span>
              <p>{view === "lab" ? "Change the local market without leaving the analytical workspace." : "Tiles share a 0–100 own-history ladder. Raw rates remain local quotes and are never ranked as universal stress."}</p>
            </div>
            <div className="mm-region-tabs" role="group" aria-label="Filter markets by region">
              {["ALL", ...regions].map((item) => (
                <button
                  key={item}
                  type="button"
                  className={item === region ? "is-active" : ""}
                  aria-pressed={item === region}
                  onClick={() => selectRegion(item)}
                >
                  {item}
                </button>
              ))}
            </div>
            <label className="mm-market-select">
              <span>Market</span>
              <select value={selected.market_id} onChange={(event) => setMarketId(event.target.value)}>
                {visibleMarkets.map((market) => (
                  <option value={market.market_id} key={market.market_id}>{market.currency} · {market.display_name}</option>
                ))}
              </select>
            </label>
            <div className="mm-market-grid">
              {visibleMarkets.map((market) => (
                <MarketTile
                  key={market.market_id}
                  market={market}
                  selected={market.market_id === selected.market_id}
                  onSelect={() => view === "world" ? openMarket(market.market_id) : setMarketId(market.market_id)}
                />
              ))}
            </div>
          </section>}

          {view === "world" && (
            <div className="mm-view-stage" role="region" aria-label="Global money-market map">
              <PressureSonar markets={visibleMarkets} selectedId={selected.market_id} onOpen={openMarket} />
              <CoverageLanes markets={visibleMarkets} />
              <section className="mm-ledger mm-ledger--world" aria-labelledby="mm-world-register-title">
                <SectionHead eyebrow="REGISTERED CLEARING SYSTEMS" title="The live atlas, without unlike-rate ranking" note="Coverage and local-history pressure are comparable; raw levels and unlike tenors are not." titleId="mm-world-register-title" />
                <div className="mm-table-wrap">
                  <table className="mm-table mm-table--ledger">
                    <caption>Registered global money-market packs</caption>
                    <thead><tr><th scope="col">Market</th><th scope="col">Benchmark</th><th scope="col">Own history</th><th scope="col">Public evidence</th><th scope="col">Clock</th><th scope="col">Open</th></tr></thead>
                    <tbody>{visibleMarkets.map((market) => {
                      const benchmark = comparisonBenchmark(market);
                      const percentile = benchmarkPercentile(market);
                      return <tr key={market.market_id}>
                        <th scope="row"><b>{market.currency} · {market.display_name}</b><span>{market.region} · {market.market_id}</span></th>
                        <td><b>{benchmark?.label || "declared gap"}</b><span>{formatCadence(benchmark?.cadence)}</span></td>
                        <td><b>{percentile === null ? "building" : "p" + percentile.toFixed(1)}</b><span>{pressureLabel(percentile)}</span></td>
                        <td><b>{fmt(market.coverage.coverage_pct, "%")}</b><span>{market.coverage.public_available || 0}/{market.coverage.declared_instruments || market.metrics.length} public</span></td>
                        <td><StatusPill value={market.status} /></td>
                        <td><button type="button" className="mm-open-button" onClick={() => openMarket(market.market_id)}>Analyse →</button></td>
                      </tr>;
                    })}</tbody>
                  </table>
                </div>
              </section>
            </div>
          )}

          {view === "lab" && <>

          <section className="mm-local" aria-labelledby="mm-local-title">
            <header className="mm-local__head">
              <div>
                <span>{selected.region} / {selected.market_id}</span>
                <h2 id="mm-local-title">{selected.currency} · {selected.display_name}</h2>
                <p>{selected.policy_regime?.replaceAll("_", " ") || "policy regime not supplied"} · {selected.timezone || "timezone unavailable"} · {selected.settlement_calendar || "settlement calendar unavailable"}</p>
              </div>
              <div className="mm-local__chips">
                <StatusPill value={selected.status} />
                <span className="mm-state mm-state--neutral">{formatCadence(selectedComparison?.cadence)}</span>
                <span className="mm-state mm-state--neutral">{fmt(selected.coverage.coverage_pct, "%")} raw coverage</span>
                {(selected.coverage.derived_context || 0) > 0 && <span className="mm-state mm-state--restricted">{selected.coverage.derived_context} derived</span>}
                {(selected.coverage.restricted || 0) > 0 && <span className="mm-state mm-state--restricted">{selected.coverage.restricted} restricted</span>}
              </div>
            </header>

            <div className="mm-local__primary">
              <ClearingLadder market={selected} />
              <div className="mm-read-grid">
                <article><span>IN SIMPLE LANGUAGE</span><p>{selected.plain_language || "No local benchmark observation is available."}</p></article>
                <article><span>QUANT DETAIL</span><p>{selected.quant_read || "The own-history distribution does not yet meet its minimum sample."}</p></article>
                <article className="mm-counter"><span>COUNTERCASE</span><p>{selectedCountercase || "A local move can reflect policy, a calendar turn, or a benchmark-method change."}</p></article>
                <article className="mm-coverage-card">
                  <span>PUBLIC RAW COVERAGE</span>
                  <strong>{fmt(selected.coverage.coverage_pct, "%")}</strong>
                  <div className="mm-coverage-bar" aria-label={fmt(selected.coverage.coverage_pct, "%") + " of declared instruments publicly available"}>
                    <i style={{ width: coverageWidth(selected.coverage.coverage_pct) }} />
                  </div>
                  <small>
                    {selected.coverage.public_available ?? 0} public · {selected.coverage.derived_context ?? 0} derived · {selected.coverage.restricted ?? 0} restricted · {selected.coverage.unavailable ?? 0} unavailable
                  </small>
                </article>
              </div>
            </div>
          </section>

          <section className="mm-lab-charts" aria-labelledby="mm-history-title">
            <SectionHead eyebrow="MARKET DIAGNOSTICS" title="Level, policy gap, curve and distribution" note="Every chart keeps one unit and one native clock. Missing history remains an explicit gap." titleId="mm-history-title" />
            <div className="mm-lab-chart-grid">
              <article className="mm-analysis-panel mm-analysis-panel--wide">
                <header><span>01 / BENCHMARK</span><h3>{selectedComparison?.label || "Benchmark history unavailable"}</h3><p>No interpolation, upsampling or cross-market level comparison.</p></header>
                {rows.length >= 2 ? (
                  <Chart key={selected.market_id + ":" + selected.benchmark?.id} rows={rows} series={[{ label: selected.benchmark?.label || selected.currency, color: P.calm }]} height={260} yLabel={(selected.benchmark?.label || "local benchmark") + " (" + (selected.benchmark?.unit || "native") + ")"} source={selected.benchmark?.source || "Seiche official-source cache"} asOf={selected.benchmark?.asof} note={formatCadence(selected.benchmark?.cadence) + " observations; local convention only"} />
                ) : <div className="mm-chart-gap"><b>History not yet drawable</b><span>{isPublicValue(selected.benchmark) ? "The live print is available, but fewer than two public observations reached this payload." : selectedDerivedOnly ? "Only non-reversible statistics may be shown; the raw level and history remain withheld." : "The benchmark is restricted, unavailable or stale beyond a safe public reading."}</span></div>}
              </article>
              <article className="mm-analysis-panel">
                <header><span>02 / POLICY GAP</span><h3>{selected.policy_relative_spread?.label || "Same-date policy gap"}</h3><p>Exact event-date intersection only; no forward fill.</p></header>
                {spreadRows.length >= 2 ? <Chart rows={spreadRows} series={[{ label: selected.policy_relative_spread?.label || "Policy-relative spread", color: P.accentSoft }]} height={230} yLabel="policy-relative spread (bp)" refLine={{ value: 0, color: P.ghost, label: "at anchor" }} source="Seiche exact-date derivation" asOf={selected.policy_relative_spread?.asof} note={selected.policy_relative_spread?.alignment || "no forward fill"} /> : <div className="mm-chart-gap mm-chart-gap--compact"><b>Compatible policy gap unavailable</b><span>A missing exact-date intersection is not filled or inferred.</span></div>}
              </article>
              <article className="mm-analysis-panel"><DistributionPlot metric={selected.benchmark} /></article>
              <article className="mm-analysis-panel mm-analysis-panel--wide">
                <header><span>03 / LOCAL RATE SET</span><h3>Which part of the curve is moving?</h3><p>Only public rates quoted in percent share this axis; stocks, volumes and basis-point spreads stay out.</p></header>
                {rateHistory.rows.length >= 2 && rateHistory.metrics.length >= 2 ? <Chart rows={rateHistory.rows} series={rateHistory.metrics.map((metric, index) => ({ label: metric.label, color: CHART_COLORS[index % CHART_COLORS.length] }))} height={250} yLabel="local rates (%)" source="Seiche local instrument register" note="outer date grid with null gaps; no interpolation" /> : <div className="mm-chart-gap"><b>Multi-rate curve unavailable</b><span>At least two public local-rate histories are required. One line is never dressed up as a curve.</span></div>}
              </article>
            </div>
            <MomentumTable market={selected} />
          </section>

          <section className="mm-metrics" aria-labelledby="mm-metrics-title">
            <SectionHead
              eyebrow="LOCAL INSTRUMENT REGISTER"
              title="Rates, facilities, liquidity and gaps"
              note="Every row states its native cadence. Restricted rows disclose the gap, never the value."
              titleId="mm-metrics-title"
            />
            <div>
              <MetricTable metrics={selected.metrics} caption={selected.display_name + " declared money-market instruments"} />
            </div>
          </section>

          {selected.market_id === "US-USD" && usdEngine && <UsdDesk engine={usdEngine} />}
          </>}

          {view === "china" && hasChinaDesk && <ChinaDesk engine={harborsEngine} market={chinaMarket} onOpenLab={() => openMarket("CN-CNY")} />}

          {view === "notes" && <>
          <section className="mm-notes-intro">
            <div><span>THE EVIDENCE ROOM</span><h2>Trust the analysis without leaving Seiche.</h2></div>
            <p>Publishers, clocks, rights limits, formulas and missing observations live here as product data—not as outbound reading assignments.</p>
            <label><span>INSPECT MARKET</span><select value={selected.market_id} onChange={(event) => setMarketId(event.target.value)}>{atlas.markets.map((market) => <option value={market.market_id} key={market.market_id}>{market.currency} · {market.display_name}</option>)}</select></label>
          </section>
          <section className="mm-evidence" aria-labelledby="mm-evidence-title">
            <SectionHead
              eyebrow="PUBLICATION CONTROL"
              title="Source clocks and known absences"
              note="Collector health is evidence metadata, not a market signal."
              titleId="mm-evidence-title"
            />
            <div className="mm-evidence__grid">
              <div className="mm-table-wrap">
                <table className="mm-table mm-table--sources">
                  <caption>Public source adapters for {selected.display_name}</caption>
                  <thead><tr><th scope="col">Adapter</th><th scope="col">Native clock</th><th scope="col">Run state</th><th scope="col">Next due</th></tr></thead>
                  <tbody>
                    {adapters.length > 0 ? adapters.map((adapter, index) => {
                      return (
                        <tr key={adapter.adapter_id || String(index)}>
                          <th scope="row">
                            <b>{adapter.adapter_id || "official source"}</b>
                            <span>{adapter.classification?.replaceAll("_", " ") || "public adapter"}</span>
                          </th>
                          <td>{formatCadence(adapter.expected_cadence)}</td>
                          <td><StatusPill value={adapter.last_run_status} /><small>{shortDate(adapter.last_finished_at)}</small></td>
                          <td>{shortDate(adapter.next_due)}</td>
                        </tr>
                      );
                    }) : (
                      <tr><td colSpan={4}>No public adapter run metadata is present in this payload.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <aside className="mm-gaps">
                <span>KNOWN GAPS</span>
                <strong>{selected.known_gaps?.length || 0}</strong>
                <p>Unavailable evidence stays visible so a sparse screen cannot masquerade as a calm market.</p>
                <ul>
                  {(selected.known_gaps || []).slice(0, 12).map((gap) => <li key={gap}>{gap}</li>)}
                  {(selected.known_gaps?.length || 0) > 12 && <li>+ {(selected.known_gaps?.length || 0) - 12} more in the instrument register</li>}
                </ul>
              </aside>
            </div>
            <div className="mm-audit-boundary">
              <div>
                <span>ATLAS CAVEATS / EVIDENCE RIGHTS</span>
                <h3>What this desk may—and may not—show</h3>
                <p>Restrictions change display rights, not the meaning of zero. A withheld or unavailable value never becomes a calm print.</p>
              </div>
              <div className="mm-audit-boundary__caveats">
                <b>Published atlas caveats</b>
                <ul>
                  {(atlas.caveats || ["No atlas caveat register was supplied."]).map((caveat) => <li key={caveat}>{caveat}</li>)}
                </ul>
              </div>
              <LegalNoticePanel notices={atlasLegalNotices} title="Rights, terms and independence" />
            </div>
          </section>

          <section className="mm-ledger" aria-labelledby="mm-ledger-title">
            <SectionHead
              eyebrow="EXPANSION COVERAGE LEDGER"
              title="Live, declared and next"
              note="A roadmap row is not presented as data coverage."
              titleId="mm-ledger-title"
            />
            <div className="mm-ledger__summary">
              <div><span>DECLARED</span><strong>{atlas.coverage?.declared_markets ?? atlas.markets.length}</strong></div>
              <div><span>LIVE BENCHMARKS</span><strong>{atlas.coverage?.live_benchmarks ?? atlas.markets.filter((market) => market.benchmark).length}</strong></div>
              <div><span>DERIVED CONTEXT</span><strong>{atlas.coverage?.derived_context_benchmarks ?? atlas.markets.filter((market) => market.derived_benchmark).length}</strong></div>
              <div><span>DISCOVERY UNIVERSE</span><strong>{atlas.coverage?.global_discovery_universe ?? atlas.markets.length + (atlas.expansion_ledger?.length || 0)}</strong></div>
            </div>
            <div className="mm-table-wrap">
              <table className="mm-table mm-table--ledger">
                <caption>Current declared market packs</caption>
                <thead><tr><th scope="col">Market</th><th scope="col">Region</th><th scope="col">Benchmark</th><th scope="col">Public coverage</th><th scope="col">State</th></tr></thead>
                <tbody>
                  {atlas.markets.map((market) => (
                    <tr key={market.market_id}>
                      <th scope="row"><b>{market.currency} · {market.display_name}</b><span>{market.market_id}</span></th>
                      <td>{market.region}</td>
                      <td>
                        {market.benchmark?.label
                          || (market.derived_benchmark ? market.derived_benchmark.label + " · derived only" : "declared gap")}
                      </td>
                      <td>
                        {fmt(market.coverage.coverage_pct, "%")}
                        <span>
                          {market.coverage.public_available ?? 0}/{market.coverage.declared_instruments ?? market.metrics.length} public
                          {(market.coverage.derived_context || 0) > 0 ? " · " + market.coverage.derived_context + " derived" : ""}
                        </span>
                      </td>
                      <td><StatusPill value={market.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(atlas.expansion_ledger?.length || 0) > 0 ? (
              <div className="mm-expansion">
                <div className="mm-expansion__control">
                  <label>
                    <span>SEARCH THE SOURCE-AUDITED QUEUE</span>
                    <input
                      type="search"
                      value={expansionQuery}
                      onChange={(event) => setExpansionQuery(event.target.value)}
                      placeholder="Country, currency, benchmark, authority or access state"
                    />
                  </label>
                  <p>
                    Showing {visibleExpansion.length} of {atlas.expansion_ledger?.length || 0} candidates · {expansionVerificationLabel}.
                    These are source records, not live quotes.
                  </p>
                </div>
                <div className="mm-table-wrap">
                  <table className="mm-table mm-table--ledger mm-table--expansion">
                    <caption>Global expansion queue — official-source metadata only</caption>
                    <thead><tr><th scope="col">Market</th><th scope="col">Region</th><th scope="col">Candidate benchmark</th><th scope="col">Official source</th><th scope="col">Rights / integration limit</th><th scope="col">Stage</th></tr></thead>
                    <tbody>
                      {visibleExpansion.map((row, index) => {
                        return (
                          <tr key={row.market_id || String(index)}>
                            <th scope="row">
                              <b>{row.currency || "—"} · {row.market || row.market_id || "planned market"}</b>
                              <span>{row.market_id || "not assigned"}</span>
                            </th>
                            <td>{row.region || "—"}</td>
                            <td>
                              <b>{row.benchmark || "to validate"}</b>
                              <span>{row.benchmark_kind || "benchmark taxonomy pending"}</span>
                            </td>
                            <td>
                              {row.authority || "to validate"}
                              <span>{row.access?.replaceAll("_", " ").toLowerCase() || "access review pending"}</span>
                            </td>
                            <td>{row.access_note || "Methodology, endpoint and rights review pending."}</td>
                            <td>
                              <StatusPill value={row.status || "planned"} />
                              <span>{row.confidence ? row.confidence.toLowerCase() + " source confidence" : "confidence pending"}</span>
                            </td>
                          </tr>
                        );
                      })}
                      {visibleExpansion.length === 0 && (
                        <tr><td colSpan={6}>No expansion record matches this search.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="mm-ledger-gap">Expansion ledger unavailable in the degraded USD fallback.</div>
            )}
          </section>

          <details className="mm-disclosure mm-disclosure--method">
            <summary>Methodology, formulas and limits</summary>
            <div className="mm-method-grid">
              {methodologyEntries.map(([key, value]) => (
                <div key={key}><b>{key.replaceAll("_", " ")}</b><p>{prose(value) || String(value)}</p></div>
              ))}
            </div>
          </details>
          </>}
        </>
      )}
    </div>
  );
}
