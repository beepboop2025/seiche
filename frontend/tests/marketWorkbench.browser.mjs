// Synthetic browser proof; no production prices or personal browser profile.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { payload } from "./fixtures/workbench.mjs";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.SEICHE_WORKBENCH_URL || "http://127.0.0.1:8207";
const output = process.env.SEICHE_WORKBENCH_ARTIFACTS || "/tmp/seiche-workbench-browser-20260909";
const errors = [], checks = [], requests = [];
const realResponse = process.env.SEICHE_WORKBENCH_REAL_RESPONSE
  ? JSON.parse(await fs.readFile(process.env.SEICHE_WORKBENCH_REAL_RESPONSE, "utf8")) : null;
let phase = "ready";
const html = `<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main class="app"><div id="root"></div></main>
<script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
<script type="module">import React from '/node_modules/.vite/deps/react.js';import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';import MarketWorkbench from '/src/tabs/MarketWorkbench.tsx';import '/src/styles.css';ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(React.StrictMode,null,React.createElement(MarketWorkbench)));</script></body></html>`;
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, acceptDownloads: true });
context.on("page", (page) => page.on("pageerror", (error) => errors.push(error.message)));
await context.route("**/*", async (route) => {
  const url = new URL(route.request().url());
  if (url.href === `${base}/workbench-qa`) return route.fulfill({ contentType: "text/html", body: html });
  if (url.pathname === "/api/v2/market-workbench") {
    const selection = Object.fromEntries(url.searchParams);
    selection.days = Number(selection.days);
    requests.push(selection);
    if (realResponse && phase === "real" && selection.provider === "ecb" && selection.base === "USD" && selection.quote === "CNY" && selection.days === 3650) {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(realResponse) });
    }
    const data = payload(selection);
    if (phase === "missing") {
      data.forex.rows.forEach((row) => { row.value = null; row.status = "unavailable"; row.evidence_status = "unavailable"; row.as_of = null; row.observation_count = 0; });
      data.forex.history = []; data.forex.coverage.available_pairs = 0; data.forex.coverage.missing_pairs = data.forex.rows.length; data.forex.coverage.returned_observations = 0;
      data.china.economic_context = {}; data.china.series = []; data.china.history = []; data.china.selected_series = null; data.china.status = "unavailable";
    }
    return route.fulfill({ status: phase === "error" ? 503 : 200, contentType: "application/json", body: JSON.stringify(data) });
  }
  if (url.origin === base) return route.continue();
  return route.abort();
});
const page = await context.newPage();
page.setDefaultTimeout(10000);
async function check(name, action) { await action(); checks.push(name); console.log(`PASS ${name}`); }
async function ready() { await page.getByRole("heading", { name: "Currency comparison", exact: true }).waitFor(); }
let failure = null;
try {
  await check("desktop_pair_dates_keyboard_chart_and_search", async () => {
    await page.goto(`${base}/workbench-qa`, { waitUntil: "networkidle" }); await ready();
    assert.match(await page.locator(".wb-pair-summary").innerText(), /7.2[\s\S]*2026-09-08/);
    const chart = page.getByRole("img", { name: /USD\/CNY reference history/ });
    await chart.focus(); await page.keyboard.press("ArrowLeft");
    assert.match(await page.locator(".wb-chart-reading").innerText(), /2026-09-07/);
    await page.getByLabel("Search pairs or sources").fill("EUR");
    assert.equal(await page.locator(".wb-table").first().locator("tbody tr").count(), 1);
    await page.getByLabel("Search pairs or sources").fill("");
    await page.screenshot({ path: path.join(output, "synthetic-forex-desktop.png"), fullPage: true });
  });
  await check("csv_and_json_preserve_selected_pair_source_and_clock", async () => {
    const csvWait = page.waitForEvent("download"); await page.getByRole("button", { name: "Download history CSV" }).click();
    const csv = await csvWait; await csv.saveAs(path.join(output, "synthetic-history.csv"));
    assert.match(await fs.readFile(path.join(output, "synthetic-history.csv"), "utf8"), /2026-09-08,7.2,USD,CNY,h10/);
    const jsonWait = page.waitForEvent("download"); await page.getByRole("button", { name: "Download evidence JSON" }).click();
    const json = await jsonWait; await json.saveAs(path.join(output, "synthetic-evidence.json"));
    const raw = JSON.parse(await fs.readFile(path.join(output, "synthetic-evidence.json"), "utf8"));
    assert.equal(raw.selection.quote, "CNY"); assert.equal(raw.forex.rows[0].sources[0].fetched_at, "2026-09-09T01:00:00Z");
  });
  await check("provider_and_base_changes_use_one_source_and_dynamic_currency_register", async () => {
    await page.getByLabel("Reference source").selectOption("ecb"); await ready();
    await page.waitForFunction(() => document.querySelector("#wb-pair-title")?.parentElement.textContent.includes("ECB references"));
    assert.match(await page.locator("#wb-pair-title").locator("..").innerText(), /ECB references/);
    assert.ok((await page.getByLabel("Quote currency").locator("option").allTextContents()).includes("HUF"));
    await page.getByLabel("Base currency").selectOption("EUR"); await ready();
    await page.waitForFunction(() => document.querySelector("#wb-pair-title")?.textContent === "EUR / CNY");
    assert.equal(await page.locator("#wb-pair-title").innerText(), "EUR / CNY");
    assert.equal(requests.at(-1).provider, "ecb"); assert.equal(requests.at(-1).base, "EUR");
  });
  await check("china_keeps_annual_periods_units_four_clocks_and_history", async () => {
    await page.getByRole("button", { name: "China Economic structure and funding channels" }).click();
    await page.getByRole("heading", { name: "Economic indicator register" }).waitFor();
    assert.match(await page.locator(".wb-clocks").first().innerText(), /2025-01-01 to 2025-12-31/);
    assert.match(await page.locator(".wb-clocks").first().innerText(), /Source release[\s\S]*Palimpsest collected[\s\S]*Seiche accepted/);
    await page.getByRole("combobox", { name: /Economic indicator/ }).selectOption("cn.wdi.reserves_months_imports");
    await page.getByRole("heading", { name: "Reserves in months of imports", exact: true }).waitFor();
    assert.match(await page.locator(".wb-chart-reading").innerText(), /months/);
    await page.getByLabel("Search indicators").fill("reserves");
    assert.equal(await page.locator(".wb-china-table tbody tr").count(), 1);
    await page.screenshot({ path: path.join(output, "synthetic-china-desktop.png"), fullPage: true });
  });
  await check("mobile_has_no_document_overflow_and_controls_remain_reachable", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: path.join(output, "synthetic-china-mobile.png"), fullPage: true });
    await page.getByRole("button", { name: "Forex Reference rates and currency crosses" }).click(); await ready();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: path.join(output, "synthetic-forex-mobile.png"), fullPage: true });
  });
  await check("HTTP_error_clears_prior_values_and_retry_recovers", async () => {
    phase = "error"; await page.getByRole("button", { name: "Refresh observations" }).click();
    await page.getByRole("alert").waitFor(); assert.equal(await page.locator(".wb-pair-summary").count(), 0);
    phase = "ready"; await page.getByRole("button", { name: "Retry workbench" }).click(); await ready();
  });
  await check("missing_history_stays_empty_and_csv_is_disabled", async () => {
    phase = "missing"; await page.getByRole("button", { name: "Refresh observations" }).click(); await ready();
    await page.getByText("No history is available for this selection.", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Download history CSV" }).isDisabled(), true);
    assert.match(await page.locator(".wb-pair-detail").innerText(), /No history is available/);
    assert.equal(await page.locator(".wb-chart").count(), 0);
  });
  if (realResponse) await check("actual_ECB_capture_displays_29_pairs_and_2557_dated_observations", async () => {
    phase = "real";
    await page.getByRole("button", { name: "China Economic structure and funding channels" }).click();
    await page.getByRole("combobox", { name: /Economic indicator/ }).selectOption("");
    await page.getByRole("button", { name: "Forex Reference rates and currency crosses" }).click();
    await page.getByLabel("Reference source").selectOption("h10"); await ready();
    await page.getByLabel("Reference source").selectOption("ecb"); await ready();
    await page.getByLabel("History window").selectOption("3650");
    await page.waitForFunction(() => document.querySelector(".wb-chart figcaption")?.textContent.includes("2,557"));
    assert.equal(await page.locator(".wb-table").first().locator("tbody tr").count(), 29);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: path.join(output, "actual-ecb-capture-mobile.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1080 });
    await page.screenshot({ path: path.join(output, "actual-ecb-capture-desktop.png"), fullPage: true });
  });
  assert.deepEqual(errors, []);
} catch (error) { failure = error; }
await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ synthetic: true, checks, page_errors: errors, requests, failure: failure?.stack ?? null }, null, 2));
await browser.close();
if (failure) throw failure;
console.log(`PASS ${checks.length} workbench browser checks; ${output}`);
