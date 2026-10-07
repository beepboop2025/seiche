import assert from "node:assert/strict";
import test from "node:test";
import {
  composePaperNote,
  loadPaperNote,
  riptidePaperView,
  seicheGaugeLine,
  undertowRuleReading,
} from "../src/riptidePaperView.mjs";

const published = {
  schema: "riptide.public.v1",
  kind: "allocation",
  available: true,
  execution_mode: "paper_only",
  real_orders: false,
  as_of: "2026-10-06",
  symbol: "SPY",
  regime: "abundant",
  target_weights: { GLD: 0.3333, IEF: 0, SPY: 0, cash: 0.6667 },
  journal_status: "succeeded",
  equity: 102865.92,
  total_return: 0.0286592,
  max_drawdown: -0.04004194,
};

test("a published paper snapshot prints weights and keeps the limits", () => {
  const view = riptidePaperView(published);
  assert.equal(view.state, "ready");
  const text = view.lines.join("\n");
  assert.match(text, /This is a paper book only\. Real orders are off\./);
  assert.match(text, /not a money-market reading, not an institution risk weight, and not an exit cost/);
  assert.match(text, /As of 2026-10-06 the paper book for SPY shows Riptide regime abundant\./);
  assert.match(text, /separate from Seiche's funding regime/);
  assert.match(text, /no per-institution risky-asset figure/);
  assert.match(text, /does not name which Undertow rows cut this weight/);
  assert.match(text, /DEFICIENT or STRAINED/);
  assert.match(text, /Partial rows cannot clear the book/);
  assert.match(text, /Daily journal status: succeeded\./);
  assert.match(text, /Paper weights: GLD about 33\.3%, IEF 0, SPY 0, cash about 66\.7%\./);
  assert.equal(text.includes("102865"), false);
  assert.equal(text.includes("total_return"), false);
  assert.equal(text.includes("drawdown"), false);
  assert.equal(text.includes("\u2014"), false);
  assert.equal(text.includes("\u2013"), false);
});

test("a missing, closed, or non-paper record fills nothing in", () => {
  const samples = [
    null,
    { available: false, real_orders: false, execution_mode: "paper_only", kind: "allocation" },
    { ...published, target_weights: {} },
    { ...published, target_weights: { SPY: 1.2 } },
    { ...published, regime: "EROSION" },
    { ...published, real_orders: true },
    { ...published, real_orders: "false" },
    { ...published, execution_mode: "live" },
    { ...published, schema: "riptide.public.v2" },
    { ...published, target_weights: { SPY: 0.8, cash: 0.8 } },
    { ...published, target_weights: { SPY: 0.1, cash: 0.1 } },
  ];
  for (const sample of samples) {
    const view = riptidePaperView(sample);
    const text = view.lines.join("\n");
    assert.equal(view.state, "unavailable");
    assert.match(text, /Nothing is filled in/);
    assert.equal(text.includes("33.3"), false);
    assert.equal(text.includes("GLD"), false);
  }
  const closed = riptidePaperView({ ...published, real_orders: true });
  assert.match(closed.lines.join("\n"), /real orders are not confirmed off/);
});

test("unreachable feeds resolve to explicit missing evidence with bounded requests", async () => {
  const calls = [];
  const view = await loadPaperNote(async (url, init) => {
    calls.push({ url, signal: init.signal });
    throw new Error("feed unavailable");
  });
  assert.equal(view.state, "unavailable");
  assert.match(view.lines.join("\n"), /Nothing is filled in/);
  assert.match(view.lines.join("\n"), /gauge could not be read/);
  assert.match(view.lines.join("\n"), /board could not be read/);
  assert.equal(calls.length, 3);
  assert.ok(calls.every(({ signal }) => signal instanceof AbortSignal));
});

test("a missing Seiche gauge is not replaced with the paper regime", () => {
  assert.equal(
    seicheGaugeLine(null),
    "Seiche funding gauge could not be read. The money-market reading is not filled in from Riptide.",
  );
  const line = seicheGaugeLine({
    index: 42.5,
    regime: "EROSION",
    generated_at: "2026-10-06T23:38:03+00:00",
  });
  assert.match(line, /reads 42\.5, regime EROSION, generated at 2026-10-06T23:38:03\+00:00/);
  assert.match(line, /separate from Riptide's paper regime/);
  assert.equal(line.includes("abundant"), false);
  assert.equal(seicheGaugeLine({ index: 0, regime: "CALM", generated_at: "2026-10-06T00:00:00Z" }).includes("reads 0,"), true);
});

test("the Undertow rule counts rates and credit once and does not claim causation", () => {
  const reading = undertowRuleReading({
    asof: "2026-10-06",
    segments: {
      UST: { tier: "STRAINED" },
      IG: { tier: "NORMAL" },
      HY: { tier: "DEFICIENT" },
      EQUITY: { tier: "PARTIAL" },
      CRYPTO: { tier: "DEFICIENT" },
    },
  });
  const text = reading.lines.join("\n");
  assert.match(text, /Rates \(UST\) is STRAINED/);
  assert.match(text, /HY is DEFICIENT/);
  assert.match(text, /The credit bucket is degraded/);
  assert.match(text, /Equities \(EQUITY\) is PARTIAL and is not scored/);
  assert.match(text, /Two buckets are degraded \(rates and credit\), so the published rule would cut paper risky exposure to a quarter/);
  assert.match(text, /cannot clear the book and do not cut/);
  assert.match(text, /ETF, FX, CN, CRYPTO, and BSTOCK are outside this cut/);
  assert.match(text, /does not say these rows caused today's weight/);
  assert.equal(text.includes("Three buckets"), false);
});

test("the LiquiLens lead puts the institution limit first and can skip a gauge", () => {
  const note = composePaperNote(published, null, null, "liquilens", { skipGauge: true });
  assert.match(note.lines[0], /no per-institution risky-asset figure/);
  assert.equal(note.lines.some((line) => line.includes("funding gauge")), false);
  assert.match(note.lines.join("\n"), /not filled in from a missing board/);
});
