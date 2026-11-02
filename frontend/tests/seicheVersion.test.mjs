// The homepage names the running Seiche release from /api/health and carries no
// hand-maintained package version numbers.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const transpile = async (path, context, jsx = false) => {
  const compilerOptions = { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 };
  if (jsx) compilerOptions.jsx = ts.JsxEmit.ReactJSX;
  vm.runInNewContext(ts.transpileModule(await readFile(new URL(path, import.meta.url), "utf8"), { compilerOptions }).outputText, context);
  return context.exports;
};
const { releaseVersion } = await transpile("../src/seicheVersion.ts", { exports: {} });
const homeSource = await readFile(new URL("../src/ProductHome.tsx", import.meta.url), "utf8");
const upgradesBlock = homeSource.match(/CONNECTED-UPGRADES:START[\s\S]*CONNECTED-UPGRADES:END/)?.[0] ?? "";

/**
 * Load ProductHome with a minimal hook runtime: effects run on demand, state is
 * kept between renders, and fetch answers like the live /api/health route.
 */
async function loadHome(fetchHealth) {
  const state = [];
  const effects = [];
  let cursor = 0;
  const hooks = {
    ...React,
    useState(initial) {
      const index = cursor++;
      if (!(index in state)) state[index] = initial;
      return [state[index], (next) => { state[index] = next; }];
    },
    useEffect(effect) { effects.push(effect); },
  };
  const requests = [];
  const home = await transpile("../src/ProductHome.tsx", {
    exports: {}, URL, AbortController, Promise,
    window: { setTimeout, clearTimeout },
    fetch: (url, init) => { requests.push({ url, init }); return fetchHealth(); },
    require(name) {
      if (name === "react") return hooks;
      if (name === "react/jsx-runtime") return jsxRuntime;
      if (name === "./apiBase") return { API_BASE: "https://api.seiche.info" };
      if (name === "./seicheVersion") return { releaseVersion };
      if (name === "./FundingPreview" || name === "./EditorialPreview") return { default: () => null };
      if (name.endsWith(".css")) return {};
      throw new Error("Unexpected ProductHome dependency: " + name);
    },
  }, true);
  const render = () => { cursor = 0; return renderToStaticMarkup(React.createElement(home.ConnectedUpgrades)); };
  const settle = async () => {
    effects.splice(0).forEach((effect) => effect());
    for (let tick = 0; tick < 5; tick += 1) await new Promise((resolve) => setImmediate(resolve));
  };
  return { render, settle, requests };
}

const healthResponse = (body, ok = true) => async () => ({
  ok, headers: { get: () => "application/json" }, json: async () => body,
});

test("the release comes from the health version field, ignoring its codename", () => {
  // Saved live /api/health value on 2026-11-02.
  assert.equal(releaseVersion({ version: "0.16.11 estuary", generated_at: "2026-11-02T09:25:05+00:00" }), "0.16.11");
  assert.equal(releaseVersion({ version: "0.17.0" }), "0.17.0");
  for (const value of [null, [], {}, { version: 16 }, { version: "estuary" }, { version: "v0.16.11" }, { version: "0.16" }, { version: "0.16.11-rc1" }]) {
    assert.equal(releaseVersion(value), null, JSON.stringify(value));
  }
});

test("the connected upgrades block carries no hand-maintained version number", () => {
  assert.ok(upgradesBlock.length > 1000, "the generated upgrades block is present");
  assert.doesNotMatch(upgradesBlock, /\b\d+\.\d+\.\d+\b/);
  assert.doesNotMatch(upgradesBlock, /0\.16\.5|0\.1\.13|0\.1\.2|0\.4\.0|0\.4\.2/);
  assert.match(upgradesBlock, /the Financial Evidence Python package or the AI SDK/);
  assert.match(upgradesBlock, /the hosted NoiseFloor workbench\. The NoiseFloor Python and MCP packages add clearer input guidance/);
});

test("the homepage shows the live release once /api/health answers", async () => {
  const home = await loadHome(healthResponse({ version: "0.16.11 estuary" }));
  assert.match(home.render(), /<p>Seiche provides compact funding context/);
  await home.settle();
  assert.equal(home.requests.length, 1);
  assert.equal(home.requests[0].url, "https://api.seiche.info/api/health");
  assert.equal(home.requests[0].init.credentials, "omit");
  const html = home.render();
  assert.match(html, /<p>Seiche 0\.16\.11 provides compact funding context/);
  assert.equal(html.match(/\b\d+\.\d+\.\d+\b/g)?.length, 1, "the live release is the only version number shown");
});

test("an unreadable health response leaves the name without any version", async () => {
  for (const fetchHealth of [
    healthResponse({ version: "estuary" }),
    healthResponse({ version: "0.16.11 estuary" }, false),
    async () => { throw new TypeError("network down"); },
  ]) {
    const home = await loadHome(fetchHealth);
    await home.settle();
    const html = home.render();
    assert.match(html, /<p>Seiche provides compact funding context/);
    assert.doesNotMatch(html, /\b\d+\.\d+\.\d+\b/);
  }
});
