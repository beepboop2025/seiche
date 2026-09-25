import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const compile = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, {
  compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022},
}).outputText).toString("base64")}`;
const core = compile(await readFile(new URL("../src/research/core.ts", import.meta.url), "utf8"));
const source = (await readFile(new URL("../src/research/seriesModel.ts", import.meta.url), "utf8"))
  .replace('from "./core"', `from "${core}"`);
const {seriesModel} = await import(compile(source));
const entry = {available: true, csv_restricted: false, mnemonic: "SOFR", unit: "%", json: "/api/series/SOFR",
  label: "SOFR", cadence: "Daily", native_lag: "One business day"};
const payload = {provenance: {mnemonic: "SOFR", unit: "%", asof: "2026-09-23", staleness: "fresh",
  source: "fred", remote_id: "SOFR", fetched_at: "2026-09-25T01:00:00Z"},
  points: [["2026-09-22", 0], ["2026-09-23", 4.2]]};

test("economic observations keep their source date, units and a valid zero", () => {
  const model = seriesModel(payload, entry);
  assert.equal(model.asOf, "2026-09-23");
  assert.equal(model.unit, "%");
  assert.deepEqual(model.points[0], {date: "2026-09-22", value: 0});
});

test("restricted, unavailable or mismatched catalog entries cannot populate charts", () => {
  for (const change of [{available: false}, {csv_restricted: true}, {unit: "bp"}, {mnemonic: "DGS2"}, {json: null}]) {
    assert.throws(() => seriesModel(payload, {...entry, ...change}));
  }
});

test("invalid numbers, dates, ordering and missing latest observations are rejected", () => {
  for (const points of [[], [["2026-09-23", null]], [["2026-09-23", "4.2"]],
    [["2026-09-23", Infinity]], [["2026-02-30", 4.2]], [["2026-09-22", 4.2]],
    [["2026-09-24", 4.2]], [["2026-09-23", 4.2], ["2026-09-23", 4.3]],
    [["2026-09-23", 4.2], ["2026-09-22", 4.3]]]) {
    assert.throws(() => seriesModel({...payload, points}, entry));
  }
});

test("malformed or excessive histories stay outside the presentation model", () => {
  for (const value of [null, {}, {provenance: null}, {...payload, points: Array(2001).fill(["2026-09-23", 4.2])}]) {
    assert.throws(() => seriesModel(value, entry));
  }
});
