import { useEffect, useState } from "react";
import { referenceConversion, fundingComparison, type ReferenceQuote, type ConversionInputs, type FundingInputs } from "./referenceScenarios";
import { formatValue } from "./giftCity";
import "./styles-reference-calculators.css";

function exportJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify({ exported_at: new Date().toISOString(), scenario: value }, null, 2) + "\n"], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function Field({ label, value, onChange, hint }: { label: string; value: string; onChange: (value: string) => void; hint?: string }) {
  return <label>{label}<input type="text" inputMode="decimal" value={value} onChange={e => onChange(e.target.value)} autoComplete="off" required />{hint && <small>{hint}</small>}</label>;
}
function Result({ values, unit, assumptions, report }: { values: [string, string][]; unit: string; assumptions: string[]; report: unknown }) {
  return <div className="reference-result" role="status"><dl>{values.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatValue(value, 6)} <small>{unit}</small></dd></div>)}</dl><details><summary>Assumptions and calculation method</summary><ul>{assumptions.map(item => <li key={item}>{item}</li>)}</ul></details><button type="button" onClick={() => exportJson(report, "seiche-reference-scenario.json")}>Download scenario JSON</button></div>;
}
export function FxReferenceCalculator({ reference }: { reference: ReferenceQuote }) {
  const [input, setInput] = useState<ConversionInputs>({ amount: "", fee_bps: "0", fixed_fee_quote: "0" });
  const [result, setResult] = useState<ReturnType<typeof referenceConversion> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const identity = JSON.stringify(reference);
  const resultMatchesReference = result && result.reference.base === reference.base
    && result.reference.quote === reference.quote && result.reference.rate === reference.rate
    && result.reference.as_of === reference.as_of && result.reference.status === reference.status
    && result.reference.provider === reference.provider;
  useEffect(() => { setResult(null); setError(null); }, [identity]);
  const update = (key: keyof ConversionInputs, value: string) => { setInput(p => ({ ...p, [key]: value })); setResult(null); setError(null); };
  return <section className="reference-calculator" aria-label="Dated FX reference converter"><h3>Convert an amount with this reference</h3><p>{reference.base} → {reference.quote} · {reference.provider} · Observation {reference.as_of ?? "unavailable"} · {reference.status}. This is a dated reference valuation; your dealer's rate may differ.</p>
    <form onSubmit={e => { e.preventDefault(); try { setResult(referenceConversion(input, reference)); setError(null); } catch (reason) { setResult(null); setError(reason instanceof Error ? reason.message : "Cannot calculate this reference."); } }}>
      <Field label={`Amount in ${reference.base}`} value={input.amount} onChange={v => update("amount", v)} />
      <Field label="Percentage fee in basis points" hint="100 bp = 1% of gross converted value" value={input.fee_bps} onChange={v => update("fee_bps", v)} />
      <Field label={`Fixed fee in ${reference.quote}`} value={input.fixed_fee_quote} onChange={v => update("fixed_fee_quote", v)} />
      <button type="submit">Calculate reference value</button>
    </form>{error && <p role="alert">{error}</p>}
    {result && resultMatchesReference && <Result unit={result.reference.quote} values={[["Gross reference value", result.outputs.gross_quote], ["Percentage fee", result.outputs.variable_fee_quote], ["Fixed fee", result.outputs.fixed_fee_quote], ["Net after entered fees", result.outputs.net_quote]]} assumptions={result.assumptions} report={result} />}
    <p className="reference-note">Calculation stays in this browser. Changing the pair, reference or inputs clears the previous result.</p>
  </section>;
}
export function FundingCostCalculator() {
  const [input, setInput] = useState<FundingInputs>({ currency: "USD", principal: "", rate_a_pct: "", rate_b_pct: "", fees_a: "0", fees_b: "0", days: "30", day_count: "360" });
  const [result, setResult] = useState<ReturnType<typeof fundingComparison> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const update = (key: keyof FundingInputs, value: string) => { setInput(p => ({ ...p, [key]: value })); setResult(null); setError(null); };
  return <section className="reference-calculator" aria-label="Money-market funding cost comparison"><h2>Compare two funding-cost scenarios</h2><p>Translate rates into the cost of financing a stated amount. Both scenarios use the same currency and day-count basis; enter the rates and fees you want to examine.</p>
    <form onSubmit={e => { e.preventDefault(); try { setResult(fundingComparison(input)); setError(null); } catch (reason) { setResult(null); setError(reason instanceof Error ? reason.message : "Cannot calculate this scenario."); } }}>
      <label>Currency<select value={input.currency} onChange={e => update("currency", e.target.value)}>{["USD", "INR", "AED", "EUR", "GBP", "JPY", "CHF", "SGD", "AUD", "CAD"].map(c => <option key={c}>{c}</option>)}</select></label>
      <Field label="Principal" value={input.principal} onChange={v => update("principal", v)} />
      <Field label="Actual calendar days" value={input.days} onChange={v => update("days", v)} />
      <label>Day-count basis<select value={input.day_count} onChange={e => update("day_count", e.target.value)}><option value="360">Actual / 360</option><option value="365">Actual / 365 fixed</option></select></label>
      <Field label="A: annual rate (%)" value={input.rate_a_pct} onChange={v => update("rate_a_pct", v)} />
      <Field label="A: total included fees" value={input.fees_a} onChange={v => update("fees_a", v)} />
      <Field label="B: annual rate (%)" value={input.rate_b_pct} onChange={v => update("rate_b_pct", v)} />
      <Field label="B: total included fees" value={input.fees_b} onChange={v => update("fees_b", v)} />
      <button type="submit">Compare funding costs</button>
    </form>{error && <p role="alert">{error}</p>}
    {result && <Result unit={input.currency} values={[["A: simple interest", result.outputs.interest_a], ["A: interest + fees", result.outputs.total_cost_a], ["B: simple interest", result.outputs.interest_b], ["B: interest + fees", result.outputs.total_cost_b], ["B minus A cost", result.outputs.cost_b_minus_a]]} assumptions={result.assumptions} report={result} />}
    <p className="reference-note">Local scenario arithmetic. An overnight observation held constant for a longer term is your assumption, not a quoted term rate. Inputs are not transmitted or saved.</p>
  </section>;
}
