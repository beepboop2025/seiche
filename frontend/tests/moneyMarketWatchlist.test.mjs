import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/moneyMarketWatchlist.ts", import.meta.url), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const watch = await import(`data:text/javascript;base64,${Buffer.from(output.outputText).toString("base64")}`);
const key = watch.MONEY_MARKET_WATCH_KEY;
function store(raw = null) {
  const data = new Map(raw === null ? [] : [[key, raw]]);
  return { data, getItem: name => data.get(name) ?? null, setItem: (name, value) => data.set(name, value), removeItem: name => data.delete(name) };
}
const encoded = ids => JSON.stringify({ schema: key, ids });

test("watching is ephemeral until explicit consent and saves only market IDs", () => {
  const storage = store(), access = () => storage;
  let state = watch.readMarketWatch(access);
  state = watch.changeMarketWatch(state, "US-USD", true, access);
  assert.deepEqual(state.ids, ["US-USD"]);
  assert.equal(storage.data.size, 0);
  state = watch.rememberMarketWatch(state, access);
  assert.equal(state.remembered, true);
  assert.deepEqual(JSON.parse(storage.getItem(key)), { schema: key, ids: ["US-USD"] });
  assert.deepEqual(watch.readMarketWatch(access).ids, ["US-USD"]);
});

test("malformed, oversized, duplicate and value-bearing preferences are rejected", () => {
  for (const raw of ["bad", "x".repeat(2049), "null", "[]", "{}", encoded(["US-USD", "US-USD"]),
    encoded(["<script>"]), encoded(["__proto__"]), encoded(["US-USD", 3]), encoded(Array.from({ length: 13 }, (_, n) => `M${n}-USD`)),
    JSON.stringify({ schema: key, ids: ["US-USD"], value: 4.1 })]) {
    assert.equal(watch.parseMarketWatch(raw), null, raw);
    const storage = store(raw), state = watch.readMarketWatch(() => storage);
    assert.deepEqual(state.ids, []); assert.equal(state.remembered, false);
    assert.equal(storage.getItem(key), raw, "Invalid preferences are not rewritten without consent");
  }
});

test("watch limit permits removing a market and never adds beyond twelve", () => {
  const storage = store(), access = () => storage;
  let state = watch.readMarketWatch(access);
  for (let i = 0; i < 12; i++) state = watch.changeMarketWatch(state, `M${i}-USD`, true, access);
  const rejected = watch.changeMarketWatch(state, "US-USD", true, access);
  assert.deepEqual(rejected.ids, state.ids); assert.match(rejected.notice, /up to 12/);
  state = watch.changeMarketWatch(rejected, "M0-USD", false, access);
  assert.equal(watch.changeMarketWatch(state, "US-USD", true, access).ids.length, 12);
});

test("an explicit remove preserves a newer market added by another tab", () => {
  const storage = store(encoded(["US-USD"])), access = () => storage;
  const previous = watch.readMarketWatch(access);
  storage.setItem(key, encoded(["US-USD", "GB-GBP"]));
  const next = watch.changeMarketWatch(previous, "US-USD", false, access);
  assert.deepEqual(next.ids, ["GB-GBP"]);
  assert.deepEqual(JSON.parse(storage.getItem(key)).ids, ["GB-GBP"]);
  storage.removeItem(key);
  const revoked = watch.changeMarketWatch(next, "CN-CNY", true, access);
  assert.equal(revoked.remembered, false); assert.deepEqual(revoked.ids, ["CN-CNY"]);
  assert.equal(storage.getItem(key), null, "Revoked persistence is not resurrected");
});

test("storage denial allows ephemeral use, retains failed updates, and clears this tab", () => {
  const unavailable = () => { throw new Error("blocked storage"); };
  let ephemeral = watch.readMarketWatch(unavailable);
  ephemeral = watch.changeMarketWatch(ephemeral, "US-USD", true, unavailable);
  assert.deepEqual(ephemeral.ids, ["US-USD"]);
  assert.equal(watch.rememberMarketWatch(ephemeral, unavailable).remembered, false);
  const storage = store(encoded(["US-USD"])), state = watch.readMarketWatch(() => storage);
  storage.setItem = () => { throw new Error("write blocked"); };
  assert.deepEqual(watch.changeMarketWatch(state, "CN-CNY", true, () => storage).ids, ["US-USD"]);
  storage.removeItem = () => { throw new Error("delete blocked"); };
  const cleared = watch.forgetMarketWatch(state, () => storage, true);
  assert.deepEqual(cleared.ids, []); assert.equal(cleared.remembered, false);
  assert.match(cleared.notice, /could not remove saved choices/);
  assert.ok(storage.getItem(key), "An unsuccessful deletion is not reported as persisted removal");
});

test("forget and clear affect only watch preferences", () => {
  const storage = store(encoded(["US-USD"])), access = () => storage;
  storage.setItem("seiche-depth", "expert");
  const state = watch.readMarketWatch(access), forgotten = watch.forgetMarketWatch(state, access);
  assert.deepEqual(forgotten.ids, ["US-USD"]); assert.equal(forgotten.remembered, false);
  assert.equal(storage.getItem(key), null); assert.equal(storage.getItem("seiche-depth"), "expert");
  assert.deepEqual(watch.forgetMarketWatch(forgotten, access, true).ids, []);
});

test("watch filtering preserves source objects, stale states, rights and native clocks", () => {
  const us = Object.freeze({ market_id: "US-USD", region: "Americas", status: "STALE_REFERENCE", benchmark: { value: 3.5, asof: "2026-08-01" } });
  const cn = Object.freeze({ market_id: "CN-CNY", region: "Asia", status: "DERIVED_CONTEXT", benchmark: { value: null, redistribution_status: "derived_only" } });
  const markets = [us, cn], ids = ["US-USD", "CN-CNY", "GB-GBP"];
  assert.deepEqual(watch.filterWatchedMarkets(markets, ids, true, "Asia"), [cn]);
  assert.equal(watch.filterWatchedMarkets(markets, ids, true)[0], us);
  assert.deepEqual(watch.filterWatchedMarkets(markets, [], true), []);
  assert.deepEqual(watch.filterWatchedMarkets(markets, [], false), markets);
  assert.deepEqual(watch.missingWatchedMarkets(markets, ids), ["GB-GBP"]);
  assert.deepEqual(watch.missingWatchedMarkets([cn], ids), ["US-USD", "GB-GBP"]);
  assert.deepEqual(watch.missingWatchedMarkets([cn], ids), ["US-USD", "GB-GBP"], "Absence persists across checks without a stored observation");
});
