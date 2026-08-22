#!/usr/bin/env node
/** Dependency-free Node 18+ example for Seiche's public REST contract. */

export const DEFAULT_BASE_URL = "https://api.seiche.info";
export const ALLOWED_SECTIONS = new Set([
  "summary",
  "money_markets",
  "forex",
  "capital_markets",
  "china_macro",
  "sources",
  "methodology",
  "all",
]);
export const DEFAULT_TIMEOUT_MS = 15_000;
export const DEFAULT_MAX_RESPONSE_BYTES = 2_000_000;
const CORE_CLOCK_DOMAINS = ["money_markets", "forex", "capital_markets"];
const SECTION_CONTENT_KEYS = new Set([
  "summary",
  "money_markets",
  "forex",
  "capital_markets",
  "china_macro",
  "sources",
  "methodology",
]);
const EXPECTED_SECTION_CONTENT = {
  summary: new Set(["summary"]),
  money_markets: new Set(["money_markets"]),
  forex: new Set(["forex"]),
  capital_markets: new Set(["capital_markets"]),
  china_macro: new Set(["china_macro"]),
  sources: new Set(["sources"]),
  methodology: new Set(["methodology"]),
  all: new Set([
    "money_markets",
    "forex",
    "capital_markets",
    "china_macro",
    "sources",
    "methodology",
  ]),
};
const CHINA_MACRO_SERIES_IDS = [
  "CN.NBS.CPI_INDEX",
  "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY",
  "CN.NBS.MANUFACTURING_PMI",
  "CN.NBS.PPI_INDEX",
];
const CHINA_COMMON_KEYS = new Set([
  "status",
  "evidence_status",
  "as_of",
  "schema",
  "available",
  "dataset",
  "publisher",
  "source_url",
  "context_only",
  "scoring_eligible",
  "cn_cny_gauge_eligible",
  "values_published",
  "raw_evidence_included",
  "history_included",
  "public_distribution",
  "rights_status",
  "terms_url",
  "series_catalog",
  "series_count",
  "reading",
  "boundaries",
]);
const CHINA_AVAILABLE_KEYS = new Set([
  ...CHINA_COMMON_KEYS,
  "source_registry_ids",
  "revision_id",
  "predecessor_revision_id",
  "knowledge_time",
  "provenance",
  "attestation",
]);
const CHINA_UNAVAILABLE_KEYS = new Set([...CHINA_COMMON_KEYS, "reason_code"]);
const CHINA_SERIES_KEYS = new Set([
  "series_id",
  "catalogid",
  "catalog_label",
  "row_id",
  "i",
  "ek",
  "ek_dp",
  "dp",
  "dp_name",
  "label",
  "reference_release_url",
  "release_url",
  "source_unit_label_exact",
  "source_unit_semantically_authoritative",
  "semantic_contract",
  "value_publication",
]);
const CHINA_SEMANTIC_KEYS = new Set([
  "value_kind",
  "canonical_unit",
  "comparison_base",
  "transform",
  "threshold",
]);
const CHINA_PROVENANCE_KEYS = new Set([
  "manifest_sha256",
  "owner_attestation",
]);
const CHINA_ATTESTATION_KEYS = new Set([
  "schema",
  "algorithm",
  "domain",
  "export_id",
  "signer_key_id",
  "signed_at",
  "manifest_sha256",
  "public_projection_sha256",
  "signature",
]);
const HEX_64_RE = /^[0-9a-f]{64}$/;
const HEX_128_RE = /^[0-9a-f]{128}$/;
const EXPORT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export class SeicheClientError extends Error {}

async function readLimitedBody(response, maxResponseBytes) {
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null) {
    const declaredLength = Number(contentLength);
    if (!Number.isSafeInteger(declaredLength) || declaredLength < 0) {
      throw new SeicheClientError("Seiche returned an invalid Content-Length header");
    }
    if (declaredLength > maxResponseBytes) {
      throw new SeicheClientError(`response exceeded the ${maxResponseBytes}-byte client limit`);
    }
  }

  if (!response.body) {
    throw new SeicheClientError("Seiche returned a response without a readable body");
  }

  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxResponseBytes) {
        await reader.cancel("response byte limit exceeded");
        throw new SeicheClientError(`response exceeded the ${maxResponseBytes}-byte client limit`);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

export async function fetchWorldMarkets({
  section = "sources",
  baseUrl = DEFAULT_BASE_URL,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  maxResponseBytes = DEFAULT_MAX_RESPONSE_BYTES,
} = {}) {
  if (!ALLOWED_SECTIONS.has(section)) {
    throw new TypeError(`section must be one of: ${[...ALLOWED_SECTIONS].join(", ")}`);
  }
  if (!(timeoutMs > 0) || !(maxResponseBytes > 0)) {
    throw new TypeError("timeoutMs and maxResponseBytes must be positive");
  }

  const url = new URL("/api/v2/world-markets", `${baseUrl.replace(/\/$/, "")}/`);
  url.searchParams.set("section", section);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let bytes;
  try {
    const response = await fetch(url, {
      headers: {
        Accept: "application/json",
        "User-Agent": "seiche-public-javascript-example/1.0 (+https://seiche.info/developers)",
      },
      signal: controller.signal,
    });
    if (!response.ok) {
      const retryAfter = response.headers.get("retry-after");
      throw new SeicheClientError(
        `Seiche returned HTTP ${response.status}${retryAfter ? `; retry-after=${retryAfter}` : ""}`,
      );
    }
    bytes = await readLimitedBody(response, maxResponseBytes);
  } catch (error) {
    if (error instanceof SeicheClientError) throw error;
    const message = error?.name === "AbortError" ? "request timed out" : String(error);
    throw new SeicheClientError(`Seiche request failed: ${message}`);
  } finally {
    clearTimeout(timer);
  }

  let payload;
  try {
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch (error) {
    throw new SeicheClientError(`Seiche returned invalid UTF-8 JSON: ${error}`);
  }
  validateContract(payload, section);
  return payload;
}

function validateContract(payload, section) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new SeicheClientError("world-markets response must be a JSON object");
  }
  if (payload.schema !== "seiche.world-markets.v1" || payload.selection !== section) {
    throw new SeicheClientError("unexpected world-markets schema or selection");
  }
  if (payload.context_only !== true || !payload.clocks?.boundary) {
    throw new SeicheClientError("context-only or clock boundary is missing");
  }
  if (!payload.citation?.canonical_url) {
    throw new SeicheClientError("citation block is missing");
  }
  if (payload.scope?.coverage_claim !== "curated_partial_non_exhaustive") {
    throw new SeicheClientError("partial-coverage boundary is missing");
  }
  validateSelectorShape(payload, section);
  validateClockContract(payload, section, payload.clocks, payload.citation);
  if (section === "china_macro" || section === "all") {
    validateChinaMacro(payload.china_macro);
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value, expected) {
  if (!isObject(value)) return false;
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function requireExactKeys(value, expected, label) {
  if (!hasExactKeys(value, expected)) {
    throw new SeicheClientError(`${label} fields do not match schema v1`);
  }
  return value;
}

function validateSelectorShape(payload, section) {
  const present = new Set(Object.keys(payload).filter((key) => SECTION_CONTENT_KEYS.has(key)));
  if (!hasSameMembers(present, EXPECTED_SECTION_CONTENT[section])) {
    throw new SeicheClientError("response content does not match the requested section");
  }
}

function hasSameMembers(actual, expected) {
  return actual.size === expected.size && [...actual].every((value) => expected.has(value));
}

function validateClockContract(payload, section, clocks, citation) {
  const domains = clocks.domains;
  if (!isObject(domains) || !hasSameMembers(new Set(Object.keys(domains)), new Set(CORE_CLOCK_DOMAINS))) {
    throw new SeicheClientError("world clock domains must contain only the core markets");
  }
  const requiredPaths = [
    [payload, "generated_at"],
    [payload, "as_of"],
    [clocks, "snapshot_generated_at"],
    [clocks, "latest_domain_as_of"],
    [clocks, "selected_evidence_as_of"],
    [citation, "generated_at"],
    [citation, "evidence_as_of"],
  ];
  if (requiredPaths.some(([object, key]) => !Object.hasOwn(object, key))) {
    throw new SeicheClientError("required world clock paths are missing");
  }
  if (
    !Array.isArray(clocks.excluded_from_observation_clocks)
    || clocks.excluded_from_observation_clocks.length !== 1
    || clocks.excluded_from_observation_clocks[0] !== "china_macro.knowledge_time"
  ) {
    throw new SeicheClientError("China knowledge time exclusion is missing");
  }
  const values = Object.values(domains);
  if (values.some((value) => value !== null && typeof value !== "string")) {
    throw new SeicheClientError("world clock domain values must be strings or null");
  }
  const nonNull = values.filter((value) => value !== null);
  const latest = nonNull.length > 0 ? nonNull.reduce((left, right) => (left > right ? left : right)) : null;
  if (clocks.latest_domain_as_of !== latest) {
    throw new SeicheClientError("latest world clock is inconsistent with core domains");
  }
  let selected = null;
  if (CORE_CLOCK_DOMAINS.includes(section)) {
    selected = domains[section];
  } else if (section === "summary" || section === "all") {
    selected = latest;
  }
  if (
    clocks.selected_evidence_as_of !== selected
    || payload.as_of !== selected
    || citation.evidence_as_of !== selected
  ) {
    throw new SeicheClientError("selected evidence clock is inconsistent");
  }
  if (
    clocks.snapshot_generated_at !== payload.generated_at
    || citation.generated_at !== payload.generated_at
  ) {
    throw new SeicheClientError("snapshot and citation clocks are inconsistent");
  }
  if (section === "china_macro" && payload.generated_at !== null) {
    throw new SeicheClientError("standalone China metadata cannot borrow a snapshot clock");
  }
}

function canonicalUtcInstant(value) {
  if (typeof value !== "string") return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{6}))?Z$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second, fraction] = match;
  const date = new Date(0);
  date.setUTCFullYear(Number(year), Number(month) - 1, Number(day));
  date.setUTCHours(Number(hour), Number(minute), Number(second), 0);
  if (Number.isNaN(date.getTime())) return null;
  const canonicalBase = `${String(date.getUTCFullYear()).padStart(4, "0")}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}T${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}:${String(date.getUTCSeconds()).padStart(2, "0")}`;
  if (value !== `${canonicalBase}${fraction ? `.${fraction}` : ""}Z`) return null;
  return BigInt(date.getTime()) * 1000n + BigInt(fraction || "0");
}

function validateChinaMacro(china) {
  if (!isObject(china)) {
    throw new SeicheClientError("China macro projection must be a JSON object");
  }
  if (typeof china.available !== "boolean") {
    throw new SeicheClientError("China macro availability state is inconsistent");
  }
  requireExactKeys(
    china,
    china.available ? CHINA_AVAILABLE_KEYS : CHINA_UNAVAILABLE_KEYS,
    "China macro",
  );
  const knowledgeInstant = canonicalUtcInstant(china.knowledge_time);
  if (
    china.schema !== "seiche.nbs-macro-context.v1"
    || china.dataset !== "CN.NBS.MACRO_CONTEXT"
    || china.publisher !== "National Bureau of Statistics of China"
    || china.source_url !== "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData"
    || china.terms_url !== "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html"
  ) {
    throw new SeicheClientError("unexpected China macro identity or source contract");
  }
  const requiredFalse = [
    "cn_cny_gauge_eligible",
    "history_included",
    "raw_evidence_included",
    "scoring_eligible",
    "values_published",
  ];
  if (china.context_only !== true || requiredFalse.some((field) => china[field] !== false)) {
    throw new SeicheClientError("China macro metadata-only boundary is missing");
  }
  if (
    china.as_of !== null
    || china.public_distribution !== "metadata_only"
    || china.rights_status !== "redistribution_review_required"
  ) {
    throw new SeicheClientError("China macro rights or observation boundary is invalid");
  }
  if (
    !Array.isArray(china.series_catalog)
    || china.series_count !== 4
    || china.series_catalog.length !== 4
  ) {
    throw new SeicheClientError("China macro series catalog is malformed");
  }
  const observedIds = [];
  for (const row of china.series_catalog) {
    requireExactKeys(row, CHINA_SERIES_KEYS, "China macro series");
    const semantic = requireExactKeys(
      row.semantic_contract,
      CHINA_SEMANTIC_KEYS,
      "China macro semantic contract",
    );
    if (Object.values(semantic).some((value) => value !== null && typeof value !== "string")) {
      throw new SeicheClientError("China macro semantic metadata is malformed");
    }
    const strings = [
      "series_id",
      "catalogid",
      "catalog_label",
      "row_id",
      "i",
      "ek",
      "ek_dp",
      "dp",
      "label",
      "reference_release_url",
      "release_url",
    ];
    if (
      strings.some((field) => typeof row[field] !== "string")
      || ["dp_name", "source_unit_label_exact"].some(
        (field) => row[field] !== null && typeof row[field] !== "string",
      )
    ) {
      throw new SeicheClientError("China macro series metadata is malformed");
    }
    if (
      typeof row.source_unit_semantically_authoritative !== "boolean"
      || row.value_publication !== "withheld_pending_rights_review"
    ) {
      throw new SeicheClientError("China macro series publication gate is invalid");
    }
    observedIds.push(row.series_id);
  }
  if (
    observedIds.length !== CHINA_MACRO_SERIES_IDS.length
    || observedIds.some((value, index) => value !== CHINA_MACRO_SERIES_IDS[index])
  ) {
    throw new SeicheClientError("China macro series identities or order drifted");
  }
  if (
    !Array.isArray(china.boundaries)
    || china.boundaries.length !== 3
    || china.boundaries.some((item) => typeof item !== "string" || item.length === 0)
    || typeof china.reading !== "string"
  ) {
    throw new SeicheClientError("China macro public boundaries are malformed");
  }
  if (
    (
      china.available
      && (china.status !== "restricted" || china.evidence_status !== "restricted")
    )
    || (
      !china.available
      && (china.status !== "structural" || china.evidence_status !== "unavailable")
    )
  ) {
    throw new SeicheClientError("China macro availability state is inconsistent");
  }
  if (!china.available) {
    if (china.reason_code !== "signed_owner_export_required") {
      throw new SeicheClientError("China macro unavailable reason is invalid");
    }
    return;
  }
  if (
    typeof china.revision_id !== "string"
    || !EXPORT_ID_RE.test(china.revision_id)
    || (
      china.predecessor_revision_id !== null
      && (
        typeof china.predecessor_revision_id !== "string"
        || !EXPORT_ID_RE.test(china.predecessor_revision_id)
      )
    )
    || knowledgeInstant === null
    || !Array.isArray(china.source_registry_ids)
    || china.source_registry_ids.length !== 2
    || china.source_registry_ids[0] !== "nbs_monthly_data_browser"
    || china.source_registry_ids[1] !== "nbs_terms_of_service"
  ) {
    throw new SeicheClientError("available China macro revision metadata is malformed");
  }
  const provenance = requireExactKeys(
    china.provenance,
    CHINA_PROVENANCE_KEYS,
    "China macro provenance",
  );
  if (
    !HEX_64_RE.test(provenance.manifest_sha256)
    || provenance.owner_attestation !== "ed25519"
  ) {
    throw new SeicheClientError("China macro provenance is malformed");
  }
  const attestation = requireExactKeys(
    china.attestation,
    CHINA_ATTESTATION_KEYS,
    "China macro attestation",
  );
  const signedInstant = canonicalUtcInstant(attestation.signed_at);
  if (
    attestation.schema !== "seiche.nbs-owner-export-signature.v1"
    || attestation.algorithm !== "ed25519"
    || attestation.domain !== "seiche-nbs-owner-export-v1"
    || attestation.export_id !== china.revision_id
    || attestation.manifest_sha256 !== provenance.manifest_sha256
    || !HEX_64_RE.test(attestation.signer_key_id)
    || !HEX_64_RE.test(attestation.public_projection_sha256)
    || !HEX_128_RE.test(attestation.signature)
    || signedInstant === null
    || signedInstant < knowledgeInstant
  ) {
    throw new SeicheClientError("China macro attestation is malformed");
  }
}

export function contractReceipt(payload, {
  timeoutMs = DEFAULT_TIMEOUT_MS,
  maxResponseBytes = DEFAULT_MAX_RESPONSE_BYTES,
} = {}) {
  if (!(timeoutMs > 0) || !(maxResponseBytes > 0)) {
    throw new TypeError("timeoutMs and maxResponseBytes must be positive");
  }
  return {
    schema: payload.schema,
    selection: payload.selection,
    status: payload.status,
    clocks: payload.clocks,
    citation: payload.citation,
    scope: payload.scope,
    client_limits: {
      timeout_ms: timeoutMs,
      max_response_bytes: maxResponseBytes,
      automatic_retries: 0,
    },
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const payload = await fetchWorldMarkets();
  console.log(JSON.stringify(contractReceipt(payload), null, 2));
}
