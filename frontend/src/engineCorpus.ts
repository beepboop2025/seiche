export type EnginePublicationState =
  | "metadata_only"
  | "schema_only"
  | "preview_values"
  | "raw_download"
  | "restricted_metadata_only";

export interface EngineRights {
  acquisition_review: string;
  public_metadata: boolean;
  public_schema: boolean;
  public_preview_values: boolean;
  public_raw_download: boolean;
  publication_state: EnginePublicationState;
}

export interface StructuralHeader {
  column_count: number;
  columns_sample: string[];
  delimiter?: string;
}

export interface StructuralProfile {
  format: string;
  archive_entry_count?: number;
  archive_entries_sample?: string[];
  inner_table?: string;
  header?: StructuralHeader;
  inner_header?: StructuralHeader;
  boundary_magic?: string;
  magic?: string;
  top_level_type?: string;
  list_length?: number;
  keys_sample?: string[];
}

export interface NormalizedRecordsLink {
  relation: "normalized_observations";
  flow_id: string;
  href: string;
}

interface EngineObjectBase {
  artifact_id: string;
  dataset_id: string;
  detail_href: string;
  rights: EngineRights;
}

export interface AcquiredObject extends EngineObjectBase {
  collection_kind: "acquired_object";
  group: string;
  data_class: string;
  engines: string[];
  media_format: string;
  content_sha256: string;
  acquired_date: string;
  attempt_count: number;
  recovered: boolean;
  structural_profile_available?: boolean;
  license?: { name: string; url?: string };
  source?: { page: string };
  structural_profile?: StructuralProfile;
  normalized_records?: NormalizedRecordsLink;
}

export interface RestrictedCollection extends EngineObjectBase {
  collection_kind: "restricted_metadata_only";
  title?: string;
  publisher?: string;
  group?: string;
  data_class?: string;
  object_count?: number;
  row_count?: number;
  total_bytes?: number;
  event_from?: string;
  event_to?: string;
  formats?: string[];
  collection_time?: string;
  manifest_sha256?: string;
  notes?: string;
  forward_evidence_eligible?: boolean;
  source?: { page: string };
}

export type EngineDataset = AcquiredObject | RestrictedCollection;

export interface EngineIndexCounts {
  attempt_count: number;
  successful_attempt_count: number;
  failed_attempt_count: number;
  object_count: number;
  recovered_object_count: number;
  unresolved_object_count: number;
  published_object_count: number;
  withheld_object_count: number;
  restricted_collection_count: number;
  structurally_profiled_object_count: number;
  bis_linked_object_count: number;
}

export interface EngineDatasetPage {
  schema_version: string;
  release_id: string;
  generated_at: string;
  verified_at: string;
  index_artifact_id: string;
  index_sha256: string;
  count: number;
  total: number;
  next_cursor: string | null;
  filters: Record<string, string>;
  counts?: EngineIndexCounts;
  datasets: EngineDataset[];
}

export interface EngineDatasetDetail {
  schema_version: string;
  release_id: string;
  generated_at: string;
  verified_at: string;
  index_artifact_id: string;
  index_sha256: string;
  dataset: EngineDataset;
}

const SHA256 = /^[a-f0-9]{64}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const FLOW_ID = /^[A-Z][A-Z0-9_]{1,63}$/;
const EXPORT_SEGMENT = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const MEDIA_FORMATS = new Set(["csv", "json", "parquet", "pdf", "xlsx", "zip"]);
const RESTRICTED_ACQUISITION_REVIEWS = new Set([
  "acquired_internal_research_only",
  "not_acquired_restricted_recipe",
]);
const PUBLICATION_STATES = new Set<EnginePublicationState>([
  "metadata_only",
  "schema_only",
  "preview_values",
  "raw_download",
  "restricted_metadata_only",
]);

function objectValue(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Engine corpus ${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Engine corpus ${field} is missing`);
  }
  return value;
}

function optionalText(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function nonnegativeInteger(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new Error(`Engine corpus ${field} is invalid`);
  }
  return value as number;
}

function stringList(value: unknown, field: string, maximum = 64): string[] {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new Error(`Engine corpus ${field} is invalid`);
  }
  const result = value.map((item) => requiredText(item, field));
  if (new Set(result).size !== result.length) {
    throw new Error(`Engine corpus ${field} repeats a value`);
  }
  return result;
}

function optionalInteger(value: unknown, field: string): number | undefined {
  return value === undefined ? undefined : nonnegativeInteger(value, field);
}

function optionalDate(value: unknown, field: string): string | undefined {
  const result = optionalText(value);
  if (result !== undefined && !DATE.test(result)) {
    throw new Error(`Engine corpus ${field} is invalid`);
  }
  return result;
}

function hash(value: unknown, field: string): string {
  const result = requiredText(value, field);
  if (!SHA256.test(result)) throw new Error(`Engine corpus ${field} is invalid`);
  return result;
}

function normalizeRights(value: unknown, restricted: boolean): EngineRights {
  const row = objectValue(value, "rights");
  const publicMetadata = row.public_metadata;
  const publicSchema = row.public_schema;
  const publicPreview = row.public_preview_values;
  const publicRaw = row.public_raw_download;
  if (
    typeof publicMetadata !== "boolean"
    || typeof publicSchema !== "boolean"
    || typeof publicPreview !== "boolean"
    || typeof publicRaw !== "boolean"
  ) {
    throw new Error("Engine corpus rights booleans are invalid");
  }
  if (!publicMetadata || (publicSchema && !publicMetadata) || (publicPreview && !publicSchema) || (publicRaw && !publicPreview)) {
    throw new Error("Engine corpus rights lattice is invalid");
  }
  const publicationState = requiredText(row.publication_state, "publication_state");
  if (!PUBLICATION_STATES.has(publicationState as EnginePublicationState)) {
    throw new Error("Engine corpus publication_state is invalid");
  }
  const expected = publicRaw
    ? "raw_download"
    : publicPreview
      ? "preview_values"
      : publicSchema
        ? "schema_only"
        : restricted
          ? "restricted_metadata_only"
          : "metadata_only";
  if (publicationState !== expected) {
    throw new Error("Engine corpus rights state contradicts its permissions");
  }
  if (restricted && (publicSchema || publicPreview || publicRaw || publicationState !== "restricted_metadata_only")) {
    throw new Error("Engine corpus restricted collection exceeds metadata-only rights");
  }
  if (!restricted && (publicPreview || publicRaw)) {
    throw new Error("Engine corpus acquired object exceeds structural-only rights");
  }
  return {
    acquisition_review: requiredText(row.acquisition_review, "acquisition_review"),
    public_metadata: publicMetadata,
    public_schema: publicSchema,
    public_preview_values: publicPreview,
    public_raw_download: publicRaw,
    publication_state: publicationState as EnginePublicationState,
  };
}

export function safeProvenanceUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length === 0) return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:"
      || !url.hostname
      || url.username
      || url.password
      || url.search
      || url.hash
    ) return null;
    return url.toString();
  } catch {
    return null;
  }
}

export function safeCorpusHref(value: unknown): string | null {
  if (typeof value !== "string" || !value.startsWith("/v1/")) return null;
  try {
    const base = "https://corpus.invalid";
    const url = new URL(value, base);
    if (url.origin !== base || url.hash || url.pathname !== "/v1/bis/records") return null;
    const keys = [...url.searchParams.keys()];
    if (keys.length !== 1 || keys[0] !== "flow_id") return null;
    const flowId = url.searchParams.get("flow_id");
    return flowId && FLOW_ID.test(flowId) ? `${url.pathname}?${url.searchParams.toString()}` : null;
  } catch {
    return null;
  }
}

export function safeDatasetDetailHref(value: unknown): string | null {
  if (typeof value !== "string" || !value.startsWith("/")) return null;
  try {
    const base = "https://corpus.invalid";
    const url = new URL(value, base);
    const marker = "/v1/datasets/";
    const markerIndex = url.pathname.indexOf(marker);
    const prefix = markerIndex >= 0 ? url.pathname.slice(0, markerIndex) : "";
    const encodedId = markerIndex >= 0 ? url.pathname.slice(markerIndex + marker.length) : "";
    if (
      url.origin !== base
      || url.search
      || url.hash
      || !["", "/api/v2/corpus"].includes(prefix)
      || !encodedId
      || encodedId.includes("/")
      || decodeURIComponent(encodedId).length === 0
    ) return null;
    return `${marker}${encodedId}`;
  } catch {
    return null;
  }
}

export function safeCorpusExportHref(value: unknown): string | null {
  if (typeof value !== "string" || !value.startsWith("/")) return null;
  try {
    const base = "https://corpus.invalid";
    const url = new URL(value, base);
    const marker = "/v1/seiche/exports/";
    const markerIndex = url.pathname.indexOf(marker);
    const prefix = markerIndex >= 0 ? url.pathname.slice(0, markerIndex) : "";
    const encodedSegments = markerIndex >= 0
      ? url.pathname.slice(markerIndex + marker.length).split("/")
      : [];
    const segments = encodedSegments.map((segment) => decodeURIComponent(segment));
    if (
      url.origin !== base
      || url.search
      || url.hash
      || !["", "/api/v2/corpus"].includes(prefix)
      || segments.length !== 2
      || segments.some((segment) => !EXPORT_SEGMENT.test(segment))
    ) return null;
    return `${marker}${segments.map(encodeURIComponent).join("/")}`;
  } catch {
    return null;
  }
}

function normalizeHeader(value: unknown, field: string): StructuralHeader {
  const row = objectValue(value, field);
  return {
    column_count: nonnegativeInteger(row.column_count, `${field} column_count`),
    columns_sample: stringList(row.columns_sample, `${field} columns_sample`, 20),
    delimiter: optionalText(row.delimiter),
  };
}

function normalizeStructuralProfile(value: unknown): StructuralProfile {
  const row = objectValue(value, "structural_profile");
  const profile: StructuralProfile = {
    format: requiredText(row.format, "structural format"),
  };
  const archiveEntryCount = optionalInteger(row.archive_entry_count, "archive_entry_count");
  if (archiveEntryCount !== undefined) profile.archive_entry_count = archiveEntryCount;
  if (row.archive_entries_sample !== undefined) {
    profile.archive_entries_sample = stringList(row.archive_entries_sample, "archive entries", 20);
  }
  const innerTable = optionalText(row.inner_table);
  if (innerTable !== undefined) profile.inner_table = innerTable;
  if (row.header !== undefined) profile.header = normalizeHeader(row.header, "header");
  if (row.inner_header !== undefined) profile.inner_header = normalizeHeader(row.inner_header, "inner_header");
  for (const field of ["boundary_magic", "magic", "top_level_type"] as const) {
    const item = optionalText(row[field]);
    if (item !== undefined) profile[field] = item;
  }
  const listLength = optionalInteger(row.list_length, "list_length");
  if (listLength !== undefined) profile.list_length = listLength;
  if (row.keys_sample !== undefined) profile.keys_sample = stringList(row.keys_sample, "keys_sample", 20);
  return profile;
}

function normalizeSource(value: unknown): { page: string } | undefined {
  if (value === undefined) return undefined;
  const page = safeProvenanceUrl(objectValue(value, "source").page);
  return page ? { page } : undefined;
}

function expectedBisFlowId(group: string, datasetId: string): string | null {
  if (group !== "bis-bulk") return null;
  const match = /^bis-(ws_[a-z0-9_]+)_csv_flat$/.exec(datasetId);
  return match ? match[1].toUpperCase() : null;
}

function normalizeAcquired(row: Record<string, unknown>): AcquiredObject {
  const rights = normalizeRights(row.rights, false);
  const mediaFormat = requiredText(row.media_format, "media_format");
  if (!MEDIA_FORMATS.has(mediaFormat)) throw new Error("Engine corpus media_format is invalid");
  const engines = stringList(row.engines, "engines");
  const detailHref = safeDatasetDetailHref(row.detail_href);
  if (!detailHref) throw new Error("Engine corpus detail_href is invalid");
  const result: AcquiredObject = {
    artifact_id: requiredText(row.artifact_id, "artifact_id"),
    dataset_id: requiredText(row.dataset_id, "dataset_id"),
    detail_href: detailHref,
    collection_kind: "acquired_object",
    group: requiredText(row.group, "group"),
    data_class: requiredText(row.data_class, "data_class"),
    engines,
    media_format: mediaFormat,
    content_sha256: hash(row.content_sha256, "content_sha256"),
    acquired_date: requiredText(row.acquired_date, "acquired_date"),
    attempt_count: nonnegativeInteger(row.attempt_count, "attempt_count"),
    recovered: false,
    rights,
  };
  if (typeof row.recovered !== "boolean") {
    throw new Error("Engine corpus recovered state is invalid");
  }
  result.recovered = row.recovered;
  if (!DATE.test(result.acquired_date) || result.attempt_count < 1) {
    throw new Error("Engine corpus acquisition identity is invalid");
  }
  if (row.license !== undefined) {
    const license = objectValue(row.license, "license");
    const url = safeProvenanceUrl(license.url);
    result.license = { name: requiredText(license.name, "license name"), ...(url ? { url } : {}) };
  }
  const source = normalizeSource(row.source);
  if (source) result.source = source;
  if (row.structural_profile !== undefined) {
    if (!rights.public_schema) throw new Error("Engine corpus profile exceeds publication rights");
    result.structural_profile = normalizeStructuralProfile(row.structural_profile);
  }
  if (row.structural_profile_available !== undefined) {
    if (typeof row.structural_profile_available !== "boolean") {
      throw new Error("Engine corpus structural_profile_available is invalid");
    }
    result.structural_profile_available = row.structural_profile_available;
  }
  if (result.structural_profile && result.structural_profile_available === false) {
    throw new Error("Engine corpus structural profile availability is contradictory");
  }
  const expectedFlowId = expectedBisFlowId(result.group, result.dataset_id);
  if (row.normalized_records !== undefined) {
    const link = objectValue(row.normalized_records, "normalized_records");
    const flowId = requiredText(link.flow_id, "flow_id");
    const href = safeCorpusHref(link.href);
    if (
      link.relation !== "normalized_observations"
      || !FLOW_ID.test(flowId)
      || flowId !== expectedFlowId
      || href !== `/v1/bis/records?flow_id=${flowId}`
    ) {
      throw new Error("Engine corpus normalized-record link is invalid");
    }
    result.normalized_records = { relation: "normalized_observations", flow_id: flowId, href };
  } else if (expectedFlowId !== null) {
    throw new Error("Engine corpus normalized-record link is missing");
  }
  return result;
}

function normalizeRestricted(row: Record<string, unknown>): RestrictedCollection {
  const rights = normalizeRights(row.rights, true);
  if (!RESTRICTED_ACQUISITION_REVIEWS.has(rights.acquisition_review)) {
    throw new Error("Engine corpus restricted acquisition review is invalid");
  }
  const detailHref = safeDatasetDetailHref(row.detail_href);
  if (!detailHref) throw new Error("Engine corpus detail_href is invalid");
  const result: RestrictedCollection = {
    artifact_id: requiredText(row.artifact_id, "artifact_id"),
    dataset_id: requiredText(row.dataset_id, "dataset_id"),
    detail_href: detailHref,
    collection_kind: "restricted_metadata_only",
    rights,
  };
  for (const field of ["title", "publisher", "group", "data_class", "collection_time", "notes"] as const) {
    const item = optionalText(row[field]);
    if (item !== undefined) result[field] = item;
  }
  for (const field of ["object_count", "row_count", "total_bytes"] as const) {
    const item = optionalInteger(row[field], field);
    if (item !== undefined) result[field] = item;
  }
  result.event_from = optionalDate(row.event_from, "event_from");
  result.event_to = optionalDate(row.event_to, "event_to");
  if (result.event_from && result.event_to && result.event_from > result.event_to) {
    throw new Error("Engine corpus event range is reversed");
  }
  if (row.formats !== undefined) result.formats = stringList(row.formats, "formats", 16);
  if (row.manifest_sha256 !== undefined) result.manifest_sha256 = hash(row.manifest_sha256, "manifest_sha256");
  if (row.forward_evidence_eligible !== undefined) {
    if (typeof row.forward_evidence_eligible !== "boolean") {
      throw new Error("Engine corpus forward_evidence_eligible is invalid");
    }
    result.forward_evidence_eligible = row.forward_evidence_eligible;
  }
  const source = normalizeSource(row.source);
  if (source) result.source = source;
  return result;
}

export function normalizeEngineDataset(value: unknown): EngineDataset {
  const row = objectValue(value, "dataset");
  if (row.collection_kind === "acquired_object") return normalizeAcquired(row);
  if (row.collection_kind === "restricted_metadata_only") return normalizeRestricted(row);
  throw new Error("Engine corpus collection_kind is invalid");
}

function normalizeCounts(value: unknown): EngineIndexCounts | undefined {
  if (value === undefined) return undefined;
  const row = objectValue(value, "counts");
  const fields: Array<keyof EngineIndexCounts> = [
    "attempt_count", "successful_attempt_count", "failed_attempt_count", "object_count",
    "recovered_object_count", "unresolved_object_count", "published_object_count",
    "withheld_object_count", "restricted_collection_count", "structurally_profiled_object_count",
    "bis_linked_object_count",
  ];
  return Object.fromEntries(fields.map((field) => [field, nonnegativeInteger(row[field], field)])) as unknown as EngineIndexCounts;
}

function normalizeEnvelope(row: Record<string, unknown>) {
  const indexArtifactId = requiredText(row.index_artifact_id ?? row.artifact_id, "index_artifact_id");
  return {
    schema_version: requiredText(row.schema_version, "schema_version"),
    release_id: requiredText(row.release_id, "release_id"),
    generated_at: requiredText(row.generated_at, "generated_at"),
    verified_at: requiredText(row.verified_at, "verified_at"),
    index_artifact_id: indexArtifactId,
    index_sha256: hash(row.index_sha256, "index_sha256"),
  };
}

export function normalizeEnginePage(value: unknown): EngineDatasetPage {
  const row = objectValue(value, "dataset page");
  if (!Array.isArray(row.datasets)) throw new Error("Engine corpus datasets are missing");
  const datasets = row.datasets.map(normalizeEngineDataset);
  if (new Set(datasets.map((item) => item.artifact_id)).size !== datasets.length) {
    throw new Error("Engine corpus page repeats an artifact");
  }
  const count = nonnegativeInteger(row.count, "count");
  const total = nonnegativeInteger(row.total, "total");
  if (count !== datasets.length || total < count) throw new Error("Engine corpus page counts are invalid");
  const filters = objectValue(row.filters ?? {}, "filters");
  if (Object.values(filters).some((item) => typeof item !== "string")) {
    throw new Error("Engine corpus filters are invalid");
  }
  return {
    ...normalizeEnvelope(row),
    count,
    total,
    next_cursor: optionalText(row.next_cursor) ?? null,
    filters: filters as Record<string, string>,
    counts: normalizeCounts(row.counts),
    datasets,
  };
}

export function normalizeEngineDetail(value: unknown): EngineDatasetDetail {
  const row = objectValue(value, "dataset detail");
  const dataset = normalizeEngineDataset(row.dataset);
  if (
    dataset.collection_kind === "acquired_object"
    && (
      typeof dataset.structural_profile_available !== "boolean"
      || dataset.structural_profile_available !== dataset.rights.public_schema
      || (dataset.structural_profile !== undefined) !== dataset.rights.public_schema
    )
  ) {
    throw new Error("Engine corpus detail structural profile contradicts its rights");
  }
  return { ...normalizeEnvelope(row), dataset };
}

function sameIndex(left: EngineDatasetPage, right: EngineDatasetPage | EngineDatasetDetail): boolean {
  return left.schema_version === right.schema_version
    && left.release_id === right.release_id
    && left.generated_at === right.generated_at
    && left.index_artifact_id === right.index_artifact_id
    && left.index_sha256 === right.index_sha256
    && left.verified_at === right.verified_at;
}

function sameStringMap(left: Record<string, string>, right: Record<string, string>): boolean {
  const leftEntries = Object.entries(left).sort(([a], [b]) => a.localeCompare(b));
  const rightEntries = Object.entries(right).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify(leftEntries) === JSON.stringify(rightEntries);
}

export function mergeEnginePages(current: EngineDatasetPage, incoming: EngineDatasetPage): EngineDatasetPage {
  if (!sameIndex(current, incoming)) throw new Error("Engine corpus index changed; reload the registry");
  if (
    current.total !== incoming.total
    || !sameStringMap(current.filters, incoming.filters)
    || JSON.stringify(current.counts ?? null) !== JSON.stringify(incoming.counts ?? null)
  ) {
    throw new Error("Engine corpus pagination contract changed; reload the registry");
  }
  const seen = new Set(current.datasets.map((row) => row.artifact_id));
  if (incoming.datasets.some((row) => seen.has(row.artifact_id))) {
    throw new Error("Engine corpus pagination repeated an artifact");
  }
  return {
    ...incoming,
    count: current.datasets.length + incoming.datasets.length,
    datasets: [...current.datasets, ...incoming.datasets],
  };
}

export function acceptEngineDetail(
  page: EngineDatasetPage,
  detail: EngineDatasetDetail,
  expectedArtifactId: string,
): EngineDatasetDetail {
  if (!sameIndex(page, detail)) throw new Error("Engine corpus detail belongs to another index");
  if (detail.dataset.artifact_id !== expectedArtifactId) {
    throw new Error("Engine corpus detail belongs to another artifact");
  }
  return detail;
}

export function filterEngineDatasets(rows: readonly EngineDataset[], query: string): EngineDataset[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [...rows];
  return rows.filter((row) => {
    const acquired = row.collection_kind === "acquired_object"
      ? `${row.group} ${row.data_class} ${row.engines.join(" ")} ${row.media_format}`
      : `${row.title ?? ""} ${row.publisher ?? ""} ${row.group ?? ""} ${row.data_class ?? ""}`;
    return `${row.dataset_id} ${row.rights.publication_state} ${acquired}`
      .toLocaleLowerCase()
      .includes(needle);
  });
}
