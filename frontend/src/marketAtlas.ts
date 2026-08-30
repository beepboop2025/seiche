export interface EvidenceEligibility {
  eligible: boolean;
  reasons: string[];
  value_encoding?: string | null;
  restricted_values?: string | null;
}

export interface MarketCatalogItem {
  market_id: string;
  monetary_area_id: string | null;
  display_name: string;
  jurisdiction_codes: string[];
  currency: string;
  local_timezone: string | null;
  policy_regime: string | null;
  support_status: string | null;
  event_cutoff: string | null;
  knowledge_cutoff: string | null;
  evidence_eligibility: EvidenceEligibility | null;
  stale_input_count: number;
  fault_count: number;
}

export interface MarketCatalog {
  schema: string | null;
  count: number;
  collection_policy: string | null;
  markets: MarketCatalogItem[];
}

export interface MarketInstrument {
  instrument_id: string;
  mnemonic: string;
  semantic_role: string;
  canonical_unit: string;
  source_adapter: string;
  publisher: string | null;
  source_url: string | null;
  connector_classification: string;
  redistribution_status: string;
  expected_cadence: string | null;
  availability: string;
}

export interface MarketObservation {
  market_id: string;
  monetary_area_id: string | null;
  jurisdiction_codes: string[];
  currency: string;
  instrument_id: string;
  semantic_role: string;
  value: string | number | null;
  value_status: string | null;
  canonical_unit: string;
  rate_compounding: string | null;
  day_count: string | null;
  event_time: string;
  source_publication_time: string | null;
  knowledge_time: string | null;
  revision_id: string;
  source: string;
  evidence_hash: string | null;
  connector_classification: string;
  redistribution_status: string;
  quality: string;
  staleness: string;
}

export interface SeriesCoverage {
  semantic_role: string;
  observations: number;
  event_start: string | null;
  event_end: string | null;
  latest_knowledge_time: string | null;
  unavailable_observations: number;
}

export interface MarketSeries {
  schema: string | null;
  status: string;
  market_id: string;
  monetary_area_id: string | null;
  jurisdiction_codes: string[];
  currency: string;
  policy_regime: string | null;
  support_status: string | null;
  calibration_id: string | null;
  coverage_scope: string | null;
  readiness_scope: string | null;
  event_cutoff: string | null;
  knowledge_cutoff: string | null;
  evidence_eligibility: EvidenceEligibility;
  data_coverage: SeriesCoverage[];
  instruments: MarketInstrument[];
  observations: MarketObservation[];
  stale_input_count: number;
  fault_count: number;
  next_cursor: string | null;
}

export interface AtlasFilters {
  query: string;
  role: string | null;
  instrumentId: string | null;
}

export interface NumericSeriesPoint {
  eventTime: string;
  knowledgeTime: string | null;
  value: number;
  observation: MarketObservation;
}

export interface PlotPoint extends NumericSeriesPoint {
  x: number;
  y: number;
}

export interface PlotModel {
  points: PlotPoint[];
  path: string;
  areaPath: string;
  observedMinValue: number;
  observedMaxValue: number;
  minValue: number;
  maxValue: number;
  firstEventTime: string;
  lastEventTime: string;
  baseline: number;
}

export type AtlasStateTone = "positive" | "caution" | "danger" | "restricted" | "neutral";

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function textOr(value: unknown, fallback: string): string {
  return text(value) ?? fallback;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(text).filter((item): item is string => item !== null);
}

function count(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function nonNegativeInteger(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 0;
}

function optionalValue(value: unknown): string | number | null {
  return typeof value === "string" || typeof value === "number" ? value : null;
}

function eligibility(value: unknown): EvidenceEligibility | null {
  const row = record(value);
  if (!row) return null;
  return {
    eligible: row.eligible === true,
    reasons: stringList(row.reasons ?? (text(row.reason) ? [row.reason] : [])),
    value_encoding: text(row.value_encoding),
    restricted_values: text(row.restricted_values),
  };
}

function normalizeCatalogItem(value: unknown): MarketCatalogItem | null {
  const row = record(value);
  const marketId = text(row?.market_id);
  if (!row || !marketId) return null;
  return {
    market_id: marketId,
    monetary_area_id: text(row.monetary_area_id),
    display_name: textOr(row.display_name, marketId),
    jurisdiction_codes: stringList(row.jurisdiction_codes),
    currency: textOr(row.currency, "—"),
    local_timezone: text(row.local_timezone),
    policy_regime: text(row.policy_regime),
    support_status: text(row.support_status),
    event_cutoff: text(row.event_cutoff),
    knowledge_cutoff: text(row.knowledge_cutoff),
    evidence_eligibility: eligibility(row.evidence_eligibility),
    stale_input_count: count(row.stale_inputs),
    fault_count: count(row.faults),
  };
}

export function normalizeMarketCatalog(value: unknown): MarketCatalog {
  const root = record(value);
  if (!root || !Array.isArray(root.markets)) {
    throw new Error("Market catalog response does not contain a markets array.");
  }
  const markets = root.markets
    .map(normalizeCatalogItem)
    .filter((item): item is MarketCatalogItem => item !== null)
    .sort((left, right) => left.display_name.localeCompare(right.display_name));
  if (markets.length === 0) {
    throw new Error("Market catalog returned no usable market definitions.");
  }
  return {
    schema: text(root.schema),
    count: nonNegativeInteger(root.count) || markets.length,
    collection_policy: text(root.collection_policy),
    markets,
  };
}

function normalizeInstrument(value: unknown): MarketInstrument | null {
  const row = record(value);
  const instrumentId = text(row?.instrument_id);
  if (!row || !instrumentId) return null;
  return {
    instrument_id: instrumentId,
    mnemonic: textOr(row.mnemonic, instrumentId),
    semantic_role: textOr(row.semantic_role, "UNSPECIFIED"),
    canonical_unit: textOr(row.canonical_unit, "unit_unavailable"),
    source_adapter: textOr(row.source_adapter, "source unavailable"),
    publisher: text(row.publisher),
    source_url: text(row.source_url),
    connector_classification: textOr(
      row.connector_classification,
      "classification unavailable",
    ),
    redistribution_status: textOr(
      row.redistribution_status,
      "rights unavailable",
    ),
    expected_cadence: text(row.expected_cadence),
    availability: textOr(row.availability, "UNAVAILABLE"),
  };
}

function normalizeObservation(value: unknown): MarketObservation | null {
  const row = record(value);
  const instrumentId = text(row?.instrument_id);
  const eventTime = text(row?.event_time);
  if (!row || !instrumentId || !eventTime) return null;
  return {
    market_id: textOr(row.market_id, "market unavailable"),
    monetary_area_id: text(row.monetary_area_id),
    jurisdiction_codes: stringList(row.jurisdiction_codes),
    currency: textOr(row.currency, "—"),
    instrument_id: instrumentId,
    semantic_role: textOr(row.semantic_role, "UNSPECIFIED"),
    value: optionalValue(row.value),
    value_status: text(row.value_status),
    canonical_unit: textOr(row.canonical_unit, "unit_unavailable"),
    rate_compounding: text(row.rate_compounding),
    day_count: text(row.day_count),
    event_time: eventTime,
    source_publication_time: text(row.source_publication_time),
    knowledge_time: text(row.knowledge_time),
    revision_id: textOr(row.revision_id, "revision unavailable"),
    source: textOr(row.source, "source unavailable"),
    evidence_hash: text(row.evidence_hash),
    connector_classification: textOr(
      row.connector_classification,
      "classification unavailable",
    ),
    redistribution_status: textOr(
      row.redistribution_status,
      "rights unavailable",
    ),
    quality: textOr(row.quality, "quality unavailable"),
    staleness: textOr(row.staleness, "unknown"),
  };
}

function normalizeCoverage(value: unknown): SeriesCoverage | null {
  const row = record(value);
  const role = text(row?.semantic_role);
  if (!row || !role) return null;
  return {
    semantic_role: role,
    observations: nonNegativeInteger(row.observations),
    event_start: text(row.event_start),
    event_end: text(row.event_end),
    latest_knowledge_time: text(row.latest_knowledge_time),
    unavailable_observations: nonNegativeInteger(row.unavailable_observations),
  };
}

export function normalizeMarketSeries(
  value: unknown,
  expectedMarketId?: string,
): MarketSeries {
  const root = record(value);
  const marketId = text(root?.market_id);
  if (!root || !marketId || !Array.isArray(root.instruments) || !Array.isArray(root.observations)) {
    throw new Error("Market series response is missing its market, instruments, or observations.");
  }
  if (expectedMarketId && marketId !== expectedMarketId) {
    throw new Error(`Market series identity mismatch: expected ${expectedMarketId}, received ${marketId}.`);
  }
  const instruments = root.instruments
    .map(normalizeInstrument)
    .filter((item): item is MarketInstrument => item !== null);
  const observations = mergeObservationPages(
    [],
    root.observations
      .map(normalizeObservation)
      .filter((item): item is MarketObservation => item !== null),
  );
  const parsedEligibility = eligibility(root.evidence_eligibility);
  return {
    schema: text(root.schema),
    status: textOr(root.status, "UNAVAILABLE"),
    market_id: marketId,
    monetary_area_id: text(root.monetary_area_id),
    jurisdiction_codes: stringList(root.jurisdiction_codes),
    currency: textOr(root.currency, "—"),
    policy_regime: text(root.policy_regime),
    support_status: text(root.support_status),
    calibration_id: text(root.calibration_id),
    coverage_scope: text(root.coverage_scope),
    readiness_scope: text(root.readiness_scope),
    event_cutoff: text(root.event_cutoff),
    knowledge_cutoff: text(root.knowledge_cutoff),
    evidence_eligibility: parsedEligibility ?? {
      eligible: false,
      reasons: ["Evidence eligibility was not reported by the API."],
    },
    data_coverage: Array.isArray(root.data_coverage)
      ? root.data_coverage
          .map(normalizeCoverage)
          .filter((item): item is SeriesCoverage => item !== null)
      : [],
    instruments,
    observations,
    stale_input_count: count(root.stale_inputs),
    fault_count: count(root.faults),
    next_cursor: text(root.next_cursor),
  };
}

export function numericValue(value: unknown): number | null {
  if (typeof value === "string" && value.trim() === "") return null;
  if (typeof value !== "string" && typeof value !== "number") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function observationIdentity(observation: MarketObservation): string {
  return [
    observation.market_id,
    observation.instrument_id,
    observation.event_time,
    observation.knowledge_time ?? "",
    observation.source,
    observation.revision_id,
  ].join("\u001f");
}

function timestamp(value: string | null): number {
  if (!value) return Number.NEGATIVE_INFINITY;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function chronological(left: MarketObservation, right: MarketObservation): number {
  return (
    timestamp(left.event_time) - timestamp(right.event_time)
    || left.instrument_id.localeCompare(right.instrument_id)
    || timestamp(left.knowledge_time) - timestamp(right.knowledge_time)
    || left.source.localeCompare(right.source)
    || left.revision_id.localeCompare(right.revision_id)
  );
}

export function mergeObservationPages(
  current: readonly MarketObservation[],
  older: readonly MarketObservation[],
): MarketObservation[] {
  const merged = new Map<string, MarketObservation>();
  for (const observation of [...current, ...older]) {
    merged.set(observationIdentity(observation), observation);
  }
  return [...merged.values()].sort(chronological);
}

function includesQuery(values: Array<string | null>, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return values.some((value) => value?.toLocaleLowerCase().includes(needle));
}

export function filterInstruments(
  instruments: readonly MarketInstrument[],
  query: string,
  role: string | null,
): MarketInstrument[] {
  return instruments.filter((instrument) => {
    if (role && instrument.semantic_role !== role) return false;
    return includesQuery(
      [
        instrument.instrument_id,
        instrument.mnemonic,
        instrument.semantic_role,
        instrument.canonical_unit,
        instrument.source_adapter,
        instrument.connector_classification,
        instrument.redistribution_status,
        instrument.availability,
      ],
      query,
    );
  });
}

export function filterObservations(
  observations: readonly MarketObservation[],
  filters: AtlasFilters,
): MarketObservation[] {
  return observations
    .filter((observation) => {
      if (filters.role && observation.semantic_role !== filters.role) return false;
      if (filters.instrumentId && observation.instrument_id !== filters.instrumentId) {
        return false;
      }
      return includesQuery(
        [
          observation.instrument_id,
          observation.semantic_role,
          observation.source,
          observation.revision_id,
          observation.canonical_unit,
          observation.connector_classification,
          observation.redistribution_status,
          observation.quality,
          observation.staleness,
        ],
        filters.query,
      );
    })
    .sort((left, right) => chronological(right, left));
}

export function numericSeriesForInstrument(
  observations: readonly MarketObservation[],
  instrumentId: string,
): NumericSeriesPoint[] {
  const latestVintageByEvent = new Map<string, NumericSeriesPoint>();
  for (const observation of observations) {
    if (observation.instrument_id !== instrumentId) continue;
    if (!isPublicPlotObservation(observation)) continue;
    const value = numericValue(observation.value);
    if (value === null) continue;
    const candidate: NumericSeriesPoint = {
      eventTime: observation.event_time,
      knowledgeTime: observation.knowledge_time,
      value,
      observation,
    };
    const current = latestVintageByEvent.get(observation.event_time);
    if (!current || timestamp(candidate.knowledgeTime) >= timestamp(current.knowledgeTime)) {
      latestVintageByEvent.set(observation.event_time, candidate);
    }
  }
  return [...latestVintageByEvent.values()].sort(
    (left, right) => timestamp(left.eventTime) - timestamp(right.eventTime),
  );
}

const PLOTTABLE_QUALITIES = new Set(["verified", "provisional", "revised", "estimated"]);

/**
 * Tables retain non-public and rejected rows so the evidence boundary remains
 * inspectable. Charts are a narrower projection: only explicitly redistributable
 * values with a known usable quality and no redaction marker may become geometry.
 */
export function isPublicPlotObservation(observation: MarketObservation): boolean {
  if (observation.redistribution_status.trim().toLocaleLowerCase() !== "allowed") return false;
  if (observation.value_status !== null) return false;
  if (!PLOTTABLE_QUALITIES.has(observation.quality.trim().toLocaleLowerCase())) return false;
  return Number.isFinite(timestamp(observation.event_time));
}

function roundCoordinate(value: number): number {
  return Math.round(value * 100) / 100;
}

export function buildPlotModel(
  points: readonly NumericSeriesPoint[],
  width = 720,
  height = 220,
): PlotModel | null {
  if (points.length === 0 || width <= 120 || height <= 100) return null;
  const left = 58;
  const right = 16;
  const top = 18;
  const bottom = 34;
  const eventTimes = points.map((point) => timestamp(point.eventTime));
  let minTime = Math.min(...eventTimes);
  let maxTime = Math.max(...eventTimes);
  if (!Number.isFinite(minTime) || !Number.isFinite(maxTime)) return null;
  if (minTime === maxTime) {
    minTime -= 43_200_000;
    maxTime += 43_200_000;
  }
  const values = points.map((point) => point.value);
  const observedMinValue = Math.min(...values);
  const observedMaxValue = Math.max(...values);
  let minValue = observedMinValue;
  let maxValue = observedMaxValue;
  if (minValue === maxValue) {
    const margin = Math.max(Math.abs(minValue) * 0.05, 1);
    minValue -= margin;
    maxValue += margin;
  }
  const xSpan = width - left - right;
  const ySpan = height - top - bottom;
  const plotted = points.map((point) => ({
    ...point,
    x: roundCoordinate(left + ((timestamp(point.eventTime) - minTime) / (maxTime - minTime)) * xSpan),
    y: roundCoordinate(top + ((maxValue - point.value) / (maxValue - minValue)) * ySpan),
  }));
  const path = plotted
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");
  const baseline = height - bottom;
  const areaPath = `${path} L ${plotted[plotted.length - 1].x} ${baseline} L ${plotted[0].x} ${baseline} Z`;
  return {
    points: plotted,
    path,
    areaPath,
    observedMinValue,
    observedMaxValue,
    minValue,
    maxValue,
    firstEventTime: points[0].eventTime,
    lastEventTime: points[points.length - 1].eventTime,
    baseline,
  };
}

export function canonicalUnitLabel(unit: string, currency?: string): string {
  switch (unit) {
    case "basis_points":
      return "bp";
    case "local_currency_millions":
      return `${currency || "local currency"} mn`;
    case "index_points":
      return "index points";
    case "ratio":
      return "ratio";
    case "count":
      return "count";
    case "contracts":
      return "contracts";
    default:
      return unit.replaceAll("_", " ");
  }
}

export function safePublicSourceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

export function atlasStateTone(value: string): AtlasStateTone {
  const state = value.trim().toLocaleLowerCase().replaceAll(/[^a-z0-9]+/g, "-");
  if (["ready", "fresh", "verified", "revised", "eligible", "allowed"].includes(state)) {
    return "positive";
  }
  if (["partial", "aging", "provisional", "estimated", "data-hold"].includes(state)) {
    return "caution";
  }
  if (["stale", "dead", "rejected"].includes(state)) return "danger";
  if (["restricted", "derived-context", "derived-only", "metadata-only"].includes(state)) {
    return "restricted";
  }
  return "neutral";
}

export function roleLabel(role: string): string {
  return role.replaceAll("_", " ").toLocaleLowerCase();
}
