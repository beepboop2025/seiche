import { useEffect, useRef, useState, type FormEvent } from "react";
import { API_BASE } from "../apiBase";
import { formatValue, GoldInputError, goldInputs, normalizeGiftCity, normalizeGoldResult, safeSourceUrl, stateTone,
  type GiftCityData, type GoldForm, type GoldResult } from "../giftCity";
import "../styles-gift-city.css";

const REFRESH_MS = 60_000;
const START: GoldForm = { quantity_kg: "1", fineness: "0.9999", price_usd_per_oz: "", annual_rate_pct: "", days: "30", fx_inr_per_usd: "", fees_usd: "0", day_count: "360" };
const EXAMPLE: GoldForm = { quantity_kg: "0.0311034768", fineness: "1", price_usd_per_oz: "4000", annual_rate_pct: "6", days: "30", fx_inr_per_usd: "90", fees_usd: "10", day_count: "360" };
const groups: Array<{ title: string; description: string; keys: Array<keyof GoldForm> }> = [
  { title: "Metal & purchase", description: "Describe the inventory you plan to finance.", keys: ["quantity_kg", "fineness", "price_usd_per_oz"] },
  { title: "Financing terms", description: "Use your facility rate and interest convention.", keys: ["annual_rate_pct", "days", "day_count"] },
  { title: "Conversion & costs", description: "Include the conversion and costs you expect to pay.", keys: ["fx_inr_per_usd", "fees_usd"] },
];
function jumpTo(id: string) { document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); }
const fields: Array<{ key: keyof GoldForm; label: string; hint: string }> = [
  { key: "quantity_kg", label: "Gross quantity · kg", hint: "Physical bar weight" },
  { key: "fineness", label: "Fineness · fraction", hint: "0.9999 means 999.9 parts per thousand" },
  { key: "price_usd_per_oz", label: "Gold price · USD / fine troy oz", hint: "Your price assumption or dated quote" },
  { key: "annual_rate_pct", label: "Annual funding rate · %", hint: "Your facility rate, including its spread" },
  { key: "days", label: "Funding period · days", hint: "Actual calendar days financed" },
  { key: "fx_inr_per_usd", label: "Conversion · INR per USD", hint: "Your FX assumption; no automatic peg conversion" },
  { key: "fees_usd", label: "Other included costs · USD", hint: "Enter the total costs you want to include" },
];
function clock(value: unknown) {
  return typeof value === "string" && value ? value.replace("T", " ").replace(/(?:\.\d+)?(?:Z|\+00:00)$/, " UTC") : "Not reported";
}
function providerLabel(value: string) {
  return value.toLowerCase() === "cbuae" ? "CBUAE VAT reference" : value.toLowerCase() === "ecb" ? "ECB reference" : value;
}
function State({ status }: { status: string }) { return <span className={`gc-state gc-state--${stateTone(status)}`}>{status.replaceAll("_", " ")}</span>; }
function download(value: unknown, name: string) {
  const href = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], { type: "application/json" }));
  const link = document.createElement("a"); link.href = href; link.download = name;
  document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}
async function jsonResponse(response: Response): Promise<unknown> {
  if (!response.ok) throw new Error(`The evidence service returned HTTP ${response.status}. Retry after checking your connection.`);
  if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("The evidence service did not return JSON.");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("The evidence response was empty.");
  const decoder = new TextDecoder(); let text = "", size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > 2_000_000) { await reader.cancel(); throw new Error("The evidence response exceeded the display limit."); }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode(); return JSON.parse(text);
  } finally { reader.releaseLock(); }
}

function GoldCarry({ evidence }: { evidence: GiftCityData | null }) {
  const [form, setForm] = useState<GoldForm>(START);
  const [result, setResult] = useState<GoldResult | null>(null);
  const [resultEvidence, setResultEvidence] = useState<GiftCityData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [invalidField, setInvalidField] = useState<keyof GoldForm | null>(null);
  const [example, setExample] = useState(false);
  const resultPanel = useRef<HTMLDivElement | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (!result) return;
    resultPanel.current?.focus({ preventScroll: true });
    resultPanel.current?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [result]);
  const change = (key: keyof GoldForm, value: string) => {
    request.current?.abort(); request.current = null; setBusy(false); setResult(null); setResultEvidence(null); setError(null); setInvalidField(null);
    setForm((old) => ({ ...old, [key]: value }));
  };
  const calculate = async (event: FormEvent) => {
    event.preventDefault(); setError(null); setInvalidField(null); setResult(null);
    let inputs;
    try { inputs = goldInputs(form); } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Check your assumptions.");
      if (reason instanceof GoldInputError) { setInvalidField(reason.field); document.getElementById(`gc-${reason.field}`)?.focus(); }
      return;
    }
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15_000); setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/v2/gift-city/gold-carry`, { method: "POST", signal: controller.signal,
        credentials: "omit", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(inputs) });
      const output = normalizeGoldResult(await jsonResponse(response), inputs);
      if (request.current !== controller) return;
      // The export retains the evidence snapshot seen at calculation time.
      setResult(output); setResultEvidence(evidence);
    } catch (reason) {
      if (request.current === controller) setError(controller.signal.aborted ? "The calculation timed out. Your assumptions remain here; retry Calculate." : reason instanceof Error ? reason.message : "The calculation is unavailable.");
    } finally {
      window.clearTimeout(timeout);
      if (request.current === controller) { request.current = null; setBusy(false); }
    }
  };
  const reset = (next: GoldForm, isExample: boolean) => {
    request.current?.abort(); request.current = null; setBusy(false); setResult(null); setResultEvidence(null);
    setError(null); setInvalidField(null); setForm({ ...next }); setExample(isExample);
  };
  const output = result?.outputs;
  return <section className="gc-carry" id="gc-gold" aria-labelledby="gc-carry-title">
    <div className="gc-section-heading"><div><span className="gc-kicker">Gold inventory financing</span><h2 id="gc-carry-title">What does holding the gold cost?</h2><p>Translate bar weight, purity, financing time and FX into an all-in cost. Market references above remain separate from your assumptions.</p></div><span className="gc-scenario-label">Caller-input scenario</span></div>
    <div className="gc-carry-grid"><form onSubmit={calculate} className="gc-form" noValidate>
      <div className="gc-form-tools"><p>Your assumptions</p><button type="button" className="gc-text-button" onClick={() => reset(EXAMPLE, true)}>Use a synthetic example</button></div>
      {example && <p className="gc-example-note" role="status">Synthetic example loaded. These are invented prices and rates, not market quotes. Edit them to explore a scenario.</p>}
      {groups.map((group) => <fieldset className="gc-input-group" key={group.title}><legend>{group.title}</legend><p>{group.description}</p><div className="gc-fields">{group.keys.map((key) => {
        if (key === "day_count") return <label key={key} htmlFor="gc-day_count"><span>Interest day-count basis</span><select id="gc-day_count" value={form.day_count} aria-describedby="gc-day-count-hint" onChange={(event) => change("day_count", event.target.value)}><option value="360">ACT/360</option><option value="365">ACT/365</option></select><small id="gc-day-count-hint">Match your financing agreement</small></label>;
        const { label, hint } = fields.find((field) => field.key === key)!;
        return <label key={key} htmlFor={`gc-${key}`}><span>{label}</span><input id={`gc-${key}`} name={key} type="text" inputMode={key === "days" ? "numeric" : "decimal"} autoComplete="off" spellCheck={false} required maxLength={40} value={form[key]} onChange={(event) => change(key, event.target.value)} aria-invalid={invalidField === key || undefined} aria-describedby={`gc-${key}-hint${invalidField === key ? " gc-calculation-error" : ""}`} /><small id={`gc-${key}-hint`}>{hint}</small></label>;
      })}</div></fieldset>)}
      <p className="gc-form-note">Weight, purity, days and zero fees start as editable assumptions. Your inputs are sent only when you calculate; they are not put in the URL or saved in this browser.</p>
      {error && <p id="gc-calculation-error" className="gc-error" role="alert">{error}</p>}
      <div className="gc-form-submit"><button className="gc-primary" type="submit" disabled={busy}>{busy ? "Calculating…" : "Calculate gold carry"}</button><button className="gc-text-button" type="button" onClick={() => reset(START, false)}>Reset inputs</button></div>
    </form><div className="gc-result" ref={resultPanel} tabIndex={-1} aria-label="Gold carry result" aria-live="polite" aria-busy={busy}>
      {output && result ? <>
        <span className="gc-kicker">Your scenario result</span><div className="gc-result-lead"><small>All-in INR per fine gram</small><strong>₹{formatValue(output.all_in_inr_per_gram, 2)}</strong><p>Fine-gold weight after adjusting gross kilograms for purity. Unentered taxes, duties and costs are excluded.</p></div>
        <dl className="gc-results"><div className="gc-total"><dt>All-in inventory cost</dt><dd>₹{formatValue(output.total_cost_inr, 2)}<small>INR</small></dd></div><div><dt>Metal value</dt><dd>${formatValue(output.metal_value_usd, 2)}</dd></div><div><dt>Funding cost</dt><dd>${formatValue(output.funding_cost_usd, 2)}</dd></div><div><dt>Included fees</dt><dd>${formatValue(result.inputs.fees_usd, 2)}</dd></div><div><dt>Total included cost</dt><dd>${formatValue(output.total_cost_usd, 2)}</dd></div><div><dt>Fine gold</dt><dd>{formatValue(output.fine_grams, 4)} g</dd></div><div><dt>Fine troy ounces</dt><dd>{formatValue(output.fine_troy_oz, 8)}</dd></div></dl>
        <details className="gc-method"><summary>Calculation assumptions</summary><ul>{result.assumptions.map((item) => <li key={item}>{item}</li>)}</ul><p>Scenario arithmetic does not establish trade eligibility, customs liability, settlement access or executable market prices.</p></details>
        <button className="gc-secondary" type="button" onClick={() => download({ schema: "seiche.gift-city-review.v1", exported_at: new Date().toISOString(), scenario: result.raw, market_context: resultEvidence?.raw ?? null }, "seiche-gold-funding-review.json")}>Download scenario + evidence JSON</button><a className="gc-next-step" href="https://liquilens-undertow.com/gold/">Review sale proceeds in Undertow</a><small className="gc-export-note">Your assumptions and the source snapshot stay together in the export.</small>
      </> : <div className="gc-result-empty"><span className="gc-gold-mark" aria-hidden="true">Au</span><h3>See the cost of carrying your gold.</h3><p>Enter your purchase, financing and conversion assumptions. The result separates the metal value, funding cost and included fees before converting them to INR.</p><ol className="gc-formula"><li><span>Metal</span><strong>Weight × purity × price</strong></li><li><span>Carry</span><strong>Funding + included fees</strong></li><li><span>INR</span><strong>Total × your conversion</strong></li></ol><small>No live gold price is assumed.</small></div>}
    </div></div>
  </section>;
}

export default function GiftCity() {
  const [data, setData] = useState<GiftCityData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const controller = new AbortController(); let mounted = true;
    const timeout = window.setTimeout(() => controller.abort(), 15_000); setBusy(true);
    void fetch(`${API_BASE}/api/v2/gift-city`, { signal: controller.signal, credentials: "omit", cache: "no-store", headers: { Accept: "application/json" } })
      .then(jsonResponse).then(normalizeGiftCity).then((next) => { if (mounted) { setData(next); setError(null); setCheckedAt(new Date().toISOString()); } })
      .catch((reason: unknown) => { if (mounted) setError(controller.signal.aborted ? "The corridor evidence request timed out." : reason instanceof Error ? reason.message : "Corridor evidence is unavailable."); })
      .finally(() => { window.clearTimeout(timeout); if (mounted) setBusy(false); });
    return () => { mounted = false; controller.abort(); window.clearTimeout(timeout); };
  }, [reload]);
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") setReload((v) => v + 1); };
    const timer = window.setInterval(refresh, REFRESH_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, []);
  const funding = ["USD", "INR", "AED"].map((currency) => ({ currency, row: data?.funding.find((r) => r.currency === currency) }));
  const fx = data?.forex.filter((row) => `${row.pair} ${row.provider}`.toLowerCase().includes(query.trim().toLowerCase())) ?? [];
  const positioning = data?.positioning ?? {};
  const positioningSource = safeSourceUrl(positioning.source_url);
  return <div className="gc-shell">
    <header className="gc-heading"><div><span className="gc-kicker">India–UAE funding & gold</span><h1>GIFT City funding desk</h1><p>Connect dollar funding, rupee cash and dirham references to the cost of financing gold.</p><nav className="gc-jump-links" aria-label="GIFT City desk sections"><a href="#gift%20city/funding" onClick={() => jumpTo("gc-funding")}>Funding</a><a href="#gift%20city/gold" onClick={() => jumpTo("gc-gold")}>Gold carry</a><a href="#gift%20city/forex" onClick={() => jumpTo("gc-forex")}>FX references</a></nav></div><div className="gc-corridor" aria-label="Three distinct funding currencies"><span>GIFT IFSC <b>USD</b></span><i aria-hidden="true">↔</i><span>India <b>INR</b></span><i aria-hidden="true">↔</i><span>UAE <b>AED</b></span></div></header>
    <div className="gc-observation-bar"><div><strong>{data ? "Dated evidence snapshot" : "Reading source evidence"}</strong><small>Assembled {clock(data?.generated_at)} · Last successful check {clock(checkedAt)}</small></div><div className="gc-actions"><button type="button" className="gc-secondary" disabled={busy} onClick={() => setReload((v) => v + 1)}>{busy ? "Refreshing…" : "Refresh evidence"}</button><button type="button" className="gc-secondary" disabled={!data} onClick={() => download(data?.raw, "seiche-gift-city-evidence.json")}>Evidence JSON</button></div></div>
    {error && <div className="gc-error" role="alert"><strong>Refresh unavailable.</strong> {error} {data ? "The last successful snapshot remains visible with its original observation dates." : "Market values are unavailable; the caller-input calculator remains usable."}<button type="button" disabled={busy} onClick={() => setReload((v) => v + 1)}>Retry</button></div>}
    <section id="gc-funding" aria-labelledby="gc-funding-title"><div className="gc-section-heading"><div><span className="gc-kicker">Funding environment</span><h2 id="gc-funding-title">Three currencies. Separate cash clocks.</h2><p>Benchmarks provide context for financing. They are not your bank’s facility rate, and currencies cannot be netted without an agreed conversion.</p></div></div><div className="gc-funding-grid">{funding.map(({ currency, row }) => <article className="gc-funding-card" key={currency}><div className="gc-card-top"><h3>{currency}</h3><State status={row?.status ?? (busy && !data ? "loading" : "unavailable")} /></div><span className="gc-rate">{formatValue(row?.value, 4)}{row?.value !== null && row?.value !== undefined && <small>{row.unit}</small>}</span><strong className="gc-benchmark">{row?.label ?? `${currency} funding reference`}</strong><span className="gc-instrument">{row?.instrument || "No admitted benchmark"}</span><dl><div><dt>Observation</dt><dd>{row?.as_of ?? "Not available"}</dd></div><div><dt>Publisher</dt><dd>{row?.source || "Not available"}</dd></div>{row?.missed_publication_opportunities !== null && row?.missed_publication_opportunities !== undefined && <div><dt>Missed publication opportunities</dt><dd>{row.missed_publication_opportunities}</dd></div>}</dl>{row?.reason && <p className="gc-card-note">{row.reason}</p>}{row?.source_url && <a href={row.source_url} target="_blank" rel="noreferrer">Publisher evidence ↗</a>}</article>)}</div><p className="gc-caption">The desk checks for updates every minute while visible. A new check or build time never changes a source observation date.</p></section>
    <GoldCarry evidence={data} />
    <section className="gc-positioning" aria-labelledby="gc-positioning-title"><div><span className="gc-kicker">Gold positioning context</span><h2 id="gc-positioning-title">Who is carrying the market exposure?</h2><p>Weekly positioning describes reported futures participation. It does not measure IIBX physical inventory, live prices or executable exit depth.</p><State status={typeof positioning.status === "string" ? positioning.status : "unavailable"} /><small>Observation {clock(positioning.as_of)}</small>{positioningSource && <a href={positioningSource} target="_blank" rel="noreferrer">Positioning source ↗</a>}</div><dl className="gc-positioning-values"><div><dt>Open interest</dt><dd>{formatValue(positioning.open_interest_contracts, 0)}</dd><small>contracts</small></div><div><dt>Managed money net</dt><dd>{formatValue(positioning.managed_money_net_contracts, 0)}</dd><small>long minus short contracts</small></div><div><dt>Producer net</dt><dd>{formatValue(positioning.producer_net_contracts, 0)}</dd><small>long minus short contracts</small></div></dl></section>
    <section id="gc-forex" aria-labelledby="gc-fx-title"><div className="gc-section-heading"><div><span className="gc-kicker">Dated FX references</span><h2 id="gc-fx-title">Same corridor, different reference purposes.</h2><p>CBUAE VAT references and ECB market references retain separate providers and observation dates. These are reference rates, not dealer spreads or executable conversion quotes.</p></div><label className="gc-search" htmlFor="gc-fx-search">Filter currencies or provider<input id="gc-fx-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="AED, INR, ECB…" /></label></div><div className="gc-table-scroll" role="region" tabIndex={0} aria-label="Dated foreign exchange references"><table><caption>Read the quote convention before comparing values or using your own scenario conversion.</caption><thead><tr><th scope="col">Pair</th><th scope="col">Reference</th><th scope="col">Convention</th><th scope="col">Observation</th><th scope="col">Source / purpose</th><th scope="col">Status</th></tr></thead><tbody>{fx.map((row, i) => <tr key={`${row.pair}-${row.provider}-${i}`}><th scope="row">{row.pair}</th><td>{formatValue(row.value, 6)}</td><td>{row.quote_convention || "See evidence"}</td><td>{row.as_of ?? "Unavailable"}</td><td>{row.source_url ? <a href={row.source_url} target="_blank" rel="noreferrer">{providerLabel(row.provider)} ↗</a> : providerLabel(row.provider)}</td><td><State status={row.status} /></td></tr>)}</tbody></table>{!fx.length && <p className="gc-empty">{query ? "No reference rows match this filter." : busy ? "Reading dated reference observations…" : "No admitted FX observations are available."}</p>}</div>{data?.uae_attribution && <p className="gc-caption">{data.uae_attribution} <a href="https://centralbank.ae/en/forex-eibor/exchange-rates/" target="_blank" rel="noreferrer">CBUAE dataset</a></p>}<a className="gc-more" href="#workbench/forex">Open the full FX history workbench →</a></section>
    <section className="gc-workflows" aria-labelledby="gc-workflow-title"><div className="gc-section-heading"><div><span className="gc-kicker">From reference to review</span><h2 id="gc-workflow-title">Build a complete funding question.</h2></div></div><div className="gc-workflow-grid"><article><span>01</span><h3>Identify the gold contract</h3><p>Record weight, purity, delivery location and contract settlement. A physical bar, a depository receipt and a futures position have different cash requirements.</p><a href="https://www.iibx.co.in/Products/Product_Gold" target="_blank" rel="noreferrer">IIBX gold contracts ↗</a></article><article><span>02</span><h3>Map the financing gap</h3><p>Separate purchase funding, margin, fees and the expected receipt of cash. Match the funding tenor to the settlement obligation in the relevant currency.</p><a href="https://www.iibx.co.in/static/settlement_process.aspx" target="_blank" rel="noreferrer">IIBX settlement process ↗</a></article><article><span>03</span><h3>Keep the UAE leg explicit</h3><p>Review dirham funding context and the purpose of the FX reference. Add the actual dealer conversion, fees and availability terms for your corridor.</p><a href="https://centralbank.ae/en/our-operations/monetary-policy-and-domestic-markets/" target="_blank" rel="noreferrer">CBUAE funding framework ↗</a></article></div></section>
    <details className="gc-method gc-source-register"><summary>Source register and methodology</summary><ul>{data?.methodology.map((item) => <li key={item}>{item}</li>)}</ul><div>{data?.sources.map((source) => <a href={source.url} target="_blank" rel="noreferrer" key={`${source.title}-${source.url}`}>{source.title} ↗</a>)}</div><p>Check the current exchange, central-bank and regulatory source for binding contract terms and eligibility. The desk assembles evidence for review; it does not determine admission, tax treatment or trade approval.</p></details>
  </div>;
}
