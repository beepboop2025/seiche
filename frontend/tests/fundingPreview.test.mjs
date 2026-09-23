import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/fundingSnapshot.ts", import.meta.url), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const context = { exports: {} };
vm.runInNewContext(output.outputText, context);
const { fundingPreview } = context.exports;

test("published readings preserve units, separate clocks and a real zero", () => {
  const result = fundingPreview({ generated_at: "2026-09-23T18:52:20+00:00", faults: [], headline: {
    sofr_pct: { value: 3.87, asof: "2026-09-22" }, reserves_b: { value: 3013.794, asof: "2026-09-16" }, srf_accepted_b: { value: 0, asof: "2026-09-23" },
  } });
  assert.equal(result.generatedAt, "2026-09-23T18:52:20+00:00");
  assert.equal(result.observations[0].unit, "%");
  assert.equal(result.observations[1].value, 3013.794);
  assert.equal(result.observations[1].unit, "USD bn");
  assert.equal(result.observations[1].observedAt, "2026-09-16");
  assert.equal(result.observations[2].value, 0);
});

test("missing, non-finite and string values cannot become observed numbers", () => {
  for (const value of [null, undefined, "0", "3.87", Infinity, NaN]) {
    const result = fundingPreview({ headline: { sofr_pct: { value, asof: "unknown" } } });
    assert.equal(result.observations[0].value, null);
    assert.equal(result.observations[0].observedAt, null);
    assert.equal(result.observations[1].value, null);
    assert.equal(result.generatedAt, null);
    assert.equal(result.faultsReported, null);
  }
});

test("a malformed envelope cannot silently produce a healthy preview", () => {
  for (const input of [null, [], {}, { headline: [] }]) assert.throws(() => fundingPreview(input));
  assert.equal(fundingPreview({ headline: {}, faults: ["source unavailable"] }).faultsReported, 1);
});
