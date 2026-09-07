// Synthetic UI proof for the real MoneyMarkets component; no production data or personal profile.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.SEICHE_WATCHLIST_URL || "http://127.0.0.1:8198";
const output = process.env.SEICHE_WATCHLIST_ARTIFACTS || "../artifacts/research-watchlist-browser";
const key = "seiche.money-market-watch.v1", errors = [], external = [], checks = [];
let phase = "initial", requests = 0;
function atlas() {
  const rows = [
    { market_id: "US-USD", region: "Americas", currency: "USD", display_name: "United States synthetic fixture", status: "STALE_REFERENCE",
      benchmark: { id: "US.NYFED.SOFR", label: "SOFR fixture", value: 3.5, unit: "%", availability: "AVAILABLE", redistribution_status: "allowed", status: "STALE", asof: "2026-08-01" } },
    { market_id: "GB-GBP", region: "Europe", currency: "GBP", display_name: "United Kingdom synthetic fixture", status: "LIVE_REFERENCE",
      benchmark: { id: "GB.BOE.SONIA", label: "SONIA fixture", value: 4, unit: "%", availability: "AVAILABLE", redistribution_status: "allowed", status: "FRESH", asof: "2026-09-07" } },
    { market_id: "CN-CNY", region: "Asia", currency: "CNY", display_name: "China synthetic fixture", status: "DERIVED_CONTEXT",
      benchmark: null, derived_benchmark: { id: "CN.RATE", label: "Derived fixture", value: null, availability: "DERIVED_CONTEXT", redistribution_status: "derived_only", status: "FRESH", asof: "2026-09-07" } },
  ];
  return { schema: "seiche.global-money-markets.v1", ok: true, generated_at: "2026-09-08T06:00:00Z", status: "PARTIAL",
    coverage: { declared_markets: 3, live_benchmarks: 1 }, markets: rows.filter(row => phase !== "absent" || row.market_id !== "US-USD")
      .map(row => ({ ...row, metrics: [], coverage: { declared_instruments: 1, public_available: row.benchmark ? 1 : 0, coverage_pct: row.benchmark ? 100 : 0 } })) };
}
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div>
<script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
<script type="module">import React from '/node_modules/.vite/deps/react.js';import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';import MoneyMarkets from '/src/tabs/MoneyMarkets.tsx';import '/src/styles.css';ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(React.StrictMode,null,React.createElement(MoneyMarkets,{snap:{engines:{},generated_at:'2026-09-08T06:00:00Z'}})));</script></body></html>`;
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
context.on("page", page => page.on("pageerror", error => errors.push(error.message)));
await context.route("**/*", async route => {
  const url = route.request().url();
  if (url === `${base}/watchlist-qa`) return route.fulfill({ contentType: "text/html", body: html });
  if (url === `${base}/api/v2/money-markets`) {
    requests++;
    return route.fulfill({ status: phase === "unavailable" ? 503 : 200, contentType: "application/json", body: JSON.stringify(atlas()) });
  }
  if (url.startsWith(`${base}/`)) return route.continue();
  external.push(url); return route.abort();
});
const page = await context.newPage();
page.setDefaultTimeout(10000);
const stored = () => page.evaluate(key => localStorage.getItem(key), key);
const watched = id => page.locator(`[data-watched-market="${id}"]`);
const ready = () => page.getByLabel("Watch a market").waitFor({ state: "visible" });
async function load() {
  await page.goto(`${base}/watchlist-qa`, { waitUntil: "networkidle" });
  await ready();
  if (phase !== "unavailable") await page.waitForFunction(() => !document.querySelector('select[aria-label="Watch a market"]').disabled);
}
async function check(name, action) { await action(); checks.push(name); console.log(`PASS ${name}`); }
let failure = null;
try {
  await check("ephemeral_watch_and_explicit_ids_only_storage", async () => {
    await load(); assert.equal(await stored(), null);
    const before = requests;
    await page.getByLabel("Watch a market", { exact: true }).selectOption("US-USD");
    assert.equal(await stored(), null); assert.match(await watched("US-USD").innerText(), /stale/i);
    assert.match(await watched("US-USD").innerText(), /2026|Aug|08/);
    await page.getByLabel("Remember market choices on this device").check();
    assert.deepEqual(JSON.parse(await stored()), { schema: key, ids: ["US-USD"] });
    await page.getByRole("button", { name: "Watched markets", exact: true }).click();
    assert.equal(await page.locator(".mm-market-tile").count(), 1);
    assert.equal(requests, before, "Watching and filtering must not add network requests");
    await page.screenshot({ path: path.join(output, "synthetic-desktop.png"), fullPage: true });
  });
  await check("reload_and_native_market_lab", async () => {
    await load(); assert.equal(await page.getByLabel("Remember market choices on this device").isChecked(), true);
    assert.equal(await page.locator(".mm-market-tile").count(), 1);
    await page.getByRole("button", { name: "Open US-USD market lab", exact: true }).click();
    assert.match(await page.locator("#mm-local-title").innerText(), /USD/);
    await page.getByLabel("Watch a market", { exact: true }).selectOption("CN-CNY");
    assert.match(await watched("CN-CNY").innerText(), /Derived-only context/);
    await page.getByRole("button", { name: "Unwatch CN-CNY", exact: true }).click();
  });
  await check("absent_markets_stay_visible_across_repeated_checks", async () => {
    phase = "absent";
    for (let i = 0; i < 2; i++) {
      await load(); assert.match(await watched("US-USD").innerText(), /Not returned in the latest atlas/);
      assert.doesNotMatch(await watched("US-USD").innerText(), /3\.5|2026-08-01/);
      assert.equal(await page.locator(".mm-market-tile").count(), 0);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "synthetic-mobile-absent.png"), fullPage: true });
  });
  await check("storage_denial_can_clear_current_tab_without_claiming_deletion", async () => {
    await page.evaluate(() => { window.restoreRemove = Storage.prototype.removeItem; Storage.prototype.removeItem = () => { throw new Error("Synthetic deletion denial"); }; });
    await page.getByRole("button", { name: "Clear watchlist", exact: true }).click();
    assert.equal(await watched("US-USD").count(), 0);
    assert.equal(await page.getByLabel("Remember market choices on this device").isChecked(), false);
    assert.ok(await stored()); assert.match(await page.locator(".mm-watchlist__notice").innerText(), /could not remove saved choices/);
    await page.evaluate(() => { Storage.prototype.removeItem = window.restoreRemove; });
  });
  await check("cross_tab_revocation_is_not_recreated", async () => {
    await load(); const other = await context.newPage(); await other.goto(`${base}/watchlist-qa`, { waitUntil: "networkidle" });
    await other.getByLabel("Remember market choices on this device").uncheck();
    await page.waitForFunction(() => !document.querySelector('.mm-watchlist__remember input').checked);
    assert.equal(await watched("US-USD").count(), 0);
    await page.getByLabel("Watch a market", { exact: true }).selectOption("GB-GBP");
    assert.equal(await stored(), null); await other.close();
  });
  await check("unavailable_atlas_does_not_claim_market_absence", async () => {
    await page.getByLabel("Remember market choices on this device").check(); phase = "unavailable";
    await load(); assert.match(await watched("GB-GBP").innerText(), /Atlas unavailable/);
    assert.doesNotMatch(await watched("GB-GBP").innerText(), /Not returned/);
    await page.screenshot({ path: path.join(output, "synthetic-mobile-unavailable.png"), fullPage: true });
  });
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
} catch (error) { failure = error.message; process.exitCode = 1; await page.screenshot({ path: path.join(output, "synthetic-failure.png"), fullPage: true }).catch(() => {}); }
finally {
  const proof = { checked_at: new Date().toISOString(), scope: "synthetic_component_browser_only", source_requests: requests,
    live_requests: 0, checks, page_errors: errors, external_requests: external, failure, status: failure ? "failed" : "passed" };
  await fs.writeFile(path.join(output, "proof.json"), JSON.stringify(proof, null, 2) + "\n"); console.log(JSON.stringify(proof));
  await context.close(); await browser.close();
}
