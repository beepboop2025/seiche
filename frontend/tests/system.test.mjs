import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const source = await readFile(new URL("../src/tabs/System.tsx", import.meta.url), "utf8");
const context = { exports: {}, require(name) {
  if (name === "react/jsx-runtime") return jsxRuntime;
  if (name === "../lib") return { fmt: (value, digits) => value.toFixed(digits) };
  throw new Error(`Unexpected dependency: ${name}`);
} };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText, context);
const render = snap => renderToStaticMarkup(React.createElement(context.exports.default, { snap, live: true }));

test("missing public data is explicit, never a fake empty log or endless loading", () => {
  const html = render({});
  assert.match(html, /Alert history is unavailable/);
  assert.match(html, /Weights and thresholds are unavailable/);
  assert.doesNotMatch(html, /loading|run .?seiche alert|No alert rules triggered/);
});

test("configuration and actual alert history render directly from the board snapshot", () => {
  const html = render({ system: {
    editorial_config: { composite_weights: { tails: 0.17 }, regimes: [{ below: 25, name: "CALM" }] },
    alert_log: { status: "ready", evaluated_at: "2026-10-26T16:30:00Z",
      alerts: [{ fired_at: "2026-10-26T16:00:00Z", rule: "regime_change", state: "STRAIN", message: "regime is STRAIN" }] },
  } });
  assert.match(html, /tails/);
  assert.match(html, /0.17/);
  assert.match(html, /CALM&lt;25/);
  assert.match(html, /regime is STRAIN/);
  assert.match(html, /2026-10-26 16:30 UTC/);
});

test("successful empty history differs from missing history", () => {
  const html = render({ system: { alert_log: { status: "ready", alerts: [], evaluated_at: "2026-10-26T16:30:00Z" } } });
  assert.match(html, /No alert rules triggered/);
  assert.doesNotMatch(html, /Alert history is unavailable/);
});

test("missing freshness never defaults to fresh and aggregate dates stay explicit", () => {
  const html = render({ provenance: [
    { mnemonic: "MISSING", availability: "unavailable" },
    { mnemonic: "TABLE", availability: "available", fetched_at: "2026-10-26T16:00:00Z" },
    { mnemonic: "OLD", availability: "available", asof: "2026-10-13", staleness: "stale" },
  ] });
  assert.match(html, /unknown/);
  assert.match(html, /Fetch clock only/);
  assert.match(html, /Multiple source dates/);
  assert.match(html, /2026-10-13/);
  assert.match(html, /chip stale/);
  assert.doesNotMatch(html, /chip fresh/);
});

test("faults identify each failed series and distinguish retained observations", () => {
  const html = render({ faults: [
    { source: "fred", series: "SOFR", detail: "collector failed", cache_used: true },
    { source: "fred", series: "IORB", detail: "collector failed", cache_used: false },
  ] });
  assert.match(html, /fred \/ SOFR/);
  assert.match(html, /fred \/ IORB/);
  assert.match(html, /Cached observations retained/);
  assert.match(html, /No usable cached observations/);
});
