import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const modelUrl = new URL("../src/bisAtlas.ts", import.meta.url);
const explorerUrl = new URL("../src/tabs/BisFlowExplorer.tsx", import.meta.url);
const source = await readFile(modelUrl, "utf8");
const explorerSource = await readFile(explorerUrl, "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  fileName: modelUrl.pathname,
});
const model = await import(`data:text/javascript;base64,${Buffer.from(transpiled.outputText).toString("base64")}`);

const flows = [
  { flow_id: "WS_GLI", name: "Global liquidity indicators", topic: "global liquidity and offshore currency credit", priority_tier: 1, product_scores: { seiche: 100 } },
  { flow_id: "WS_XRU", name: "US dollar exchange rates", topic: "bilateral US dollar exchange rates", priority_tier: 1, product_scores: { seiche: 98 } },
  { flow_id: "WS_OTC_DERIV2", name: "OTC derivatives outstanding", priority_tier: 1, product_scores: { seiche: 95 } },
  { flow_id: "WS_CPMI_SYSTEMS", name: "Payment and settlement systems", priority_tier: 2, product_scores: { seiche: 92 } },
];

function record(index = 1, overrides = {}) {
  return {
    format: "liquilens-bis-observation-v1",
    logical_id: `bisobs_${String(index).padStart(40, "0")}`,
    flow_id: "WS_GLI",
    flow_name: "Global liquidity indicators",
    flow_version: "1.0",
    series_key: `Q.TO1.5C.S.B.R.B.${index}`,
    dimensions: { FREQ: "Q", UNIT_MEASURE: "770", REF_AREA: "US" },
    dimension_labels: { FREQ: "Quarterly", UNIT_MEASURE: "Percentage of GDP", REF_AREA: "United States" },
    period: {
      source_period: "2000-Q1",
      event_time: "2000-03-31T00:00:00Z",
      event_time_start: "2000-01-01T00:00:00Z",
      event_time_end: "2000-03-31T00:00:00Z",
      precision: "quarter",
    },
    event_time: "2000-03-31T00:00:00Z",
    knowledge_time: "2026-08-11T00:00:00Z",
    first_knowledge_time: "2026-08-11T00:00:00Z",
    knowledge_time_basis: "first-local-capture",
    as_of_rule: "usable only when as_of >= knowledge_time",
    historical_vintage_reconstructed: false,
    value_numeric: 79.843,
    value_text: "79.843",
    action: "I",
    attributes: {},
    attribute_labels: {},
    revision_number: 1,
    semantic_sha256: "a".repeat(64),
    source: {
      publisher: "Bank for International Settlements",
      url: "https://stats.bis.org/statx/srs/table/j4.pdf",
      capture_id: 42,
      capture_sha256: "b".repeat(64),
      capture_knowledge_time: "2026-08-11T00:00:00Z",
      source_row_number: index + 1,
      license_url: "https://www.bis.org/terms_conditions.htm",
      attribution_required: true,
    },
    product_scores: { seiche: 100 },
    topic: "global liquidity",
    evidence_class: "observed",
    ...overrides,
  };
}

function page(records, overrides = {}) {
  return {
    schema_version: "2026-08-30",
    generated_at: "2026-08-30T12:00:00Z",
    knowledge_time: "2026-08-11T00:00:00Z",
    artifact_generated_at: "2026-08-11T01:00:00Z",
    artifact_knowledge_time: "2026-08-11T00:00:00Z",
    artifact_sha256: "c".repeat(64),
    serving_generated_at: "2026-08-30T11:00:00Z",
    flow_id: "WS_GLI",
    evidence_class: "observed",
    rights: {
      usage_class: "public",
      license_url: "https://www.bis.org/terms_conditions.htm",
      commercial_training_eligible: false,
      knowledge_time: "2026-08-11T00:00:00Z",
      public_values: true,
    },
    count: records.length,
    complete_snapshot: false,
    next_cursor: "opaque-cursor",
    records,
    ...overrides,
  };
}

test("BIS flows are classified into meaningful market strata", () => {
  assert.equal(model.bisDomain(flows[0]), "credit");
  assert.equal(model.bisDomain(flows[1]), "fx");
  assert.equal(model.bisDomain(flows[2]), "markets");
  assert.equal(model.bisDomain(flows[3]), "payments");
});

test("flow filtering preserves priority order and searches identifiers", () => {
  assert.deepEqual(model.filterBisFlows(flows, "fx", "").map((row) => row.flow_id), ["WS_XRU"]);
  assert.deepEqual(model.filterBisFlows(flows, "all", "offshore").map((row) => row.flow_id), ["WS_GLI"]);
  assert.equal(model.sortBisFlows(flows)[0].flow_id, "WS_GLI");
});

test("live numeric BIS records preserve codes, labels, value text, and clocks", () => {
  const normalized = model.normalizeBisPage(page([record()]), "WS_GLI");
  const row = normalized.records[0];
  assert.equal(row.value_numeric, 79.843);
  assert.equal(row.value_decimal, null);
  assert.equal(model.bisValue(row), "79.843 Percentage of GDP");
  assert.deepEqual(model.bisDimensionPairs(row)[0], {
    code: "FREQ",
    value: "Q",
    label: "Quarterly",
  });
  assert.equal(row.source.publisher, "Bank for International Settlements");
  assert.equal(row.format, "liquilens-bis-observation-v1");
  assert.equal(row.period.source_period, "2000-Q1");
  assert.equal(row.as_of_rule, "usable only when as_of >= knowledge_time");
  assert.equal(row.event_time_basis, null);
  assert.equal(row.scheduled_release_time, null);
});

test("exact-decimal and missing values remain distinct without inventing zero", () => {
  const exact = record(2, { value_decimal: "1.000", value_numeric: undefined, value_text: "1.000" });
  const missing = record(3, { value_decimal: null, value_numeric: undefined, value_text: "" });
  const normalized = model.normalizeBisPage(page([exact, missing]), "WS_GLI");
  assert.equal(normalized.records[0].value_decimal, "1.000");
  assert.equal(model.bisValue(normalized.records[0]), "1.000 Percentage of GDP");
  assert.equal(model.bisValue(normalized.records[1]), "unavailable");
});

test("pagination binds flow and immutable artifact identity", () => {
  const first = model.normalizeBisPage(page([record(1)]), "WS_GLI");
  const second = model.normalizeBisPage(page([record(2)], { next_cursor: null }), "WS_GLI");
  const merged = model.mergeBisPages(first, second);
  assert.deepEqual(merged.records.map((row) => row.logical_id), [record(1).logical_id, record(2).logical_id]);
  assert.equal(merged.count, 2);
  assert.equal(merged.next_cursor, null);

  const changed = model.normalizeBisPage(
    page([record(3)], { artifact_sha256: "d".repeat(64) }),
    "WS_GLI",
  );
  assert.throws(() => model.mergeBisPages(first, changed), /snapshot changed/);
  assert.throws(() => model.mergeBisPages(first, first), /repeated a logical record/);
});

test("API-only and registry-only flows preserve explicit unavailable snapshot semantics", () => {
  const apiOnly = model.normalizeBisPage(
    page([], {
      flow_id: "BIS_REL_CAL",
      evidence_class: "unavailable",
      count: 0,
      complete_snapshot: false,
      serving_generated_at: null,
    }),
    "BIS_REL_CAL",
  );
  assert.equal(apiOnly.count, 0);
  assert.equal(apiOnly.complete_snapshot, false);
  assert.equal(model.isBisBulkUnavailable(apiOnly, "api-only"), true);

  const registryOnly = model.normalizeBisPage(
    page([], {
      flow_id: "WS_NA_SEC_C3",
      evidence_class: "unavailable",
      artifact_generated_at: null,
      artifact_knowledge_time: null,
      artifact_sha256: null,
      serving_generated_at: null,
      rights: {
        usage_class: "unavailable",
        license_url: null,
        commercial_training_eligible: false,
        knowledge_time: null,
        public_values: false,
      },
      count: 0,
      complete_snapshot: null,
      next_cursor: null,
    }),
    "WS_NA_SEC_C3",
  );
  assert.equal(registryOnly.complete_snapshot, null);
  assert.equal(model.isBisBulkUnavailable(registryOnly, "registry-only"), true);

  for (const completeSnapshot of [true, false]) {
    const bulkMaterializing = model.normalizeBisPage(
      page([], {
        evidence_class: "unavailable",
        serving_generated_at: null,
        count: 0,
        complete_snapshot: completeSnapshot,
        next_cursor: null,
      }),
      "WS_GLI",
    );
    assert.equal(model.isBisBulkUnavailable(bulkMaterializing, "bulk-flat"), true);
  }
});

test("page count and snapshot completeness fail closed without changing observed search emptiness", () => {
  assert.throws(
    () => model.normalizeBisPage(page([record()], { count: 0 }), "WS_GLI"),
    /count does not match/,
  );
  assert.throws(
    () => model.normalizeBisPage(page([], { complete_snapshot: "unknown" }), "WS_GLI"),
    /complete_snapshot/,
  );
  const observed = model.normalizeBisPage(page([record()]), "WS_GLI");
  assert.equal(model.isBisBulkUnavailable(observed, "bulk-flat"), false);
  assert.deepEqual(model.filterBisRecords(observed.records, "not-present"), []);
  const observedEmpty = model.normalizeBisPage(page([], {
    evidence_class: "observed",
    count: 0,
    next_cursor: null,
  }), "WS_GLI");
  assert.equal(model.isBisBulkUnavailable(observedEmpty, "bulk-flat"), false);
});

test("mixed-flow, non-finite, and unknown evidence fail closed", () => {
  assert.throws(
    () => model.normalizeBisPage(page([record(1, { flow_id: "WS_XRU" })]), "WS_GLI"),
    /mixed records/,
  );
  assert.throws(
    () => model.normalizeBisPage(page([record(1, { value_numeric: Number.POSITIVE_INFINITY })]), "WS_GLI"),
    /value_numeric/,
  );
  const unknown = model.normalizeBisPage(
    page([record(1, { evidence_class: "mystery" })], { evidence_class: "mystery" }),
    "WS_GLI",
  );
  assert.equal(unknown.evidence_class, "unavailable");
  assert.equal(unknown.records[0].evidence_class, "unavailable");
});

test("explorer calls full records and declares race and selection semantics", () => {
  assert.match(explorerSource, /\/v1\/bis\/records\?flow_id=/);
  assert.doesNotMatch(explorerSource, /\/v1\/bis\/observations/);
  assert.match(explorerSource, /requestGeneration/);
  assert.match(explorerSource, /mergeBisPages/);
  assert.match(explorerSource, /aria-pressed=/);
  assert.match(explorerSource, /aria-current=/);
  assert.match(explorerSource, /artifact_sha256/);
  assert.match(explorerSource, /source\.capture_knowledge_time/);
  assert.match(explorerSource, /"source_period"/);
  assert.match(explorerSource, /NO BULK SNAPSHOT/);
  assert.match(explorerSource, /count 0 is not a zero market observation/);
  assert.match(explorerSource, /isBisBulkUnavailable/);
});
