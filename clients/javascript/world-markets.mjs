#!/usr/bin/env node
/** Dependency-free Node 18+ example for Seiche's public REST contract. */

export const DEFAULT_BASE_URL = "https://api.seiche.info";
export const ALLOWED_SECTIONS = new Set([
  "summary",
  "money_markets",
  "forex",
  "capital_markets",
  "sources",
  "methodology",
  "all",
]);
export const DEFAULT_TIMEOUT_MS = 15_000;
export const DEFAULT_MAX_RESPONSE_BYTES = 2_000_000;

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
