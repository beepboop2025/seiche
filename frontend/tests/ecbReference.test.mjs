import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(await readFile(new URL("../src/ecbReference.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, context);
const { readEcbUsdReferences, transmissionInputs, signedPercent, currencyDirection, ECB_USD_CNY_QUERY } = context.exports;

const live = JSON.parse(await readFile(new URL("./ecbWorkbench.live.json", import.meta.url), "utf8"));
const atlas = JSON.parse(await readFile(new URL("./moneyMarketsAtlas.live.json", import.meta.url), "utf8"));
const copy = (value) => JSON.parse(JSON.stringify(value));
const cnyRow = (payload) => payload.forex.rows.find((row) => row.pair === "USD/CNY");

test("the China desk requests the default ECB USD/CNY selection", () => {
  assert.equal(ECB_USD_CNY_QUERY, "base=USD&quote=CNY&days=365&provider=ecb");
  assert.deepEqual({ ...live.selection, china_series: undefined }, { provider: "ecb", base: "USD", quote: "CNY", days: 365, china_series: undefined });
});

test("the saved live workbench yields the ECB CNY leg with its own date, windows and history", () => {
  const { cny } = readEcbUsdReferences(live);
  assert.equal(cny.pair, "USD/CNY");
  assert.equal(cny.unit, "CNY per USD");
  assert.equal(cny.value, 6.652882205513785);
  assert.equal(cny.asof, "2026-10-30");
  assert.equal(cny.status, "fresh");
  assert.equal(cny.observations, 254);
  assert.equal(cny.publisher, "European Central Bank");
  assert.equal(signedPercent(cny.change60), "-1.40%");
  assert.equal(cny.vol20.toFixed(2), "0.69");
  // Sixty observations span 84 calendar days here: the label must name the observations.
  assert.equal(cny.change60From, "2026-08-07");
  assert.equal(cny.window20From, "2026-10-02");
  assert.equal(cny.history.length, 254);
  assert.deepEqual([...cny.history[0]], ["2025-11-03", 7.120635747785306]);
  assert.deepEqual([...cny.history.at(-1)], ["2026-10-30", 6.652882205513785]);
});

test("every USD cross keeps its quote convention, and only rights-cleared rows survive", () => {
  const { crosses } = readEcbUsdReferences(live);
  assert.deepEqual(crosses.map((row) => row.quote).sort(), ["CNY", "EUR", "GBP", "JPY", "KRW"]);
  const eur = crosses.find((row) => row.quote === "EUR");
  assert.equal(eur.unit, "EUR per USD");
  assert.equal(signedPercent(eur.change60), "+3.25%");
  assert.equal(currencyDirection("EUR", eur.change60), "EUR weaker against USD");
  assert.equal(currencyDirection("CNY", -1.4), "CNY stronger against USD");
  assert.equal(currencyDirection("HKD", 0), "HKD unchanged against USD");

  const restricted = copy(live);
  cnyRow(restricted).sources[1].redistribution_status = "metadata_only";
  const result = readEcbUsdReferences(restricted);
  assert.equal(result.cny, null, "one leg without permission withholds the cross");
  assert.equal(result.crosses.some((row) => row.quote === "CNY"), false);
});

test("a withheld H.10 selection, another schema or a non-reference response is refused", () => {
  for (const mutate of [
    (payload) => { payload.selection.provider = "h10"; },
    (payload) => { payload.schema = "seiche.market-workbench.v2"; },
    (payload) => { payload.forex.reference_only = false; },
    (payload) => { payload.selection.base = "EUR"; },
  ]) {
    const payload = copy(live);
    mutate(payload);
    assert.equal(readEcbUsdReferences(payload), null);
  }
  assert.equal(readEcbUsdReferences(null), null);
});

test("history that does not end on the latest observation is not charted, and windows lose their dates", () => {
  const shifted = copy(live);
  shifted.forex.history.pop();
  const { cny } = readEcbUsdReferences(shifted);
  assert.equal(cny.value, 6.652882205513785, "the dated latest value remains");
  assert.equal(cny.history.length, 0);
  assert.equal(cny.change60From, null);
  assert.equal(cny.window20From, null);

  const disagreeing = copy(live);
  cnyRow(disagreeing).change_60obs_pct = -2;
  assert.equal(readEcbUsdReferences(disagreeing).cny.change60From, null, "a window date is shown only when the history reproduces the change");
});

test("malformed, duplicated or mislabelled rows stay gaps instead of guesses", () => {
  for (const mutate of [
    (row) => { row.value = 0; },
    (row) => { row.value = "6.65"; },
    (row) => { row.as_of = "2026-02-30"; },
    (row) => { row.unit = "USD per CNY"; },
    (row) => { row.sources = []; },
  ]) {
    const payload = copy(live);
    mutate(cnyRow(payload));
    assert.equal(readEcbUsdReferences(payload).cny, null);
  }
  const duplicated = copy(live);
  duplicated.forex.rows.push(copy(cnyRow(duplicated)));
  assert.equal(readEcbUsdReferences(duplicated).cny, null);
  const badHistory = copy(live);
  badHistory.forex.history[3].value = -1;
  assert.equal(readEcbUsdReferences(badHistory).cny.history.length, 0);
});

test("transmission inputs pair each market's public local rate with its ECB cross, never a withheld value", () => {
  const { crosses } = readEcbUsdReferences(live);
  const rows = transmissionInputs(atlas.markets, crosses);
  assert.deepEqual(rows.map((row) => row.marketId), ["EA-EUR", "UK-GBP", "TW-TWD"], "USD is the reference and China has its own desk");
  const [euro, sterling, taiwan] = rows;
  assert.equal(euro.localRate.id, "EA.ECB.ESTR");
  assert.equal(euro.cross.quote, "EUR");
  // SONIA is derived-only, so sterling falls back to its public policy rate.
  assert.equal(sterling.localRate.id, "GB.BOE.BANK_RATE");
  assert.equal(taiwan.cross, null);
  assert.equal(taiwan.localRate.asof, "2026-09-30");

  const restricted = copy(atlas.markets);
  restricted.find((market) => market.market_id === "EA-EUR").benchmark.redistribution_status = "metadata_only";
  assert.equal(transmissionInputs(restricted, crosses).find((row) => row.marketId === "EA-EUR").localRate.id, "EA.ECB.MRO");
});
