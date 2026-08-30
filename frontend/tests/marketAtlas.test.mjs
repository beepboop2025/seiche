import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const modelUrl = new URL("../src/marketAtlas.ts", import.meta.url);
const explorerUrl = new URL("../src/tabs/MarketSeriesExplorer.tsx", import.meta.url);
const datasetExplorerUrl = new URL("../src/tabs/EngineDatasetExplorer.tsx", import.meta.url);
const corpusUrl = new URL("../src/tabs/Corpus.tsx", import.meta.url);
const appUrl = new URL("../src/App.tsx", import.meta.url);
const catalogUrl = new URL("../public/.well-known/ai-catalog.json", import.meta.url);
const source = await readFile(modelUrl, "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: modelUrl.pathname,
});
const atlas = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled.outputText).toString("base64")}`
);

function observation(overrides = {}) {
  return {
    market_id: "US-USD",
    monetary_area_id: "US",
    jurisdiction_codes: ["US"],
    currency: "USD",
    instrument_id: "US.NYFED.SOFR",
    semantic_role: "SECURED_OVERNIGHT",
    value: "531",
    value_status: null,
    canonical_unit: "basis_points",
    rate_compounding: "simple",
    day_count: "ACT/360",
    event_time: "2026-08-28T00:00:00+00:00",
    source_publication_time: "2026-08-28T12:00:00+00:00",
    knowledge_time: "2026-08-28T12:01:00+00:00",
    revision_id: "v1",
    source: "nyfed_rates",
    evidence_hash: "a".repeat(64),
    connector_classification: "official_open",
    redistribution_status: "allowed",
    quality: "verified",
    staleness: "fresh",
    ...overrides,
  };
}

function seriesPayload(overrides = {}) {
  return {
    schema: "seiche.market-series.v2",
    status: "PARTIAL",
    market_id: "US-USD",
    monetary_area_id: "US",
    jurisdiction_codes: ["US"],
    currency: "USD",
    policy_regime: "corridor",
    support_status: "SUPPORTED",
    calibration_id: "us-v1",
    coverage_scope: "returned_page",
    readiness_scope: "latest_public_observation_per_instrument",
    evidence_eligibility: { eligible: true, reasons: [] },
    event_cutoff: "2026-08-28T00:00:00+00:00",
    knowledge_cutoff: "2026-08-28T12:01:00+00:00",
    stale_inputs: [],
    faults: [],
    data_coverage: [],
    instruments: [
      {
        instrument_id: "US.NYFED.SOFR",
        mnemonic: "SOFR",
        semantic_role: "SECURED_OVERNIGHT",
        canonical_unit: "basis_points",
        source_adapter: "nyfed_rates",
        publisher: "Federal Reserve Bank of New York",
        source_url: "https://www.newyorkfed.org/markets/reference-rates/sofr",
        connector_classification: "official_open",
        redistribution_status: "allowed",
        expected_cadence: "P1D",
        availability: "READY",
      },
    ],
    observations: [observation()],
    next_cursor: null,
    ...overrides,
  };
}

test("numeric parsing never turns missing or malformed evidence into zero", () => {
  for (const value of [null, undefined, "", "  ", "NaN", Number.NaN, Infinity, {}, []]) {
    assert.equal(atlas.numericValue(value), null);
  }
  assert.equal(atlas.numericValue("0"), 0);
  assert.equal(atlas.numericValue(0), 0);
  assert.equal(atlas.numericValue("-12.75"), -12.75);
});

test("series normalization preserves rights-redacted evidence metadata", () => {
  const payload = seriesPayload({
    observations: [
      observation({
        value: null,
        value_status: "REDACTED_BY_LICENCE",
        redistribution_status: "derived_only",
        connector_classification: "licensed",
      }),
    ],
  });
  const normalized = atlas.normalizeMarketSeries(payload, "US-USD");

  assert.equal(normalized.observations[0].value, null);
  assert.equal(normalized.observations[0].value_status, "REDACTED_BY_LICENCE");
  assert.equal(normalized.observations[0].revision_id, "v1");
  assert.equal(normalized.observations[0].evidence_hash, "a".repeat(64));
  assert.equal(atlas.numericSeriesForInstrument(normalized.observations, "US.NYFED.SOFR").length, 0);
});

test("cursor pages deduplicate immutable identities and become globally chronological", () => {
  const newest = observation({ event_time: "2026-08-29T00:00:00Z", revision_id: "new" });
  const boundary = observation({ event_time: "2026-08-28T00:00:00Z", revision_id: "boundary" });
  const oldest = observation({ event_time: "2026-08-27T00:00:00Z", revision_id: "old" });
  const merged = atlas.mergeObservationPages([boundary, newest], [oldest, boundary]);

  assert.deepEqual(merged.map((row) => row.revision_id), ["old", "boundary", "new"]);
});

test("series rejects cross-market observations and pagination metadata drift", () => {
  assert.throws(
    () => atlas.normalizeMarketSeries(seriesPayload({
      observations: [observation({ market_id: "EA-EUR" })],
    }), "US-USD"),
    /observation identity mismatch/,
  );

  const current = atlas.normalizeMarketSeries(seriesPayload(), "US-USD");
  const older = atlas.normalizeMarketSeries(seriesPayload({
    event_cutoff: "2026-08-20T00:00:00Z",
    knowledge_cutoff: "2026-08-20T12:00:00Z",
    observations: [observation({ event_time: "2026-08-20T00:00:00Z" })],
  }), "US-USD");
  assert.doesNotThrow(() => atlas.assertCompatibleMarketSeriesPage(current, older));

  const changedRights = atlas.normalizeMarketSeries(seriesPayload({
    evidence_eligibility: { eligible: false, reasons: ["rights changed"] },
  }), "US-USD");
  assert.throws(
    () => atlas.assertCompatibleMarketSeriesPage(current, changedRights),
    /changed identity, rights, instruments, or source state/,
  );
});

test("the plot chooses the latest loaded knowledge vintage for each event", () => {
  const event = "2026-08-28T00:00:00Z";
  const rows = [
    observation({ event_time: event, knowledge_time: "2026-08-28T10:00:00Z", revision_id: "first", value: "500" }),
    observation({ event_time: event, knowledge_time: "2026-08-28T12:00:00Z", revision_id: "revision", value: "505" }),
    observation({ event_time: "2026-08-29T00:00:00Z", knowledge_time: "2026-08-29T12:00:00Z", revision_id: "next", value: "510" }),
    observation({ event_time: "2026-08-30T00:00:00Z", revision_id: "redacted", value: null }),
  ];
  const points = atlas.numericSeriesForInstrument(rows, "US.NYFED.SOFR");

  assert.deepEqual(points.map((point) => point.value), [505, 510]);
  assert.equal(points[0].observation.revision_id, "revision");
  const plot = atlas.buildPlotModel(points);
  assert.ok(plot);
  assert.match(plot.path, /^M /);
  assert.ok(plot.points.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y)));
});

test("the chart excludes rejected, redacted, non-public, and unknown-quality values", () => {
  const rows = [
    observation({ event_time: "2026-08-21T00:00:00Z", revision_id: "public", value: "501" }),
    observation({ event_time: "2026-08-22T00:00:00Z", revision_id: "rejected", quality: "rejected", value: "999" }),
    observation({ event_time: "2026-08-23T00:00:00Z", revision_id: "redacted", value_status: "REDACTED_BY_LICENCE", value: "998" }),
    observation({ event_time: "2026-08-24T00:00:00Z", revision_id: "derived", redistribution_status: "derived_only", value: "997" }),
    observation({ event_time: "2026-08-25T00:00:00Z", revision_id: "unknown", quality: "quality unavailable", value: "996" }),
    observation({ event_time: "not-a-date", revision_id: "bad-clock", value: "995" }),
  ];

  const points = atlas.numericSeriesForInstrument(rows, "US.NYFED.SOFR");
  assert.deepEqual(points.map((point) => point.observation.revision_id), ["public"]);
});

test("constant and single-point series still produce a finite accessible plot domain", () => {
  const points = atlas.numericSeriesForInstrument([observation({ value: "0" })], "US.NYFED.SOFR");
  const plot = atlas.buildPlotModel(points);

  assert.ok(plot);
  assert.equal(plot.points.length, 1);
  assert.ok(plot.minValue < 0);
  assert.ok(plot.maxValue > 0);
  assert.equal(plot.observedMinValue, 0);
  assert.equal(plot.observedMaxValue, 0);
  assert.ok(Number.isFinite(plot.points[0].x));
  assert.ok(Number.isFinite(plot.points[0].y));
});

test("text, role and instrument filters compose without mutating source rows", () => {
  const sofr = observation();
  const iorb = observation({
    instrument_id: "US.FED.IORB",
    semantic_role: "POLICY_TARGET",
    source: "fred_daily",
    revision_id: "iorb-v1",
  });
  const rows = [sofr, iorb];

  assert.deepEqual(
    atlas.filterObservations(rows, { query: "FRED", role: "POLICY_TARGET", instrumentId: "US.FED.IORB" })
      .map((row) => row.instrument_id),
    ["US.FED.IORB"],
  );
  assert.equal(rows.length, 2);
});

test("catalog normalization rejects a missing contract and sorts usable markets", () => {
  assert.throws(() => atlas.normalizeMarketCatalog({}), /markets array/);
  const catalog = atlas.normalizeMarketCatalog({
    schema: "seiche.markets.v2",
    count: 2,
    markets: [
      { market_id: "US-USD", display_name: "United States", currency: "USD", stale_inputs: [], faults: [] },
      { market_id: "EA-EUR", display_name: "Euro area", currency: "EUR", stale_inputs: [], faults: [] },
    ],
  });

  assert.deepEqual(catalog.markets.map((market) => market.market_id), ["EA-EUR", "US-USD"]);
});

test("series keeps sanitized source faults visible and rejects false-zero metadata", () => {
  const normalized = atlas.normalizeMarketSeries(seriesPayload({
    faults: [{
      source: "hkma_official",
      status: "FAILED",
      category: "HTTP_ERROR",
      detail: "official source returned an HTTP error",
      market_id: "US-USD",
      finished_at: "2026-08-29T20:06:28Z",
      next_due: "2026-08-30T20:05:08Z",
    }],
  }), "US-USD");
  assert.equal(normalized.fault_count, 1);
  assert.equal(normalized.faults[0].category, "HTTP_ERROR");
  assert.throws(
    () => atlas.normalizeMarketSeries(seriesPayload({ faults: undefined }), "US-USD"),
    /faults must be an array/,
  );
  assert.throws(
    () => atlas.normalizeMarketCatalog({
      count: 0,
      markets: [{ market_id: "US-USD", stale_inputs: [], faults: [] }],
    }),
    /count differs/,
  );
});

test("unit labels keep canonical scale visible", () => {
  assert.equal(atlas.canonicalUnitLabel("basis_points", "USD"), "bp");
  assert.equal(atlas.canonicalUnitLabel("local_currency_millions", "INR"), "INR mn");
  assert.equal(atlas.canonicalUnitLabel("index_points", "CNY"), "index points");
});

test("instrument normalization preserves publisher and only HTTPS source URLs are linkable", () => {
  const normalized = atlas.normalizeMarketSeries(seriesPayload(), "US-USD");
  assert.equal(normalized.instruments[0].publisher, "Federal Reserve Bank of New York");
  assert.equal(
    atlas.safePublicSourceUrl(normalized.instruments[0].source_url),
    "https://www.newyorkfed.org/markets/reference-rates/sofr",
  );
  assert.equal(atlas.safePublicSourceUrl("http://example.com/source"), null);
  assert.equal(atlas.safePublicSourceUrl("javascript:alert(1)"), null);
  assert.equal(atlas.safePublicSourceUrl("/relative/source"), null);
});

test("visual evidence tones fail closed for unknown and unavailable states", () => {
  assert.equal(atlas.atlasStateTone("allowed"), "positive");
  assert.equal(atlas.atlasStateTone("verified"), "positive");
  assert.equal(atlas.atlasStateTone("unavailable"), "neutral");
  assert.equal(atlas.atlasStateTone("rights unavailable"), "neutral");
  assert.equal(atlas.atlasStateTone("not_ready"), "neutral");
  assert.equal(atlas.atlasStateTone(""), "neutral");
});

test("market explorer passes currency through every instrument-level unit label", async () => {
  const explorer = await readFile(explorerUrl, "utf8");
  assert.match(explorer, /canonicalUnitLabel\(instrument\.canonical_unit, currency\)/);
  assert.doesNotMatch(explorer, /canonicalUnitLabel\(instrument\.canonical_unit\)/);
  assert.match(explorer, /source URL unavailable/);
  assert.match(explorer, /safePublicSourceUrl\(instrument\.source_url\)/);
});

test("dataset cursors are generation-bound and detailed market inventory is lazy", async () => {
  const [corpus, datasets] = await Promise.all([
    readFile(corpusUrl, "utf8"),
    readFile(datasetExplorerUrl, "utf8"),
  ]);
  assert.match(datasets, /pageGeneration\.current !== generation/);
  assert.match(datasets, /current\.data\.next_cursor !== expectedCursor/);
  assert.match(datasets, /paginationAbort\.current === controller/);
  assert.match(datasets, /acceptEngineDetail/);
  assert.match(datasets, /detailGeneration\.current === generation/);
  assert.match(corpus, /new IntersectionObserver/);
  assert.match(corpus, /marketInventoryRequested/);
});

test("navigation exposes current state and the corpus card mirrors nine grounded tools", async () => {
  const [app, catalogText] = await Promise.all([
    readFile(appUrl, "utf8"),
    readFile(catalogUrl, "utf8"),
  ]);
  const catalog = JSON.parse(catalogText);
  const corpus = catalog.entries.find(
    (entry) => entry.identifier === "urn:air:seiche.info:mcp:market-corpus",
  );

  assert.match(app, /aria-current=\{t === tab \? "page" : undefined\}/);
  assert.ok(corpus);
  assert.equal(corpus.metadata.publicToolCount, 9);
  assert.equal(corpus.capabilities.length, 9);
  assert.ok(corpus.capabilities.includes("bis_flow_manifest"));
  assert.ok(corpus.capabilities.includes("inspect_dataset"));
  assert.ok(corpus.capabilities.includes("corpus_health"));
  assert.equal(corpus.data.repository, undefined);
  assert.equal(corpus.metadata.availabilityClaim, "declared_endpoint_verify_with_corpus_health");
  assert.doesNotMatch(JSON.stringify(corpus), /\b(?:active|live)\b/i);
});

test("market inventory rejects an unavailable payload instead of displaying zero coverage", async () => {
  const corpusSource = await readFile(new URL("../src/tabs/Corpus.tsx", import.meta.url), "utf8");
  assert.match(corpusSource, /value\.status !== "ok"/);
  assert.match(corpusSource, /value\.evidence_class !== "observed"/);
  assert.match(corpusSource, /\.then\(requireAvailableSeicheMarkets\)/);
  assert.match(corpusSource, /availableSeicheMarkets\(catalog\?\.corpora\?\.seiche\)/);
});
