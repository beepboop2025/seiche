import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const modelUrl = new URL("../src/tabs/oilFundingScenario.ts", import.meta.url);
const componentUrl = new URL("../src/tabs/OilFunding.tsx", import.meta.url);
const source = await readFile(modelUrl, "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: modelUrl.pathname,
});
const model = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled.outputText).toString("base64")}`
);

test("missing and non-finite funding rates stay unavailable", () => {
  for (const value of [null, undefined, "", "   ", Number.NaN, Infinity, -Infinity]) {
    assert.equal(model.finiteOrNull(value), null);
  }
  assert.equal(model.finiteOrNull(0), 0);
  assert.equal(model.finiteOrNull("0"), 0);
  assert.equal(model.finiteOrNull(4.25), 4.25);
});

test("missing funding quarantines every rate-dependent scenario output", () => {
  const scenario = model.initialScenario({
    live: {
      wti: { price_usd_per_bbl: 80 },
      inr: { per_usd: 84 },
    },
    scenario: { assumptions: { funding_rate_pct: null } },
  });
  const output = model.calculateScenario(scenario);

  assert.equal(scenario.fundingRate, null);
  assert.equal(output.carry.financing, null);
  assert.equal(output.carry.required, null);
  assert.equal(output.carry.headroom, null);
  assert.equal(output.trade.financingCost, null);
  assert.equal(Number.isFinite(output.carry.storage), true);
  assert.equal(Number.isFinite(output.trade.cargoCredit), true);
});

test("an explicit zero funding assumption remains a real zero", () => {
  const scenario = model.initialScenario({
    live: {
      wti: { price_usd_per_bbl: 80 },
      inr: { per_usd: 84 },
    },
    scenario: { assumptions: { funding_rate_pct: 0 } },
  });
  const output = model.calculateScenario(scenario);

  assert.equal(scenario.fundingRate, 0);
  assert.equal(output.carry.financing, 0);
  assert.equal(output.trade.financingCost, 0);
  assert.equal(output.carry.required, output.carry.storage + output.carry.insurance);
});

test("OilFunding renders an explicit unavailable path instead of the old fallback", async () => {
  const component = await readFile(componentUrl, "utf8");

  assert.doesNotMatch(component, /finite\(a\.funding_rate_pct,\s*5\)/);
  assert.match(component, /value == null \? "unavailable"/);
  assert.match(component, /Funding rate unavailable/);
  assert.match(component, /funding input unavailable/);
});
