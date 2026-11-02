// Loads MoneyMarkets.tsx and its evidence helpers in a VM, with a Chart stub that
// records what it was asked to draw. Shared by the blank-data regression tests.
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";

const src = (path) => new URL(`../src/${path}`, import.meta.url);

async function load(path, requireMap = {}, jsx = false) {
  const context = {
    exports: {}, URL, console,
    require(name) {
      if (name === "react") return React;
      if (name === "react/jsx-runtime") return jsxRuntime;
      if (name.endsWith(".css")) return {};
      if (name in requireMap) return requireMap[name];
      throw new Error(`Unexpected dependency of ${path}: ${name}`);
    },
  };
  const compilerOptions = { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 };
  if (jsx) compilerOptions.jsx = ts.JsxEmit.ReactJSX;
  vm.runInNewContext(ts.transpileModule(await readFile(src(path), "utf8"), { compilerOptions }).outputText, context);
  return context.exports;
}

export const fixture = async (name) => JSON.parse(await readFile(new URL(name, import.meta.url), "utf8"));

/** A Chart stand-in that exposes its rows, series, source and note as data attributes. */
export function ChartStub(props) {
  return React.createElement("figure", {
    "data-chart": props.yLabel,
    "data-rows": String(props.rows.length),
    "data-first": String(props.rows[0]?.[0] ?? ""),
    "data-last": String(props.rows[props.rows.length - 1]?.[0] ?? ""),
    "data-series": props.series.map((series) => series.label).join("|"),
    "data-source": props.source,
    "data-note": props.note,
  });
}

export async function loadMoneyMarkets() {
  const funding = await load("usdFunding.ts");
  const ecb = await load("ecbReference.ts");
  const usdDesk = await load("usdAtlasDesk.ts", { "./usdFunding": funding });
  const snapshot = await load("snapshotStatus.ts");
  const summary = await load("UsdFundingSummary.tsx", { "./apiBase": { API_BASE: "https://example.invalid" }, "./usdFunding": funding }, true);
  const moneyMarkets = await load("tabs/MoneyMarkets.tsx", {
    "../snapshotStatus": snapshot,
    "../UsdFundingSummary": summary,
    "../ecbReference": ecb,
    "../usdAtlasDesk": usdDesk,
    "../apiBase": { API_BASE: "https://example.invalid" },
    "../auth": { authHeaders: () => ({}) },
    "../palette": { P: {} },
    "../shareRoutes": { moneyMarketSharePath: (id) => `/markets/${id}` },
    "../moneyMarketWatchlist": { MAX_WATCHED_MARKETS: 6, filterWatchedMarkets: (markets) => markets, missingWatchedMarkets: () => [] },
    "../useMoneyMarketWatchlist": { useMoneyMarketWatchlist: () => ({ ids: [], notice: "", remembered: false }) },
    "../Chart": { default: ChartStub },
    "./IndiaFunding": { default: () => null },
    "./CountryFunding": { default: () => null },
  }, true);
  return { moneyMarkets, ecb, usdDesk, funding };
}
