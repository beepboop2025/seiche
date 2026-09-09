import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
import { payload, chinaRow } from "./fixtures/workbench.mjs";

const source = await readFile(new URL("../src/marketWorkbench.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
const model = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);
const selection = { base: "USD", quote: "CNY", days: 365, provider: "h10" };

test("normalization preserves missing changes, date clocks and complete raw evidence", () => {
  const raw = payload();
  const data = model.normalizeWorkbench(raw, selection);
  assert.equal(data.forex.rows.find((row) => row.quote_currency === "CNY").change_60obs_pct, null);
  assert.equal(data.china.history[0].period_end, "2024-12-31");
  assert.equal(data.china.history[0].accepted_at, "2026-09-08T00:00:00Z");
  assert.equal(data.raw, raw);
  assert.equal(data.china.history[0].raw, raw.china.history[0]);
});

test("responses cannot cross pair, provider or history-window selections", () => {
  for (const override of [{ base: "EUR" }, { quote: "EUR" }, { days: 90 }, { provider: "ecb" }]) {
    assert.throws(() => model.normalizeWorkbench(payload(override), selection), /does not match/);
  }
  const raw = payload(); raw.forex.reference_only = false;
  assert.throws(() => model.normalizeWorkbench(raw, selection), /reference-rate convention/);
});

test("currency table rejects duplicate quotes and inconsistent base orientation", () => {
  const duplicate = payload(); duplicate.forex.rows.push(duplicate.forex.rows[0]);
  assert.throws(() => model.normalizeWorkbench(duplicate, selection), /Ambiguous currency/);
  const mixed = payload(); mixed.forex.rows[0].base_currency = "EUR";
  assert.throws(() => model.normalizeWorkbench(mixed, selection), /Currency label|Ambiguous currency/);
});

test("empty and invalid values cannot become zero or valid reference observations", () => {
  assert.equal(model.finiteValue(null), null);
  assert.equal(model.finiteValue("0"), 0);
  assert.equal(model.finiteValue("-3.25"), -3.25);
  for (const value of ["", " ", "NaN", "Infinity", true, [], Infinity, NaN]) assert.throws(() => model.finiteValue(value));
  for (const value of [null, 0, -2, "", Infinity]) {
    const raw = payload(); raw.forex.history[0].value = value;
    assert.throws(() => model.normalizeWorkbench(raw, selection));
  }
  for (const overrides of [{ value: -1 }, { as_of: null }, { pair: "EUR/JPY" }, { unit: "USD per CNY" }]) {
    const raw = payload(); Object.assign(raw.forex.rows[0], overrides);
    assert.throws(() => model.normalizeWorkbench(raw, selection));
  }
});

test("duplicate or invalid observation dates cannot draw an ambiguous FX chart", () => {
  const duplicate = payload(); duplicate.forex.history.push(duplicate.forex.history[0]);
  assert.throws(() => model.normalizeWorkbench(duplicate, selection), /Duplicate FX/);
  const invalid = payload(); invalid.forex.history[0].date = "2026-02-30";
  assert.throws(() => model.normalizeWorkbench(invalid, selection), /Invalid observation date/);
});

test("China history cannot mix indicators, annual periods or measurement units", () => {
  for (const overrides of [{ series_id: "cn.wdi.other" }, { period_end: "2025-12-31" }, { unit: "USD" }]) {
    const raw = payload(); Object.assign(raw.china.history[0], overrides);
    assert.throws(() => model.normalizeWorkbench(raw, selection), /Ambiguous China/);
  }
  const raw = payload(); raw.china.economic_context = {};
  assert.throws(() => model.normalizeWorkbench(raw, selection), /structural evidence boundary/);
});

test("empty China acceptance remains unavailable without erasing valid FX", () => {
  const raw = payload(); raw.china = { ...raw.china, status: "unavailable", economic_context: {}, series: [], history: [], selected_series: null };
  const data = model.normalizeWorkbench(raw, selection);
  assert.equal(data.china.economic_context, null);
  assert.equal(data.forex.history.length, 3);
});

test("plot geometry follows actual elapsed dates and handles a constant or single point", () => {
  const plot = model.seriesPlot([{ date: "2026-09-05", value: 7 }, { date: "2026-09-01", value: 7 }, { date: "2026-09-02", value: 7 }]);
  assert.deepEqual(plot.points.map((point) => point.date), ["2026-09-01", "2026-09-02", "2026-09-05"]);
  assert.equal(plot.points[1].x - plot.points[0].x, 170);
  assert.equal(plot.points[2].x - plot.points[1].x, 510);
  assert.equal(plot.points[0].y, plot.points[2].y);
  assert.doesNotMatch(plot.path, /NaN|Infinity/);
  assert.equal(model.seriesPlot([{ date: "2026-09-01", value: 7 }]).points[0].x, 420);
  assert.equal(model.seriesPlot([]), null);
});

test("FX history exports include quote convention, provider and all source clocks", () => {
  const csv = model.fxHistoryCsv(model.normalizeWorkbench(payload(), selection));
  assert.match(csv, /base_currency,quote_currency,provider,unit,evidence_status,generated_at,sources,quote_convention/);
  assert.match(csv, /2026-09-08,7.2,USD,CNY,h10,CNY per USD,observed/);
  assert.match(csv, /DEXCHUS/);
  assert.match(csv, /2026-09-09T01:00:00Z/);
  assert.match(csv, /quote currency units per one base currency/);
});

test("China CSV preserves revisions and provenance while neutralizing spreadsheet formulas", () => {
  const raw = payload(); raw.china.history[0] = chinaRow({ period_start: "2024-01-01", period_end: "2024-12-31", value: -2, source_id: "=HYPERLINK(\"https://bad.test\")" });
  const csv = model.chinaHistoryCsv(model.normalizeWorkbench(raw, selection));
  assert.match(csv, /2024-12-31,-2,annual %/);
  assert.match(csv, /'=HYPERLINK/);
  assert.match(csv, /released_at,collected_at,accepted_at/);
  assert.match(csv, /FM.LBL.BMNY.ZG/);
});

test("source links reject script URLs and embedded credentials", () => {
  assert.equal(model.safeEvidenceUrl("javascript:alert(1)"), null);
  assert.equal(model.safeEvidenceUrl("https://user:secret@example.test"), null);
  assert.equal(model.safeEvidenceUrl("https://fred.stlouisfed.org/series/DEXCHUS"), "https://fred.stlouisfed.org/series/DEXCHUS");
});

test("currency search matches pair, status and source identifiers", () => {
  const data = model.normalizeWorkbench(payload(), selection);
  assert.equal(model.filterFxRows(data.forex.rows, "CNY fresh").length, 1);
  assert.equal(model.filterFxRows(data.forex.rows, "DEXCHUS").length, 2);
  assert.equal(model.filterFxRows(data.forex.rows, "no-such-pair").length, 0);
});
