export type BisDomain = "funding" | "fx" | "credit" | "markets" | "payments" | "macro";

export type BisEvidenceClass =
  | "observed"
  | "derived"
  | "structural"
  | "restricted"
  | "unavailable";

export interface BisFlowRecord {
  flow_id: string;
  name?: string;
  topic?: string;
  availability?: string;
  priority_tier?: number;
  product_scores?: Record<string, number>;
  cautions?: string[];
}

export interface BisRights {
  usage_class: string;
  license_url: string | null;
  commercial_training_eligible: boolean;
  knowledge_time: string | null;
  public_values: boolean;
}

export interface BisRecordSource {
  publisher: string;
  url: string;
  capture_id: number;
  capture_sha256: string;
  capture_knowledge_time: string;
  source_row_number: number;
  license_url: string | null;
  attribution_required: boolean;
}

export interface BisRecord {
  format: string;
  logical_id: string;
  flow_id: string;
  flow_name: string;
  flow_version: string;
  series_key: string;
  dimensions: Record<string, string>;
  dimension_labels: Record<string, string>;
  period: Record<string, unknown>;
  event_time: string | null;
  event_time_basis: string | null;
  scheduled_release_time: string | null;
  knowledge_time: string;
  first_knowledge_time: string;
  knowledge_time_basis: string;
  as_of_rule: string;
  historical_vintage_reconstructed: boolean;
  value_text: string;
  value_decimal: string | null;
  value_numeric: number | null;
  action: string;
  attributes: Record<string, string>;
  attribute_labels: Record<string, string>;
  revision_number: number;
  semantic_sha256: string;
  source: BisRecordSource;
  product_scores: Record<string, number>;
  topic: string;
  evidence_class: BisEvidenceClass;
}

export interface BisPage {
  schema_version: string;
  generated_at: string | null;
  knowledge_time: string | null;
  artifact_generated_at: string | null;
  artifact_knowledge_time: string | null;
  artifact_sha256: string | null;
  serving_generated_at: string | null;
  flow_id: string;
  evidence_class: BisEvidenceClass;
  rights: BisRights;
  count: number;
  complete_snapshot: boolean | null;
  next_cursor: string | null;
  records: BisRecord[];
}

const DOMAIN_PATTERNS: ReadonlyArray<[BisDomain, RegExp]> = [
  ["payments", /payment|clearing|settlement|cashless|device|participant/i],
  ["credit", /credit|banking|bank loan|debt service|property/i],
  ["fx", /exchange rate|currency|effective exchange|\bfx\b/i],
  ["markets", /securit|derivative|turnover|notional|issuance/i],
  ["funding", /policy rate|central bank|liquidity|balance.sheet/i],
];

const EVIDENCE_CLASSES = new Set<BisEvidenceClass>([
  "observed",
  "derived",
  "structural",
  "restricted",
  "unavailable",
]);

function objectValue(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`BIS ${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`BIS ${field} is missing`);
  }
  return value;
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function requiredInteger(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value)) throw new Error(`BIS ${field} is invalid`);
  return value as number;
}

function requiredCount(value: unknown, field: string): number {
  const count = requiredInteger(value, field);
  if (count < 0) throw new Error(`BIS ${field} is invalid`);
  return count;
}

function nullableBoolean(value: unknown, field: string): boolean | null {
  if (value !== null && typeof value !== "boolean") {
    throw new Error(`BIS ${field} is invalid`);
  }
  return value;
}

function stringMap(value: unknown, field: string): Record<string, string> {
  const source = objectValue(value, field);
  const entries = Object.entries(source);
  if (entries.some(([key, item]) => !key || typeof item !== "string")) {
    throw new Error(`BIS ${field} contains a non-string value`);
  }
  return Object.fromEntries(entries) as Record<string, string>;
}

function scoreMap(value: unknown): Record<string, number> {
  const source = objectValue(value, "product_scores");
  if (Object.values(source).some((item) => typeof item !== "number" || !Number.isFinite(item))) {
    throw new Error("BIS product_scores contains an invalid score");
  }
  return source as Record<string, number>;
}

export function normalizeBisEvidence(value: unknown): BisEvidenceClass {
  return typeof value === "string" && EVIDENCE_CLASSES.has(value as BisEvidenceClass)
    ? value as BisEvidenceClass
    : "unavailable";
}

function normalizeRights(value: unknown): BisRights {
  const rights = objectValue(value, "rights");
  return {
    usage_class: optionalText(rights.usage_class) ?? "unclassified",
    license_url: optionalText(rights.license_url),
    commercial_training_eligible: rights.commercial_training_eligible === true,
    knowledge_time: optionalText(rights.knowledge_time),
    public_values: rights.public_values === true,
  };
}

function normalizeSource(value: unknown): BisRecordSource {
  const source = objectValue(value, "record source");
  return {
    publisher: requiredText(source.publisher, "source publisher"),
    url: requiredText(source.url, "source URL"),
    capture_id: requiredInteger(source.capture_id, "source capture_id"),
    capture_sha256: requiredText(source.capture_sha256, "source capture_sha256"),
    capture_knowledge_time: requiredText(
      source.capture_knowledge_time,
      "source capture_knowledge_time",
    ),
    source_row_number: requiredInteger(source.source_row_number, "source row number"),
    license_url: optionalText(source.license_url),
    attribution_required: source.attribution_required === true,
  };
}

function normalizeRecord(value: unknown, expectedFlowId: string): BisRecord {
  const row = objectValue(value, "record");
  const flowId = requiredText(row.flow_id, "record flow_id");
  if (flowId !== expectedFlowId) throw new Error("BIS response mixed records from another flow");
  const numeric = row.value_numeric;
  if (
    numeric !== undefined
    && numeric !== null
    && (typeof numeric !== "number" || !Number.isFinite(numeric))
  ) {
    throw new Error("BIS value_numeric is invalid");
  }
  const decimal = row.value_decimal;
  if (decimal !== undefined && decimal !== null && typeof decimal !== "string") {
    throw new Error("BIS value_decimal is invalid");
  }
  if (typeof row.historical_vintage_reconstructed !== "boolean") {
    throw new Error("BIS historical_vintage_reconstructed is invalid");
  }
  return {
    format: requiredText(row.format, "format"),
    logical_id: requiredText(row.logical_id, "logical_id"),
    flow_id: flowId,
    flow_name: requiredText(row.flow_name, "flow_name"),
    flow_version: requiredText(row.flow_version, "flow_version"),
    series_key: requiredText(row.series_key, "series_key"),
    dimensions: stringMap(row.dimensions, "dimensions"),
    dimension_labels: stringMap(row.dimension_labels, "dimension_labels"),
    period: objectValue(row.period, "period"),
    event_time: optionalText(row.event_time),
    event_time_basis: optionalText(row.event_time_basis),
    scheduled_release_time: optionalText(row.scheduled_release_time),
    knowledge_time: requiredText(row.knowledge_time, "knowledge_time"),
    first_knowledge_time: requiredText(row.first_knowledge_time, "first_knowledge_time"),
    knowledge_time_basis: requiredText(row.knowledge_time_basis, "knowledge_time_basis"),
    as_of_rule: requiredText(row.as_of_rule, "as_of_rule"),
    historical_vintage_reconstructed: row.historical_vintage_reconstructed,
    value_text: typeof row.value_text === "string" ? row.value_text : "",
    value_decimal: typeof decimal === "string" ? decimal : null,
    value_numeric: typeof numeric === "number" ? numeric : null,
    action: requiredText(row.action, "action"),
    attributes: stringMap(row.attributes, "attributes"),
    attribute_labels: stringMap(row.attribute_labels, "attribute_labels"),
    revision_number: requiredInteger(row.revision_number, "revision_number"),
    semantic_sha256: requiredText(row.semantic_sha256, "semantic_sha256"),
    source: normalizeSource(row.source),
    product_scores: scoreMap(row.product_scores),
    topic: requiredText(row.topic, "topic"),
    evidence_class: normalizeBisEvidence(row.evidence_class),
  };
}

export function normalizeBisPage(value: unknown, expectedFlowId: string): BisPage {
  const page = objectValue(value, "page");
  const flowId = requiredText(page.flow_id, "page flow_id");
  if (flowId !== expectedFlowId) throw new Error("BIS response belongs to another flow");
  if (!Array.isArray(page.records)) throw new Error("BIS records are missing");
  const records = page.records.map((row) => normalizeRecord(row, flowId));
  const count = requiredCount(page.count, "count");
  if (count !== records.length) throw new Error("BIS count does not match the response records");
  return {
    schema_version: requiredText(page.schema_version, "schema_version"),
    generated_at: optionalText(page.generated_at),
    knowledge_time: optionalText(page.knowledge_time),
    artifact_generated_at: optionalText(page.artifact_generated_at),
    artifact_knowledge_time: optionalText(page.artifact_knowledge_time),
    artifact_sha256: optionalText(page.artifact_sha256),
    serving_generated_at: optionalText(page.serving_generated_at),
    flow_id: flowId,
    evidence_class: normalizeBisEvidence(page.evidence_class),
    rights: normalizeRights(page.rights),
    count,
    complete_snapshot: nullableBoolean(page.complete_snapshot, "complete_snapshot"),
    next_cursor: optionalText(page.next_cursor),
    records,
  };
}

export function isBisBulkUnavailable(
  page: BisPage,
  _availability: string | undefined,
): boolean {
  return page.evidence_class === "unavailable"
    && page.serving_generated_at === null
    && page.count === 0
    && page.records.length === 0;
}

export function bisDomain(flow: BisFlowRecord): BisDomain {
  const text = `${flow.name ?? ""} ${flow.topic ?? ""}`;
  return DOMAIN_PATTERNS.find(([, pattern]) => pattern.test(text))?.[0] ?? "macro";
}

export function sortBisFlows(flows: readonly BisFlowRecord[]): BisFlowRecord[] {
  return [...flows].sort((left, right) => {
    const tier = (left.priority_tier ?? 99) - (right.priority_tier ?? 99);
    if (tier !== 0) return tier;
    const fit = (right.product_scores?.seiche ?? 0) - (left.product_scores?.seiche ?? 0);
    return fit !== 0 ? fit : left.flow_id.localeCompare(right.flow_id);
  });
}

export function filterBisFlows(
  flows: readonly BisFlowRecord[],
  domain: BisDomain | "all",
  query: string,
): BisFlowRecord[] {
  const needle = query.trim().toLocaleLowerCase();
  return sortBisFlows(flows).filter((flow) => {
    if (domain !== "all" && bisDomain(flow) !== domain) return false;
    if (!needle) return true;
    return `${flow.flow_id} ${flow.name ?? ""} ${flow.topic ?? ""}`
      .toLocaleLowerCase()
      .includes(needle);
  });
}

export function filterBisRecords(records: readonly BisRecord[], query: string): BisRecord[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [...records];
  return records.filter((row) => {
    const dimensionText = Object.entries(row.dimension_labels)
      .map(([key, value]) => `${key} ${value}`)
      .join(" ");
    return `${row.logical_id} ${row.series_key} ${row.flow_name} ${row.topic} ${row.value_text} ${dimensionText}`
      .toLocaleLowerCase()
      .includes(needle);
  });
}

export function mergeBisPages(current: BisPage, incoming: BisPage): BisPage {
  if (incoming.flow_id !== current.flow_id) throw new Error("BIS pagination changed flow");
  if (
    incoming.artifact_sha256 !== current.artifact_sha256
    || incoming.serving_generated_at !== current.serving_generated_at
    || incoming.artifact_knowledge_time !== current.artifact_knowledge_time
    || incoming.complete_snapshot !== current.complete_snapshot
  ) {
    throw new Error("BIS pagination snapshot changed; reload the flow");
  }
  const seen = new Set(current.records.map((row) => row.logical_id));
  const duplicate = incoming.records.find((row) => seen.has(row.logical_id));
  if (duplicate) throw new Error("BIS pagination repeated a logical record");
  return {
    ...incoming,
    count: current.count + incoming.count,
    records: [...current.records, ...incoming.records],
  };
}

export function bisValue(row: BisRecord): string {
  if (!row.value_text) return "unavailable";
  const unit = row.dimension_labels.UNIT_MEASURE
    ?? row.dimensions.UNIT_MEASURE
    ?? row.attribute_labels.UNIT_MEASURE
    ?? row.attributes.UNIT_MEASURE;
  return unit ? `${row.value_text} ${unit}` : row.value_text;
}

export function bisDimensionPairs(row: BisRecord): Array<{
  code: string;
  value: string;
  label: string;
}> {
  return Object.entries(row.dimensions)
    .map(([code, value]) => ({
      code,
      value,
      label: row.dimension_labels[code] ?? value,
    }))
    .sort((left, right) => left.code.localeCompare(right.code));
}

export function bisAttributePairs(row: BisRecord): Array<{
  code: string;
  value: string;
  label: string;
}> {
  return Object.entries(row.attributes)
    .map(([code, value]) => ({
      code,
      value,
      label: row.attribute_labels[code] ?? value,
    }))
    .sort((left, right) => left.code.localeCompare(right.code));
}
