// Regression tests for the Money markets desks that rendered blank or misleading
// while the legacy funding board is withheld, run against saved live payloads.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { fixture, loadMoneyMarkets } from "./moneyMarketsHarness.mjs";

const { moneyMarkets, ecb } = await loadMoneyMarkets();
const { ChinaDesk, HarborPressure, transmissionGap } = moneyMarkets;
const atlas = await fixture("moneyMarketsAtlas.live.json");
const workbench = await fixture("ecbWorkbench.live.json");
const ready = { status: "ready", data: ecb.readEcbUsdReferences(workbench) };
const market = (id) => atlas.markets.find((item) => item.market_id === id);
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
const noop = () => undefined;
// Shipped text carries no em or en dashes; a blank value must never render as one.
const DASH = /[\u2013\u2014]/;
const article = (html, heading) => html.match(new RegExp(`<article><span>${heading}</span>[\\s\\S]*?</article>`))?.[0] ?? "";

const china = (references) => render(ChinaDesk, { market: market("CN-CNY"), onOpenLab: noop, references, onRetryReferences: noop });
const transmission = (props) => render(HarborPressure, {
  engine: null, excludedHarbors: ["CHINA"], board: "withheld", markets: atlas.markets,
  references: ready, onRetryReferences: noop, ...props,
});

test("the China desk reads its CNY leg from the ECB reference cross, with observation windows named as such", () => {
  const html = china(ready);
  const card = article(html, "CNY / 60 ECB OBSERVATIONS");
  assert.match(card, /<strong>-1\.40%<\/strong>/);
  assert.match(card, /6\.6529 CNY per USD on 2026-10-30; 0\.69% annualized volatility over the last 20 observations/);
  assert.match(card, /ECB reference · fresh · since 2026-08-07/);
  assert.doesNotMatch(card, DASH);
  assert.match(html, /data-chart="CNY per USD" data-rows="254" data-first="2025-11-03" data-last="2026-10-30"/);
  assert.match(html, /USD\/CNY moved -1\.40% over the last 60 ECB observations \(2026-08-07 to 2026-10-30\), so the renminbi strengthened against the dollar\./);
  assert.match(html, /Annualized volatility over the last 20 observations \(from 2026-10-02\) is 0\.69%\./);
  // The withheld harbors engine and its day-count labels are gone.
  assert.doesNotMatch(html, /60 DAYS|10d annualized|10-day|FX history unavailable|indexed to 100/);
});

test("before or without the ECB cross the China desk says so instead of showing a dash", () => {
  const loading = china({ status: "loading" });
  assert.match(article(loading, "CNY / 60 ECB OBSERVATIONS"), /LOADING[\s\S]*Reading the ECB reference rate/);
  const failed = china({ status: "error", message: "HTTP 503" });
  assert.match(article(failed, "CNY / 60 ECB OBSERVATIONS"), /UNAVAILABLE[\s\S]*ECB reference rate unavailable/);
  assert.match(failed, /The reference-rate request failed \(HTTP 503\)[\s\S]*Retry the reference rate/);
  assert.match(failed, /The ECB currency leg is unavailable, so Seiche leaves the direction open\./);
  const missing = china({ status: "ready", data: { generatedAt: null, crosses: [], cny: null } });
  assert.match(missing, /ECB USD\/CNY reference not served/);
  for (const html of [loading, failed, missing]) assert.doesNotMatch(article(html, "CNY / 60 ECB OBSERVATIONS"), DASH);
});

test("the transmission check states the source-rights withholding and shows the rights-cleared inputs", () => {
  const html = transmission();
  assert.match(html, /Transmission scores are withheld under Seiche’s 2026-10-28 source-rights policy\./);
  assert.match(html, /A withheld score is not a calm reading\./);
  assert.doesNotMatch(html, /still accruing/);
  const row = (currency) => html.match(new RegExp(`<tr><th scope="row"><b>${currency}</b>[\\s\\S]*?</tr>`))?.[0] ?? "";
  assert.match(row("EUR"), /\+3\.25%[\s\S]*EUR weaker against USD[\s\S]*ECB · fresh · 2026-10-30[\s\S]*4\.71%[\s\S]*ESTR 2\.439 %[\s\S]*-0\.3 bp over 20 obs[\s\S]*2026-10-29/);
  assert.match(row("GBP"), /BOE BANK RATE 3\.75 %/, "a derived-only benchmark falls back to the public policy rate");
  assert.match(row("TWD"), /no ECB reference for TWD[\s\S]*TW CBC OVERNIGHT CALL MONTHLY 0\.817 %[\s\S]*20-obs change unavailable/);
  assert.equal(row("USD"), "");
  assert.equal(row("CNY"), "");
  assert.doesNotMatch(html, DASH);
});

test("the transmission message follows the board's actual state", () => {
  assert.match(transmissionGap("not_loaded", null), /has not loaded on this page/);
  assert.match(transmissionGap("loaded", { ok: false, reason: "no harbor has any live series" }),
    /carries no Harbors transmission engine \(no harbor has any live series\)/);
  assert.match(transmissionGap("loaded", { category: "source_rights_restricted", ok: false }), /withheld under Seiche’s 2026-10-28 source-rights policy/);
  // Only a present engine whose harbors still lack scores is accruing history.
  assert.equal(transmissionGap("loaded", { ok: true, harbors: [{ harbor: "JAPAN", stress: null }] }),
    "The global transmission engine is still accruing enough local history.");
  const scored = transmission({ board: "loaded", engine: { ok: true, harbors: [{ harbor: "JAPAN", stress: 64, regime: "watch" }] } });
  assert.match(scored, /<b>JAPAN<\/b>[\s\S]*<strong>64<\/strong>/);
  assert.match(scored, /Each score is a blend/);
  assert.doesNotMatch(scored, /mm-transmission__table/);
});

test("an ECB failure keeps the local rates and offers a retry", () => {
  const html = transmission({ references: { status: "error", message: "HTTP 503" } });
  assert.match(html, /ECB reference rates could not be read \(HTTP 503\)\. Local rates below are unaffected\.[\s\S]*Retry/);
  assert.match(html, /ESTR 2\.439 %/);
  assert.match(html, /ECB reference unavailable/);
});

test("Money markets wires the ECB request and the withheld board state", async () => {
  const source = await readFile(new URL("../src/tabs/MoneyMarkets.tsx", import.meta.url), "utf8");
  assert.match(source, /fetch\(`\$\{API_BASE\}\/api\/v2\/market-workbench\?\$\{ECB_USD_CNY_QUERY\}`/);
  assert.match(source, /const board: BoardState = snap\.engines \? "loaded" : boardWithheld \? "withheld" : "not_loaded";/);
  // The China desk no longer reads the withheld harbors engine for its currency leg.
  assert.doesNotMatch(source, /fx_rows, engine\?\.fx_labels|chg_60d_pct, "%", true\)\}<\/strong>/);
});

test("the withheld board state reaches Money markets", async () => {
  const app = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /<MoneyMarkets snap=\{\{\}\} boardWithheld=\{withheld\} \/>/);
});
