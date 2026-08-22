import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const modelUrl = new URL("../src/tabs/oilFundingScenario.ts", import.meta.url);
const componentUrl = new URL("../src/tabs/OilFunding.tsx", import.meta.url);
const rvQualityUrl = new URL("../src/rvxrayQuality.ts", import.meta.url);
const positioningUrl = new URL("../src/tabs/Positioning.tsx", import.meta.url);
const boardUrl = new URL("../src/tabs/Board.tsx", import.meta.url);
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
const rvQualitySource = await readFile(rvQualityUrl, "utf8");
const rvQualityTranspiled = ts.transpileModule(rvQualitySource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: rvQualityUrl.pathname,
});
const rvQuality = await import(
  `data:text/javascript;base64,${Buffer.from(rvQualityTranspiled.outputText).toString("base64")}`
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

test("snapshot refreshes update only untouched scenario fields", () => {
  const current = model.initialScenario({
    asof: "2026-08-20",
    scenario: { assumptions: { oil_price_usd_per_bbl: 80, funding_rate_pct: 5 } },
  });
  const refreshed = model.initialScenario({
    asof: "2026-08-21",
    scenario: { assumptions: { oil_price_usd_per_bbl: 86, funding_rate_pct: null } },
  });
  current.oilPrice = 92;

  const reconciled = model.reconcileScenarioDefaults(
    current,
    refreshed,
    new Set(["oilPrice"]),
  );

  assert.equal(reconciled.oilPrice, 92);
  assert.equal(reconciled.fundingRate, null);
  assert.equal(reconciled.tenorDays, refreshed.tenorDays);
});

test("an edited funding rate survives an unavailable server refresh and is labelled explicit", () => {
  const current = model.initialScenario({
    asof: "2026-08-20",
    scenario: { assumptions: { funding_rate_pct: 5 } },
  });
  current.fundingRate = 4.25;
  const refreshedEngine = {
    asof: "2026-08-21",
    scenario: {
      assumptions: { funding_rate_pct: null },
      funding_rate_evidence: { basis: "unavailable", asof: null },
    },
  };
  const edited = new Set(["fundingRate"]);
  const reconciled = model.reconcileScenarioDefaults(
    current,
    model.initialScenario(refreshedEngine),
    edited,
  );
  const note = model.scenarioSourceNote(
    model.scenarioSource(refreshedEngine),
    reconciled,
    edited,
  );

  assert.equal(reconciled.fundingRate, 4.25);
  assert.match(note, /2026-08-21 snapshot/);
  assert.match(note, /explicit user scenario assumption/);
  assert.doesNotMatch(note, /observed SOFR is dated/);
});

test("source notes retain the provenance paired with the current defaults", () => {
  const engine = {
    asof: "2026-08-21",
    scenario: {
      assumptions: { funding_rate_pct: 4.4 },
      funding_rate_evidence: {
        basis: "observed_sofr",
        asof: "2026-08-20",
      },
    },
  };
  const note = model.scenarioSourceNote(
    model.scenarioSource(engine),
    model.initialScenario(engine),
    new Set(),
  );

  assert.match(note, /2026-08-21 snapshot/);
  assert.match(note, /observed SOFR is dated 2026-08-20/);
});

test("OilFunding renders an explicit unavailable path instead of the old fallback", async () => {
  const component = await readFile(componentUrl, "utf8");

  assert.doesNotMatch(component, /finite\(a\.funding_rate_pct,\s*5\)/);
  assert.match(component, /value == null \? "unavailable"/);
  assert.match(component, /Funding rate unavailable/);
  assert.match(component, /funding input unavailable/);
  assert.match(component, /reconcileScenarioDefaults/);
  assert.match(component, /editedFields\.current\.add\(field\)/);
  assert.ok(
    component.indexOf("useEffect(() => {") < component.indexOf("if (!engine.ok)"),
    "OilFunding hooks must run before the live engine availability branch",
  );
});

test("RV X-Ray quality labels make partial and unavailable totals explicit", () => {
  const engine = {
    metric_coverage: {
      pair_proxy_b: { status: "partial", usable_rows: 1, total_rows: 2 },
      gross_short_b: { status: "complete", usable_rows: 2, total_rows: 2 },
      net_b: { status: "unavailable", usable_rows: 0, total_rows: 2 },
    },
  };

  assert.equal(rvQuality.rvMetricQualityLabel(engine, "pair_proxy_b"), "PARTIAL · 1/2 rows");
  assert.equal(rvQuality.rvMetricQualityLabel(engine, "gross_short_b"), null);
  assert.equal(rvQuality.rvMetricQualityLabel(engine, "net_b"), "UNAVAILABLE · 0/2 rows");
  assert.equal(
    rvQuality.rvQualityLabel({
      status: "partial",
      usable_rows: 1,
      total_rows: 2,
      coverage_unit: "expected_contracts",
    }),
    "PARTIAL · 1/2 contracts",
  );
});

test("RV X-Ray screens render coverage beside current values", async () => {
  const positioning = await readFile(positioningUrl, "utf8");
  const board = await readFile(boardUrl, "utf8");

  assert.match(positioning, /rvMetricQualityLabel\(e, "pair_proxy_b"\)/);
  assert.match(positioning, /Chart gaps mark incomplete contract coverage/);
  assert.match(positioning, /Shock outputs are withheld unless every required current aggregate/);
  assert.match(positioning, /s\.mtm_loss_b == null \? "—"/);
  assert.match(positioning, /pair_change_13w_b > 50 \? "warn"/);
  assert.match(board, /pair proxy \$\{pairQuality\}/);
  assert.match(board, /score withheld/);
});

test("Oil funding reset label describes defaults honestly", async () => {
  const component = await readFile(componentUrl, "utf8");
  assert.match(component, /RESET TO SNAPSHOT DEFAULTS/);
  assert.doesNotMatch(component, /RESET TO OBSERVED/);
});
