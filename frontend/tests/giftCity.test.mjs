import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/giftCity.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { goldInputs, normalizeGiftCity, normalizeGoldResult, safeSourceUrl, stateTone, formatValue } = await import(`data:text/javascript;base64,${Buffer.from(code.outputText).toString("base64")}`);
const form = () => ({ quantity_kg: "1.0000", fineness: "0.9999", price_usd_per_oz: "2500.123456789012", annual_rate_pct: "5.25", days: "30", day_count: "360", fx_inr_per_usd: "85.012345678901", fees_usd: "0" });
function evidence() {
  return { schema: "seiche.gift-city.v1", generated_at: "2026-10-02T19:00:00Z", status: "partial",
    funding: [{ currency: "INR", label: "India", instrument: "CALL_WAR", value: 5.08, unit: "%", as_of: "2026-09-29", status: "STALE", missed_publication_opportunities: 2, source: "RBI", source_url: "https://www.rbi.org.in/" },
      { currency: "AED", label: "UAE", instrument: "DONIA", value: null, as_of: null, status: "UNAVAILABLE", source_url: "https://centralbank.ae/" }],
    forex: { rows: [
      { pair: "USD/INR", provider: "ecb", value: 85.2, as_of: "2026-10-01", status: "stale", unit: "INR per USD", source_url: "https://www.ecb.europa.eu/" },
      { pair: "USD/INR", provider: "cbuae", value: 85.3, as_of: "2026-10-02", status: "fresh", unit: "INR per USD", source_url: "https://centralbank.ae/" },
    ], uae_capture: { attribution: "Source: Central Bank of the UAE." } },
    gold: { positioning: { status: "fresh", as_of: "2026-09-29", open_interest_contracts: 100 } }, sources: [], methodology: [] };
}

test("scenario decimal strings preserve precision and only day fields become integers", () => {
  const out = goldInputs(form());
  assert.equal(out.price_usd_per_oz, "2500.123456789012");
  assert.equal(out.quantity_kg, "1.0000");
  assert.equal(out.fx_inr_per_usd, "85.012345678901");
  assert.equal(out.days, 30); assert.equal(out.day_count, 360);
  assert.equal(goldInputs({ ...form(), days: "0", day_count: "365" }).days, 0);
});

test("scenario rejects missing quotes, ambiguous units, exponent notation and invalid day counts", () => {
  for (const [key, value] of [["price_usd_per_oz", ""], ["price_usd_per_oz", "1e4"], ["price_usd_per_oz", "2500 USD"],
    ["quantity_kg", "0"], ["fineness", "999.9"], ["fineness", "1.01"], ["fx_inr_per_usd", "NaN"],
    ["fees_usd", "-1"], ["annual_rate_pct", "-1"], ["annual_rate_pct", "1001"], ["days", "1.5"],
    ["days", "3661"], ["day_count", "366"], ["price_usd_per_oz", "2.1234567890123"]]) {
    assert.throws(() => goldInputs({ ...form(), [key]: value }), undefined, `${key}=${value}`);
  }
});

test("normalization keeps the source clocks, stale findings and separate provider rows", () => {
  const raw = evidence(), out = normalizeGiftCity(raw);
  assert.equal(out.funding[0].as_of, "2026-09-29");
  assert.equal(out.funding[0].status, "STALE");
  assert.equal(out.funding[0].missed_publication_opportunities, 2);
  assert.equal(out.funding[1].value, null);
  assert.equal(out.forex.length, 2);
  assert.deepEqual(out.forex.map(r => [r.provider, r.value, r.as_of]), [["ecb", "85.2", "2026-10-01"], ["cbuae", "85.3", "2026-10-02"]]);
  assert.equal(out.raw, raw);
  assert.equal(out.uae_attribution, "Source: Central Bank of the UAE.");
  assert.equal(stateTone("STALE"), "bad"); assert.equal(stateTone("aging"), "warn");
});

test("untrusted source links and invalid source values cannot become clickable evidence", () => {
  for (const url of ["javascript:alert(1)", "//example.com", "https://user:pass@example.com", "http://example.com"]) assert.equal(safeSourceUrl(url), null);
  assert.equal(safeSourceUrl("https://centralbank.ae/"), "https://centralbank.ae/");
  for (const value of [true, "", "NaN", Infinity]) { const raw = evidence(); raw.funding[0].value = value; assert.throws(() => normalizeGiftCity(raw)); }
  const raw = evidence(); raw.forex.rows[0].as_of = "2026-02-31"; assert.throws(() => normalizeGiftCity(raw));
  assert.equal(formatValue(null), "—"); assert.equal(formatValue(undefined), "—"); assert.equal(formatValue(0), "0");
});

test("a calculation receipt must bind the displayed assumptions and return explicit decimal results", () => {
  const inputs = goldInputs(form());
  const receipt = { schema: "seiche.gold-carry.v1", status: "scenario", inputs,
    outputs: { fine_troy_oz: "32.1", fine_grams: "999.9", metal_value_usd: "80250.12", funding_cost_usd: "351.1", total_cost_usd: "80601.22", all_in_inr_per_gram: "6852.44" }, assumptions: ["Simple interest"] };
  assert.equal(normalizeGoldResult(receipt, inputs).outputs.fine_grams, "999.9");
  assert.throws(() => normalizeGoldResult({ ...receipt, inputs: { ...inputs, days: 31 } }, inputs));
  assert.throws(() => normalizeGoldResult({ ...receipt, status: "live_quote" }, inputs));
  assert.throws(() => normalizeGoldResult({ ...receipt, outputs: { ...receipt.outputs, funding_cost_usd: null } }, inputs));
});
