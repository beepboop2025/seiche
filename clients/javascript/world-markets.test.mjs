import assert from "node:assert/strict";
import test from "node:test";

import {
  contractReceipt,
  fetchWorldMarkets,
  SeicheClientError,
} from "./world-markets.mjs";

const payload = {
  schema: "seiche.world-markets.v1",
  selection: "sources",
  context_only: true,
  clocks: { boundary: "synthetic-offline-contract" },
  citation: { canonical_url: "https://seiche.info/world-markets" },
  scope: { coverage_claim: "curated_partial_non_exhaustive" },
};

test("accepts a bounded JSON response", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
  assert.deepEqual(await fetchWorldMarkets({ section: "sources" }), payload);
});

test("receipt records the effective caller-supplied limits", () => {
  const receipt = contractReceipt(payload, {
    timeoutMs: 1234,
    maxResponseBytes: 5678,
  });
  assert.deepEqual(receipt.client_limits, {
    timeout_ms: 1234,
    max_response_bytes: 5678,
    automatic_retries: 0,
  });
});

test("rejects an oversized chunked body while streaming", async () => {
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(12));
      controller.enqueue(new Uint8Array(12));
      controller.close();
    },
  }), { status: 200 });

  await assert.rejects(
    fetchWorldMarkets({ section: "sources", maxResponseBytes: 16 }),
    (error) => error instanceof SeicheClientError && /16-byte client limit/.test(error.message),
  );
});

test("timeout remains active while the response body is streaming", async () => {
  globalThis.fetch = async (_url, { signal }) => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array([123]));
      signal.addEventListener("abort", () => {
        controller.error(new DOMException("request aborted", "AbortError"));
      }, { once: true });
    },
  }), { status: 200 });

  await assert.rejects(
    fetchWorldMarkets({ section: "sources", timeoutMs: 20 }),
    (error) => error instanceof SeicheClientError && /request timed out/.test(error.message),
  );
});
