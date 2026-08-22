import assert from "node:assert/strict";
import test from "node:test";

import {
  contractReceipt,
  fetchWorldMarkets,
  SeicheClientError,
} from "./world-markets.mjs";

const SERIES_IDS = [
  "CN.NBS.CPI_INDEX",
  "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY",
  "CN.NBS.MANUFACTURING_PMI",
  "CN.NBS.PPI_INDEX",
];

function series(seriesId) {
  return {
    series_id: seriesId,
    catalogid: "catalog-id",
    catalog_label: "Catalog label",
    row_id: "row-id",
    i: "indicator-id",
    ek: "export-key",
    ek_dp: "export-key-dimension",
    dp: "1",
    dp_name: "dimension",
    label: "Series label",
    reference_release_url: "https://www.stats.gov.cn/english/PressRelease/202608/t20260810_1965018.html",
    release_url: "https://www.stats.gov.cn/english/PressRelease/202608/t20260810_1965018.html",
    source_unit_label_exact: "%",
    source_unit_semantically_authoritative: true,
    semantic_contract: {
      value_kind: "index_level",
      canonical_unit: "index_points",
      comparison_base: null,
      transform: null,
      threshold: null,
    },
    value_publication: "withheld_pending_rights_review",
  };
}

function chinaMacro({ available = true } = {}) {
  const common = {
    schema: "seiche.nbs-macro-context.v1",
    dataset: "CN.NBS.MACRO_CONTEXT",
    publisher: "National Bureau of Statistics of China",
    source_url: "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData",
    terms_url: "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html",
    status: available ? "restricted" : "structural",
    evidence_status: available ? "restricted" : "unavailable",
    available,
    as_of: null,
    context_only: true,
    scoring_eligible: false,
    cn_cny_gauge_eligible: false,
    values_published: false,
    raw_evidence_included: false,
    history_included: false,
    public_distribution: "metadata_only",
    rights_status: "redistribution_review_required",
    series_catalog: SERIES_IDS.map(series),
    series_count: 4,
    reading: "Metadata-only China macro context.",
    boundaries: ["owner", "values", "scoring"],
  };
  if (!available) {
    return { ...common, reason_code: "signed_owner_export_required" };
  }
  return {
    ...common,
    revision_id: "nbs-2026-07-r1",
    predecessor_revision_id: null,
    knowledge_time: "2026-08-10T02:00:00Z",
    source_registry_ids: ["nbs_monthly_data_browser", "nbs_terms_of_service"],
    provenance: {
      manifest_sha256: "a".repeat(64),
      owner_attestation: "ed25519",
    },
    attestation: {
      schema: "seiche.nbs-owner-export-signature.v1",
      algorithm: "ed25519",
      domain: "seiche-nbs-owner-export-v1",
      export_id: "nbs-2026-07-r1",
      signer_key_id: "c".repeat(64),
      signed_at: "2026-08-10T02:05:00Z",
      manifest_sha256: "a".repeat(64),
      public_projection_sha256: "d".repeat(64),
      signature: "e".repeat(128),
    },
  };
}

function payloadFor(selection = "sources") {
  const generatedAt = selection === "china_macro" ? null : "2026-08-21T20:54:06Z";
  const domains = selection === "china_macro"
    ? { money_markets: null, forex: null, capital_markets: null }
    : {
      money_markets: "2026-08-20",
      forex: "2026-08-19",
      capital_markets: "2026-08-18",
    };
  const latest = selection === "china_macro" ? null : "2026-08-20";
  const selected = ["summary", "all"].includes(selection)
    ? latest
    : (["money_markets", "forex", "capital_markets"].includes(selection)
      ? domains[selection]
      : null);
  const payload = {
    schema: "seiche.world-markets.v1",
    selection,
    generated_at: generatedAt,
    as_of: selected,
    context_only: true,
    clocks: {
      boundary: "Response time never advances a source clock.",
      domains,
      snapshot_generated_at: generatedAt,
      latest_domain_as_of: latest,
      selected_evidence_as_of: selected,
      excluded_from_observation_clocks: ["china_macro.knowledge_time"],
    },
    citation: {
      canonical_url: "https://seiche.info/world-markets",
      generated_at: generatedAt,
      evidence_as_of: selected,
    },
    scope: { coverage_claim: "curated_partial_non_exhaustive" },
  };
  if (selection === "summary") payload.summary = {};
  else if (["money_markets", "forex", "capital_markets"].includes(selection)) {
    payload[selection] = {};
  } else if (selection === "china_macro") payload.china_macro = chinaMacro();
  else if (selection === "sources") payload.sources = [];
  else if (selection === "methodology") payload.methodology = {};
  else {
    payload.money_markets = {};
    payload.forex = {};
    payload.capital_markets = {};
    payload.china_macro = chinaMacro();
    payload.sources = [];
    payload.methodology = {};
  }
  return payload;
}

function mockJson(payload) {
  globalThis.fetch = async () => new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

async function assertContractRejects(payload, section = payload.selection) {
  mockJson(payload);
  await assert.rejects(
    fetchWorldMarkets({ section }),
    (error) => error instanceof SeicheClientError,
  );
}

test("accepts a bounded source response and records effective limits", async () => {
  const payload = payloadFor("sources");
  mockJson(payload);
  assert.deepEqual(await fetchWorldMarkets({ section: "sources" }), payload);
  assert.deepEqual(contractReceipt(payload, {
    timeoutMs: 1234,
    maxResponseBytes: 5678,
  }).client_limits, {
    timeout_ms: 1234,
    max_response_bytes: 5678,
    automatic_retries: 0,
  });
});

test("accepts both exact China availability states", async () => {
  const available = payloadFor("china_macro");
  mockJson(available);
  assert.equal((await fetchWorldMarkets({ section: "china_macro" })).china_macro.available, true);

  const unavailable = payloadFor("china_macro");
  unavailable.china_macro = chinaMacro({ available: false });
  mockJson(unavailable);
  assert.equal((await fetchWorldMarkets({ section: "china_macro" })).china_macro.available, false);
});

test("rejects aliases, arbitrary data, omissions, order drift, and contaminated clocks", async () => {
  const mutations = [
    (payload) => { payload.china_macro.values_published = true; },
    (payload) => { payload.china_macro.series_catalog[0].latest_value = "100.5"; },
    (payload) => { payload.china_macro.series_catalog[0].value = "100.5"; },
    (payload) => { payload.china_macro.series_catalog[0].harmless_metric = 100.5; },
    (payload) => { delete payload.china_macro.knowledge_time; },
    (payload) => { delete payload.china_macro.provenance; },
    (payload) => { delete payload.china_macro.attestation; },
    (payload) => { payload.china_macro.provenance.raw_sha256 = "b".repeat(64); },
    (payload) => { payload.china_macro.provenance.raw_size_bytes = 2048; },
    (payload) => { payload.china_macro.attestation.raw_sha256 = "b".repeat(64); },
    (payload) => { payload.china_macro.attestation.signed_at = "2026-08-10T01:59:59Z"; },
    (payload) => {
      payload.china_macro.knowledge_time = "2026-08-10T02:00:00.000001Z";
      payload.china_macro.attestation.signed_at = "2026-08-10T02:00:00Z";
    },
    (payload) => { payload.china_macro.series_catalog.reverse(); },
    (payload) => { payload.clocks.domains.china_macro = payload.china_macro.knowledge_time; },
    (payload) => { payload.clocks.selected_evidence_as_of = payload.china_macro.knowledge_time; },
    (payload) => { payload.citation.evidence_as_of = payload.china_macro.knowledge_time; },
    (payload) => { delete payload.citation.evidence_as_of; },
    (payload) => { delete payload.clocks.selected_evidence_as_of; },
    (payload) => { payload.clocks.excluded_from_observation_clocks = []; },
    (payload) => {
      payload.generated_at = payload.china_macro.knowledge_time;
      payload.clocks.snapshot_generated_at = payload.china_macro.knowledge_time;
      payload.citation.generated_at = payload.china_macro.knowledge_time;
    },
  ];
  for (const mutate of mutations) {
    const payload = payloadFor("china_macro");
    mutate(payload);
    await assertContractRejects(payload);
  }
});

test("unavailable China state rejects signed-state metadata", async () => {
  for (const [field, value] of [
    ["knowledge_time", "2026-08-10T02:00:00Z"],
    ["revision_id", "nbs-forged"],
    ["provenance", {}],
    ["attestation", {}],
  ]) {
    const payload = payloadFor("china_macro");
    payload.china_macro = chinaMacro({ available: false });
    payload.china_macro[field] = value;
    await assertContractRejects(payload);
  }
});

test("all requires China while every named selector preserves its own shape", async () => {
  const all = payloadFor("all");
  mockJson(all);
  await fetchWorldMarkets({ section: "all" });

  const missingChina = payloadFor("all");
  delete missingChina.china_macro;
  await assertContractRejects(missingChina);

  const missingCore = payloadFor("all");
  delete missingCore.forex;
  await assertContractRejects(missingCore);

  const named = payloadFor("forex");
  named.china_macro = chinaMacro();
  await assertContractRejects(named, "forex");
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
