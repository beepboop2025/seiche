import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const source = await readFile(new URL("../src/snapshotStatus.ts", import.meta.url), "utf8");
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, context);
const { moneyMarketDisplay, apiCheckStatus } = context.exports;

const fundingContext = { exports: {} };
vm.runInNewContext(ts.transpileModule(await readFile(new URL("../src/usdFunding.ts", import.meta.url), "utf8"), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, fundingContext);
const summaryContext = {exports: {}, require(name) {
  if (name === "react") return React;
  if (name === "react/jsx-runtime") return jsxRuntime;
  if (name === "./apiBase") return {API_BASE: "https://example.invalid"};
  if (name === "./usdFunding") return fundingContext.exports;
  if (name.endsWith(".css")) return {};
  throw new Error("Unexpected funding summary dependency: " + name);
}};
vm.runInNewContext(ts.transpileModule(await readFile(new URL("../src/UsdFundingSummary.tsx", import.meta.url), "utf8"), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX},
}).outputText, summaryContext);

const moneyMarketsSource = await readFile(new URL("../src/tabs/MoneyMarkets.tsx", import.meta.url), "utf8");
const moneyMarketsContext = { exports: {}, URL, console, require(name) {
  if (name === "react") return React;
  if (name === "react/jsx-runtime") return jsxRuntime;
  if (name === "../snapshotStatus") return context.exports;
  if (name === "../UsdFundingSummary") return summaryContext.exports;
  if (name === "../apiBase") return { API_BASE: "https://example.invalid" };
  if (name === "../auth") return { authHeaders: () => ({}) };
  if (name === "../palette") return { P: {} };
  if (name === "../shareRoutes") return { moneyMarketSharePath: id => `/markets/${id}` };
  if (name === "../moneyMarketWatchlist") return { MAX_WATCHED_MARKETS: 6, filterWatchedMarkets: markets => markets, missingWatchedMarkets: () => [] };
  if (name === "../useMoneyMarketWatchlist") return { useMoneyMarketWatchlist: () => ({ ids: [], notice: "", remembered: false }) };
  if (["../Chart", "./IndiaFunding", "./CountryFunding"].includes(name)) return { default: () => null };
  if (name.endsWith(".css")) return {};
  throw new Error(`Unexpected MoneyMarkets dependency: ${name}`);
} };
vm.runInNewContext(ts.transpileModule(moneyMarketsSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText, moneyMarketsContext);

test("MoneyMarkets renders the USD evidence on its first paint while the global request is pending", () => {
  const html = renderToStaticMarkup(React.createElement(moneyMarketsContext.exports.default, { snap: {
    generated_at: "2026-10-21T19:00:00Z",
    engines: { money_market: { asof: "2026-10-21", plain_language: "Dated USD funding evidence", sections: [{
      id: "policy", label: "Policy rates", metrics: [{ id: "policy.sofr", label: "SOFR", value: 3.5, unit: "%", asof: "2026-10-20", status: "available" }],
    }] } },
  } }));
  assert.match(html, /Refreshing the global atlas/);
  assert.match(html, /Dated USD funding evidence/);
  assert.match(html, /USD funding/);
  assert.match(html, /3.50%/);
  assert.match(html, /As of 2026-10-20/);
  assert.match(html, /Reserve balances/);
  assert.doesNotMatch(html, /Official observations remain blank/);
  assert.doesNotMatch(html, /Global atlas unavailable/);
});

test("the first render shows a dated USD fallback before an atlas request completes", () => {
  const fallback = { generated_at: "2026-10-21T19:00:00Z", markets: [{ market_id: "US-USD", benchmark: { asof: "2026-10-20", value: 0 } }] };
  const display = moneyMarketDisplay(null, fallback);
  assert.equal(display.atlas, fallback);
  assert.equal(display.mode, "usd-fallback");
  assert.equal(display.atlas.markets[0].benchmark.value, 0);
  assert.equal(display.atlas.markets[0].benchmark.asof, "2026-10-20");
});

test("global success takes precedence, and failed refresh uses the newest fallback without inventing coverage", () => {
  const oldFallback = { generated_at: "2026-10-20", markets: [{ market_id: "US-USD" }] };
  const newFallback = { generated_at: "2026-10-21", markets: [{ market_id: "US-USD" }] };
  const global = { generated_at: "2026-10-22", markets: [{ market_id: "US-USD" }, { market_id: "IN-INR" }] };
  assert.equal(moneyMarketDisplay(global, oldFallback).atlas, global);
  assert.equal(moneyMarketDisplay(global, newFallback).mode, "live");
  assert.equal(moneyMarketDisplay(null, newFallback).atlas, newFallback);
  assert.equal(moneyMarketDisplay(null, newFallback).atlas.markets.length, 1);
  assert.equal(moneyMarketDisplay(null, null).atlas, null);
  assert.equal(moneyMarketDisplay(null, null).mode, "unavailable");
});

test("failed initial and subsequent API checks cannot be presented as pending or successful", () => {
  assert.equal(apiCheckStatus(null, null), "Refresh pending");
  assert.equal(apiCheckStatus(null, "timeout"), "Refresh failed; no successful API check");
  assert.equal(apiCheckStatus("2026-10-21T23:12:00Z", "timeout"), "Refresh failed; last success 2026-10-21 23:12 UTC");
  assert.equal(apiCheckStatus("2026-10-22T10:05:00Z", null), "Last success 2026-10-22 10:05 UTC");
});
