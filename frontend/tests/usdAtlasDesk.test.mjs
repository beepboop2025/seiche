import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const transpile = async (path, context) => {
  vm.runInNewContext(ts.transpileModule(await readFile(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  return context.exports;
};
const funding = await transpile("../src/usdFunding.ts", { exports: {} });
const { usdAtlasDesk, usdDisplayMetric, usdChartPanes, publicUsdObservation } = await transpile("../src/usdAtlasDesk.ts", {
  exports: {}, URL, require: (name) => {
    if (name === "./usdFunding") return funding;
    throw new Error("Unexpected dependency: " + name);
  },
});

const atlas = JSON.parse(await readFile(new URL("./moneyMarketsAtlas.live.json", import.meta.url), "utf8"));
const copy = (value) => JSON.parse(JSON.stringify(value));
// Values built inside the VM realm compare structurally only after a JSON round trip.
const plain = copy;
const us = () => copy(atlas.markets.find((market) => market.market_id === "US-USD"));
const section = (desk, id) => desk.sections.find((item) => item.id === id);
const ids = (rows) => rows.map((row) => row.id);

test("the live US pack fills the desk with its 37 rights-cleared rows and lists the 7 withheld ones", () => {
  const desk = usdAtlasDesk(us());
  assert.equal(desk.publicCount, 37);
  assert.equal(desk.declaredCount, 44);
  assert.deepEqual(plain(desk.sections.map((item) => [item.id, item.metrics.length])),
    [["policy_corridor", 2], ["secured_repo", 18], ["unsecured_funding", 12], ["sofr_averages", 4], ["cash_facilities", 2]]);
  assert.deepEqual(plain(desk.gaps.map((gap) => gap.label)), ["IORB", "SOFR", "EFFR", "SRF CEILING", "TB3M", "CP NONFIN 3M", "WRESBAL"]);
  assert.ok(desk.gaps.every((gap) => gap.state === "withheld: metadata only" && !("value" in gap)), "a gap never carries a value");
  assert.equal(desk.latestObservation, "2026-11-02");
});

test("every shown row is dated, sourced and either redistributable or the server's qualified spread", () => {
  const desk = usdAtlasDesk(us());
  const rows = desk.sections.flatMap((item) => item.metrics);
  assert.equal(rows.length, 38);
  for (const row of rows) {
    assert.match(row.asof, /^\d{4}-\d{2}-\d{2}$/, row.id);
    assert.ok(row.source, row.id);
    assert.equal(row.availability, "AVAILABLE", row.id);
    assert.ok(row.redistribution_status === "allowed" || row.formula_version === "mm.atlas.policy-spread.v1", row.id);
  }
  const sofr = section(desk, "secured_repo").metrics.find((row) => row.id === "US.NYFED.SOFR_MEDIAN");
  assert.equal(sofr.value, 3.94);
  assert.equal(sofr.asof, "2026-10-29");
  assert.equal(sofr.source, "nyfed_rates");
  assert.deepEqual(plain(ids(section(desk, "policy_corridor").metrics)), ["US.FED.IORB_DIRECT", "US.NYFED.SOFR_MEDIAN_MINUS_US.FED.IORB_DIRECT"]);
  assert.deepEqual(plain(desk.sources.map((source) => [source.source, source.host, source.rows])), [
    ["fed_prates", "www.federalreserve.gov", 1], ["fiscaldata", "fiscaldata.treasury.gov", 1],
    ["nyfed_facilities", "www.newyorkfed.org", 1], ["nyfed_rates", "markets.newyorkfed.org", 22],
    ["nyfed_unsecured_rates", "markets.newyorkfed.org", 12], ["nyfed_rates + fed_prates", "markets.newyorkfed.org", 1],
  ]);
});

test("dollar stocks convert from millions to billions exactly once, history included", () => {
  const cash = section(usdAtlasDesk(us()), "cash_facilities").metrics;
  const tga = cash.find((row) => row.id === "US.TREASURY.GOVERNMENT_CASH");
  assert.equal(tga.value, 1015.632);
  assert.equal(tga.unit, "$B");
  assert.equal(tga.change_1_observation, -36.898);
  assert.equal(tga.change_unit, "$B");
  assert.equal(tga.change_vol_unit, "$B/year^0.5");
  assert.equal(tga.percentile_3y, 99.3, "ranks are scale-free and unchanged");
  assert.equal(tga.history.at(-1)[1], 1015.632);
  const srf = cash.find((row) => row.id === "US.NYFED.SRF_TAKEUP");
  assert.equal(srf.value, 0, "zero take-up is an observation, not a gap");
  const rate = { id: "x", unit: "%", value: 3.9 };
  assert.equal(usdDisplayMetric(rate), rate);
});

test("a withheld, undated or unpermitted row never reaches a section", () => {
  const market = us();
  const sofr = market.metrics.find((row) => row.id === "US.NYFED.SOFR_MEDIAN");
  sofr.redistribution_status = "metadata_only";
  market.metrics.find((row) => row.id === "US.NYFED.TGCR_MEDIAN").asof = "2026-02-30";
  market.metrics.find((row) => row.id === "US.NYFED.BGCR_MEDIAN").value = null;
  const desk = usdAtlasDesk(market);
  const shown = new Set(ids(desk.sections.flatMap((item) => item.metrics)));
  for (const id of ["US.NYFED.SOFR_MEDIAN", "US.NYFED.TGCR_MEDIAN", "US.NYFED.BGCR_MEDIAN"]) {
    assert.equal(shown.has(id), false, id);
    assert.ok(desk.gaps.some((gap) => gap.id === id), id);
  }
  assert.equal(desk.gaps.find((gap) => gap.id === "US.NYFED.SOFR_MEDIAN").state, "withheld: metadata only");
  assert.equal(desk.gaps.find((gap) => gap.id === "US.NYFED.BGCR_MEDIAN").state, "no dated public value");
  // Without a public SOFR leg the server's spread no longer qualifies either.
  assert.equal(shown.has("US.NYFED.SOFR_MEDIAN_MINUS_US.FED.IORB_DIRECT"), false);
  assert.equal(desk.publicCount, 34);
  assert.equal(publicUsdObservation({ ...sofr, redistribution_status: "allowed" }), true);
});

test("the spread joins the desk only on its exact lineage date", () => {
  const market = us();
  market.policy_relative_spread.input_lineage[1].event_time = "2026-10-30T00:00:00+00:00";
  const corridor = section(usdAtlasDesk(market), "policy_corridor");
  assert.deepEqual(plain(ids(corridor.metrics)), ["US.FED.IORB_DIRECT"]);
});

test("unclassified public rows stay visible instead of disappearing", () => {
  const market = us();
  market.metrics.push({ ...market.metrics.find((row) => row.id === "US.NYFED.SOFR_MEDIAN"), id: "US.NYFED.NEW_SERIES", label: "NEW SERIES" });
  const other = section(usdAtlasDesk(market), "other_public");
  assert.deepEqual(plain(ids(other.metrics)), ["US.NYFED.NEW_SERIES"]);
  assert.equal(usdAtlasDesk({ ...us(), market_id: "EA-EUR" }), null);
  assert.equal(usdAtlasDesk({ market_id: "US-USD", metrics: [] }), null);
});

test("chart panes keep one unit per axis and give each dollar stock its own pane", () => {
  const desk = usdAtlasDesk(us());
  const secured = usdChartPanes(section(desk, "secured_repo").metrics);
  assert.deepEqual(plain(secured.map((pane) => [pane.unit, [...pane.labels]])), [["%", ["SOFR MEDIAN", "TGCR MEDIAN", "BGCR MEDIAN"]]]);
  assert.equal(secured[0].rows.length, 12);
  assert.ok(secured[0].rows.every((row) => row.length === 4));
  const corridor = usdChartPanes(section(desk, "policy_corridor").metrics);
  assert.deepEqual(plain(corridor.map((pane) => pane.unit)), ["%", "bp"]);
  const cash = usdChartPanes(section(desk, "cash_facilities").metrics);
  assert.deepEqual(plain(cash.map((pane) => [pane.unit, [...pane.labels]])), [["$B", ["TGA DAILY"]], ["$B", ["SRF ACCEPTED"]]]);
  assert.equal(cash[0].rows.at(-1)[1], 1015.632);
});
