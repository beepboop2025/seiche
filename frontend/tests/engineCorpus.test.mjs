import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const modelUrl = new URL("../src/engineCorpus.ts", import.meta.url);
const explorerUrl = new URL("../src/tabs/EngineDatasetExplorer.tsx", import.meta.url);
const source = await readFile(modelUrl, "utf8");
const explorerSource = await readFile(explorerUrl, "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  fileName: modelUrl.pathname,
});
const model = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled.outputText).toString("base64")}`
);

const INDEX_SHA = "a".repeat(64);
const OBJECT_SHA = "b".repeat(64);
const MANIFEST_SHA = "c".repeat(64);

function rights(overrides = {}) {
  return {
    acquisition_review: "approved_public",
    public_metadata: true,
    public_schema: false,
    public_preview_values: false,
    public_raw_download: false,
    publication_state: "metadata_only",
    ...overrides,
  };
}

function acquired(overrides = {}) {
  return {
    artifact_id: `liquilens-engine-object:release:market-rates:${OBJECT_SHA.slice(0, 16)}`,
    dataset_id: "market-rates",
    detail_href: "/v1/datasets/market-rates",
    collection_kind: "acquired_object",
    group: "market-rates",
    data_class: "context_feature",
    engines: ["money_market_watch"],
    media_format: "csv",
    content_sha256: OBJECT_SHA,
    acquired_date: "2026-08-10",
    attempt_count: 2,
    recovered: true,
    rights: rights(),
    license: { name: "Public data", url: "https://example.test/terms" },
    source: { page: "https://example.test/source" },
    ...overrides,
  };
}

function restricted(overrides = {}) {
  return {
    artifact_id: "liquilens-engine-restricted:release:nse-equity-cut",
    dataset_id: "nse-equity-cut",
    detail_href: "/v1/datasets/nse-equity-cut",
    collection_kind: "restricted_metadata_only",
    title: "NSE equity historical cut",
    publisher: "National Stock Exchange of India",
    group: "nse-equity",
    data_class: "context_feature",
    object_count: 736,
    row_count: 2_185_105,
    total_bytes: 300_000_000,
    event_from: "2023-08-11",
    event_to: "2026-08-10",
    formats: ["csv"],
    collection_time: "2026-08-12T00:00:00Z",
    manifest_sha256: MANIFEST_SHA,
    notes: "Internal research only; values and downloads are withheld.",
    forward_evidence_eligible: false,
    rights: rights({
      acquisition_review: "acquired_internal_research_only",
      publication_state: "restricted_metadata_only",
    }),
    source: { page: "https://www.nseindia.com/" },
    ...overrides,
  };
}

function counts(overrides = {}) {
  return {
    attempt_count: 1118,
    successful_attempt_count: 1110,
    failed_attempt_count: 8,
    object_count: 1110,
    recovered_object_count: 8,
    unresolved_object_count: 0,
    published_object_count: 1110,
    withheld_object_count: 0,
    restricted_collection_count: 12,
    structurally_profiled_object_count: 19,
    bis_linked_object_count: 27,
    ...overrides,
  };
}

function page(datasets, overrides = {}) {
  return {
    schema_version: "1.0.0",
    release_id: "corpus-0123456789abcdef",
    generated_at: "2026-08-10T06:23:01Z",
    verified_at: "2026-08-10T06:23:01Z",
    index_artifact_id: `liquilens-engine-public-index-v1:release:${INDEX_SHA}`,
    index_sha256: INDEX_SHA,
    total: datasets.length,
    count: datasets.length,
    next_cursor: null,
    filters: {},
    counts: counts(),
    datasets,
    ...overrides,
  };
}

test("acquired objects preserve recovery, hashes, rights, and structural-only detail", () => {
  const row = acquired({
    dataset_id: "bis-ws_gli_csv_flat",
    detail_href: "/v1/datasets/bis-ws_gli_csv_flat",
    group: "bis-bulk",
    rights: rights({ public_schema: true, publication_state: "schema_only" }),
    structural_profile_available: true,
    structural_profile: {
      format: "zip",
      archive_entry_count: 1,
      archive_entries_sample: ["market.csv"],
      inner_table: "market.csv",
      inner_header: {
        column_count: 3,
        columns_sample: ["TIME_PERIOD", "OBS_VALUE", "UNIT_MEASURE"],
        delimiter: ",",
      },
    },
    normalized_records: {
      relation: "normalized_observations",
      flow_id: "WS_GLI",
      href: "/v1/bis/records?flow_id=WS_GLI",
    },
  });
  const normalized = model.normalizeEnginePage(page([row]));
  const object = normalized.datasets[0];

  assert.equal(object.recovered, true);
  assert.equal(object.content_sha256, OBJECT_SHA);
  assert.equal(object.rights.publication_state, "schema_only");
  assert.deepEqual(object.structural_profile.inner_header.columns_sample, [
    "TIME_PERIOD", "OBS_VALUE", "UNIT_MEASURE",
  ]);
  assert.equal(object.normalized_records.href, "/v1/bis/records?flow_id=WS_GLI");
  assert.equal(normalized.counts.object_count, 1110);
  assert.equal(normalized.counts.attempt_count, 1118);
});

test("acquired internal cuts remain explicit metadata-only collections", () => {
  const normalized = model.normalizeEnginePage(page([restricted()]));
  const row = normalized.datasets[0];

  assert.equal(row.collection_kind, "restricted_metadata_only");
  assert.equal(row.row_count, 2_185_105);
  assert.equal(row.rights.acquisition_review, "acquired_internal_research_only");
  assert.equal(row.rights.public_preview_values, false);
  assert.equal(row.forward_evidence_eligible, false);
  assert.equal(row.manifest_sha256, MANIFEST_SHA);
  assert.equal("download" in row, false);
});

test("withheld identifiers cannot enter the public UI model", () => {
  assert.throws(
    () => model.normalizeEngineDataset(acquired({
      rights: rights({
        public_metadata: false,
        publication_state: "metadata_only",
      }),
    })),
    /rights lattice/,
  );
  const normalized = model.normalizeEnginePage(page([], {
    counts: counts({ published_object_count: 1109, withheld_object_count: 1 }),
  }));
  assert.equal(normalized.datasets.length, 0);
  assert.equal(normalized.counts.withheld_object_count, 1);
});

test("provenance and corpus links reject foreign, signed, and ambiguous URLs", () => {
  assert.equal(model.safeProvenanceUrl("https://example.test/source"), "https://example.test/source");
  assert.equal(model.safeProvenanceUrl("//example.test/source"), null);
  assert.equal(model.safeProvenanceUrl("https://example.test/source?token=secret"), null);
  assert.equal(model.safeProvenanceUrl("http://example.test/source"), null);
  assert.equal(model.safeDatasetDetailHref("/v1/datasets/market-rates"), "/v1/datasets/market-rates");
  assert.equal(
    model.safeDatasetDetailHref("/api/v2/corpus/v1/datasets/market-rates"),
    "/v1/datasets/market-rates",
  );
  assert.equal(model.safeDatasetDetailHref("https://evil.test/v1/datasets/market-rates"), null);
  assert.equal(model.safeCorpusHref("/v1/bis/records?flow_id=WS_GLI"), "/v1/bis/records?flow_id=WS_GLI");
  assert.equal(model.safeCorpusHref("/v1/bis/records?flow_id=WS_GLI&token=secret"), null);
  assert.equal(
    model.safeCorpusExportHref("/api/v2/corpus/v1/seiche/exports/market-pack/receipt.json"),
    "/v1/seiche/exports/market-pack/receipt.json",
  );
  assert.equal(model.safeCorpusExportHref("https://evil.test/export.json"), null);
  assert.equal(model.safeCorpusExportHref("/v1/seiche/exports/market-pack/receipt.json?token=secret"), null);
  assert.throws(
    () => model.normalizeEngineDataset(acquired({
      dataset_id: "bis-ws_gli_csv_flat",
      detail_href: "/v1/datasets/bis-ws_gli_csv_flat",
      group: "bis-bulk",
      normalized_records: {
        relation: "normalized_observations",
        flow_id: "WS_CBS_PUB",
        href: "/v1/bis/records?flow_id=WS_CBS_PUB",
      },
    })),
    /normalized-record link is invalid/,
  );
  assert.throws(
    () => model.normalizeEngineDataset(acquired({
      dataset_id: "bis-ws_gli_csv_flat",
      detail_href: "/v1/datasets/bis-ws_gli_csv_flat",
      group: "bis-bulk",
    })),
    /normalized-record link is missing/,
  );
});

test("pagination and detail are bound to one immutable index and artifact", () => {
  const first = model.normalizeEnginePage(page([acquired()], {
    total: 2,
    next_cursor: "opaque-cursor",
  }));
  const secondRow = acquired({
    artifact_id: `liquilens-engine-object:release:market-fx:${"d".repeat(16)}`,
    dataset_id: "market-fx",
    detail_href: "/v1/datasets/market-fx",
    content_sha256: "d".repeat(64),
  });
  const second = model.normalizeEnginePage(page([secondRow], { total: 2 }));
  const merged = model.mergeEnginePages(first, second);
  assert.deepEqual(merged.datasets.map((row) => row.dataset_id), ["market-rates", "market-fx"]);

  const changed = model.normalizeEnginePage(page([secondRow], {
    total: 2,
    index_sha256: "e".repeat(64),
  }));
  assert.throws(() => model.mergeEnginePages(first, changed), /index changed/);
  const changedFilter = model.normalizeEnginePage(page([secondRow], {
    total: 2,
    filters: { group: "market-fx" },
  }));
  assert.throws(() => model.mergeEnginePages(first, changedFilter), /pagination contract changed/);
  const changedTotal = model.normalizeEnginePage(page([secondRow], { total: 3 }));
  assert.throws(() => model.mergeEnginePages(first, changedTotal), /pagination contract changed/);

  const detail = model.normalizeEngineDetail({
    ...page([], { count: 0, total: 0 }),
    dataset: acquired({ structural_profile_available: false }),
  });
  assert.equal(model.acceptEngineDetail(first, detail, acquired().artifact_id), detail);
  assert.throws(() => model.acceptEngineDetail(first, detail, secondRow.artifact_id), /another artifact/);
});

test("rights state, dates, formats, and structural claims fail closed", () => {
  assert.throws(
    () => model.normalizeEngineDataset(acquired({
      rights: rights({ public_preview_values: true, publication_state: "preview_values" }),
    })),
    /rights lattice/,
  );
  assert.throws(
    () => model.normalizeEngineDataset(restricted({ event_from: "2027-01-01", event_to: "2026-01-01" })),
    /reversed/,
  );
  assert.throws(
    () => model.normalizeEngineDataset(acquired({ media_format: "exe" })),
    /media_format/,
  );
  assert.throws(
    () => model.normalizeEngineDataset(acquired({ recovered: "false" })),
    /recovered state/,
  );
  assert.throws(
    () => model.normalizeEngineDataset(restricted({
      rights: rights({
        acquisition_review: "acquired_internal_research_only",
        public_schema: true,
        publication_state: "schema_only",
      }),
    })),
    /metadata-only rights/,
  );
  assert.throws(
    () => model.normalizeEngineDataset(acquired({
      rights: rights({
        public_schema: true,
        public_preview_values: true,
        publication_state: "preview_values",
      }),
    })),
    /structural-only rights/,
  );
  assert.throws(
    () => model.normalizeEngineDataset(acquired({
      rights: rights(),
      structural_profile: { format: "csv", header: { column_count: 1, columns_sample: ["value"] } },
    })),
    /exceeds publication rights/,
  );
  assert.throws(
    () => model.normalizeEngineDetail({
      ...page([], { count: 0, total: 0 }),
      dataset: acquired({ structural_profile_available: true }),
    }),
    /structural profile contradicts its rights/,
  );
  assert.throws(
    () => model.normalizeEngineDetail({
      ...page([], { count: 0, total: 0 }),
      dataset: acquired({
        rights: rights({ public_schema: true, publication_state: "schema_only" }),
        structural_profile_available: false,
      }),
    }),
    /structural profile contradicts its rights/,
  );
});

test("registry group and engine filters remain server-side across pagination", () => {
  assert.match(explorerSource, /params\.set\("group", group\)/);
  assert.match(explorerSource, /params\.set\("engine", engine\)/);
  assert.equal(explorerSource.match(/params\.set\("group", group\)/g)?.length, 2);
  assert.equal(explorerSource.match(/params\.set\("engine", engine\)/g)?.length, 2);
  assert.match(explorerSource, /Market group/);
  assert.match(explorerSource, /Engine use/);
});
