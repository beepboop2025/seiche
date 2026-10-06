// Real React lifecycle regression; all observations and responses are synthetic.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.SEICHE_ATLAS_TEST_URL || "http://127.0.0.1:8218";
const checks = [], errors = [], external = [];
const atlas = label => ({
  schema: "seiche.global-money-markets.v1", status: "PARTIAL", plain_language: label,
  generated_at: "2026-10-06T09:00:00Z", coverage: { declared_markets: 1, live_benchmarks: 0 },
  markets: [{ market_id: "JP-JPY", display_name: "Japan synthetic fixture", currency: "JPY", region: "Asia",
    metrics: [], coverage: { declared_instruments: 1, public_available: 0, coverage_pct: 0 }, known_gaps: [] }],
  country_funding: { schema: "seiche.country-funding-catalog.v1", countries: [{ code: "JP", name: "Japan" }],
    desks: [{ country: "JP", country_name: "Japan", headline: label, currency: "JPY", funding_market_id: "JP-JPY",
      coverage: { available: 0, current: 0, declared: 1 }, sections: [{ id: "policy", scope: "Japan", metrics: [] }],
      curves: [], known_gaps: ["Synthetic missing curve"], caveats: [], sources: [] }] },
});
const html = strict => `<!doctype html><html><body><div id="root"></div>
<script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
<script type="module">
import React from '/node_modules/.vite/deps/react.js';import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
import MoneyMarkets from '/src/tabs/MoneyMarkets.tsx';
let root=ReactDOM.createRoot(document.getElementById('root')), snap={engines:{},generated_at:'2026-10-06T09:00:00Z'};
const render=()=>{const desk=React.createElement(MoneyMarkets,{snap});root.render(${strict ? "React.createElement(React.StrictMode,null,desk)" : "desk"});};
window.qa={refresh(n){snap={generated_at:'2026-10-06T09:'+String(n).padStart(2,'0')+':00Z',engines:{money_market:{
plain_language:'Latest fallback '+n,asof:'2026-10-06',sections:[{id:'policy',label:'Fixture policy',metrics:[]}]}}};render();},
unmount(){root.unmount();},mount(){root=ReactDOM.createRoot(document.getElementById('root'));render();}};render();
</script></body></html>`;
async function until(predicate) {
  const deadline = Date.now() + 10000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, "request did not reach the controlled endpoint");
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
async function fixture(strict = false) {
  const context = await browser.newContext();
  const pending = [];
  await context.route("**/*", route => {
    const url = route.request().url();
    if (url === base + "/loading-qa") return route.fulfill({ contentType: "text/html", body: html(strict) });
    if (url === base + "/api/v2/money-markets") { pending.push(route); return; }
    if (url.startsWith(base + "/")) return route.continue();
    external.push(url); return route.abort();
  });
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  page.setDefaultTimeout(10000);
  await page.clock.install();
  await page.goto(base + "/loading-qa", { waitUntil: "domcontentloaded" });
  await until(() => pending.length > 0);
  return { context, page, pending,
    respond: (index, body = atlas("Delayed Japan fixture"), status = 200, contentType = "application/json") =>
      pending[index].fulfill({ status, contentType, body: typeof body === "string" ? body : JSON.stringify(body) }) };
}
async function check(name, action, strict = false) {
  const f = await fixture(strict);
  try { await action(f); checks.push(name); console.log("PASS " + name); }
  finally { await f.context.close(); }
}
try {
  await check("slow success survives overview refreshes and refreshes again after completion", async ({ page, pending, respond }) => {
    for (const n of [1, 2, 3]) { await page.evaluate(n => window.qa.refresh(n), n); await page.clock.runFor(1); }
    await page.clock.fastForward(11000);
    assert.equal(pending.length, 1, "Overview updates must not cancel or duplicate an active request");
    assert.equal(await page.getByText("The atlas cannot be assessed.", { exact: true }).count(), 0);
    assert.equal(await page.locator(".mm-mode-note").count(), 0, "A slow valid response must not become a USD fallback");
    await respond(0);
    await page.getByLabel("Watch a market", { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('select[aria-label="Watch a market"]').disabled);
    await page.getByRole("button", { name: /Country funding/ }).click();
    assert.equal(await page.locator("#funding-country").inputValue(), "JP");
    assert.equal(await page.locator("#country-funding-title").innerText(), "Delayed Japan fixture");
    await page.evaluate(() => window.qa.refresh(4)); await until(() => pending.length === 2);
    await respond(1, atlas("Refreshed Japan fixture"));
    await page.waitForFunction(() => document.getElementById("country-funding-title")?.textContent === "Refreshed Japan fixture");
    assert.equal(pending.length, 2);
  });
  await check("a stalled request uses the newest fallback and explicit retry can recover", async ({ page, pending, respond }) => {
    await page.evaluate(() => window.qa.refresh(7)); await page.clock.runFor(1);
    await page.clock.fastForward(26000);
    await page.getByRole("button", { name: "Retry global atlas", exact: true }).waitFor();
    assert.match(await page.locator("body").innerText(), /Latest fallback 7/);
    assert.equal(pending.length, 1);
    await page.getByRole("button", { name: "Retry global atlas", exact: true }).click();
    await until(() => pending.length === 2); await respond(1);
    await page.waitForFunction(() => !document.querySelector(".mm-mode-note"));
    await page.getByRole("button", { name: /Country funding/ }).click();
    assert.equal(await page.locator("#funding-country").inputValue(), "JP");
  });
  await check("unmount cancels its request and a late completion cannot replace the reopened desk", async ({ page, pending, respond }) => {
    await page.evaluate(() => { window.qa.unmount(); window.qa.mount(); });
    await until(() => pending.length === 2);
    await respond(0, atlas("Obsolete response")).catch(() => undefined);
    await respond(1, atlas("Reopened Japan fixture"));
    await page.waitForFunction(() => !document.querySelector('select[aria-label="Watch a market"]').disabled);
    await page.getByRole("button", { name: /Country funding/ }).click();
    assert.equal(await page.locator("#country-funding-title").innerText(), "Reopened Japan fixture");
    await page.clock.fastForward(26000);
    assert.equal(await page.locator("#country-funding-title").innerText(), "Reopened Japan fixture");
  });
  for (const [name, body, status, type, message] of [
    ["HTML", "<html>not data</html>", 200, "text/html", "endpoint unavailable"],
    ["invalid contract", { markets: [] }, 200, "application/json", "invalid contract"],
    ["expired session", {}, 401, "application/json", "session expired"],
  ]) {
    await check(name + " remains unavailable without a safe fallback", async ({ page, respond }) => {
      await respond(0, body, status, type);
      await page.getByRole("button", { name: "Retry endpoint", exact: true }).waitFor();
      assert.match(await page.locator(".mm-empty").innerText(), new RegExp(message));
      assert.equal(await page.locator("#funding-country").count(), 0);
    });
  }
  await check("React StrictMode cleanup does not clear its replacement request", async ({ page, pending, respond }) => {
    await page.waitForFunction(() => window.qa);
    // StrictMode can abort its first setup before the browser sends it to routing.
    for (let i = 0; i < pending.length; i++) await respond(i).catch(() => undefined);
    await page.waitForFunction(() => !document.querySelector('select[aria-label="Watch a market"]').disabled);
    await page.getByRole("button", { name: /Country funding/ }).click();
    assert.equal(await page.locator("#funding-country").inputValue(), "JP");
  }, true);
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  const result = { status: "PASS", checks, errors, external, fixture: "synthetic", production_data: false };
  if (process.env.SEICHE_ATLAS_TEST_RECEIPT) await fs.writeFile(process.env.SEICHE_ATLAS_TEST_RECEIPT, JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} finally { await browser.close(); }
