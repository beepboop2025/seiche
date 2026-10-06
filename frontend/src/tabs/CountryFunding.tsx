import { useState } from "react";
import { API_BASE } from "../apiBase";
import "../styles-india-funding.css";

type Metric = { id: string; label: string; value: number | null; unit: string; asof: string | null;
  status: string; source_url?: string; tenor_years?: number; observation_period?: string;
  published_at?: string | null; knowledge_time?: string; cadence?: string };
type Spread = { id: string; value_bp: number | null; change_bp: number | null; movement: string;
  status: string; asof: string | null; previous_asof: string | null; reason: string | null };
type Curve = { id: string; kind: string; cadence: string; methodology: string; nodes: Metric[]; spreads: Spread[] };
type CountryDesk = { country: string; country_name: string; headline: string; currency: string;
  funding_market_id: string; coverage: { available: number; current: number; declared: number };
  sections: Array<{ id: string; scope: string; metrics: Metric[] }>; curves: Curve[];
  sovereign_spread_to_germany?: { status: string; asof: string | null; value_bp: number | null; definition: string; reason: string } | null;
  known_gaps: string[]; caveats: string[];
  sources: Array<{ publisher: string; source_url: string; rights_url?: string; terms?: string }> };
export type CountryFundingCollection = { schema: string;
  countries: Array<{ code: string; name: string }>; desks: CountryDesk[] };

const number = (value: number | null | undefined, digits = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
const words = (value: string) => value.replaceAll("_", " ");

function Metrics({ metrics, caption }: { metrics: Metric[]; caption: string }) {
  return <div className="india-scroll"><table><caption>{caption}</caption>
    <thead><tr><th>Series</th><th>Value</th><th>Unit</th><th>Observation</th><th>Evidence status</th></tr></thead>
    <tbody>{metrics.length ? metrics.map(metric => <tr key={metric.id}>
      <th>{metric.source_url ? <a href={metric.source_url} target="_blank" rel="noreferrer">{words(metric.label)}</a> : words(metric.label)}</th>
      <td>{number(metric.value, metric.unit === "%" ? 4 : 2)}</td><td>{metric.unit}</td>
      <td>{metric.observation_period || metric.asof || "—"}{metric.cadence === "P1M" && <small>Monthly reference</small>}</td>
      <td>{metric.status === "UNKNOWN" ? "Publication time or calendar unknown" : words(metric.status)}
        {metric.asof && <small>Published: {metric.published_at || "unknown"}</small>}</td>
    </tr>) : <tr><td colSpan={5}>No series has been admitted for this section.</td></tr>}</tbody>
  </table></div>;
}

function CurveView({ curve }: { curve: Curve }) {
  const day = curve.nodes.map(node => node.asof || "").sort().at(-1);
  const nodes = curve.nodes.filter(node => node.asof === day && typeof node.value === "number" && Number.isFinite(node.value));
  const minimum = nodes.length ? Math.floor(Math.min(...nodes.map(n => n.value!)) * 2) / 2 - 0.25 : 0;
  const maximum = nodes.length ? Math.ceil(Math.max(...nodes.map(n => n.value!)) * 2) / 2 + 0.25 : 1;
  const maxTenor = Math.max(10, ...curve.nodes.map(node => node.tenor_years || 0));
  const x = (tenor: number) => 48 + tenor / maxTenor * 700;
  const y = (value: number) => 220 - (value - minimum) / (maximum - minimum) * 180;
  return <div className="india-curve"><h3>{words(curve.kind)}</h3>
    <p>{curve.methodology}</p>
    <p>{curve.cadence === "P1M" ? "Reference month" : "Curve observation"}: {curve.cadence === "P1M" ? day?.slice(0, 7) || "unavailable" : day || "unavailable"}. Only matching dates are plotted.</p>
    {nodes.length > 0 && <svg viewBox="0 0 800 270" role="img" aria-label={`${words(curve.kind)} for ${day}`}>
      <title>{words(curve.kind)} for {day}; missing tenors are not interpolated</title>
      {[minimum, (minimum + maximum) / 2, maximum].map(value => <g key={value}>
        <line x1="48" x2="758" y1={y(value)} y2={y(value)} stroke="currentColor" opacity="0.15" />
        <text x="3" y={y(value) + 4}>{number(value)}%</text>
      </g>)}
      {nodes.map(node => <g key={node.id}>
        <circle cx={x(node.tenor_years || 0)} cy={y(node.value!)} r="5" fill="currentColor"><title>{node.tenor_years} years: {number(node.value, 4)}% · {node.asof}</title></circle>
        <text x={x(node.tenor_years || 0)} y="248" textAnchor="middle">{node.tenor_years}y</text>
      </g>)}
    </svg>}
    <Metrics metrics={curve.nodes} caption="Sovereign references · source dates and yield conventions" />
    {curve.nodes.length > 1 && <div className="india-scroll"><table><caption>Curve slopes and changes over aligned observations</caption>
      <thead><tr><th>Spread</th><th>Level (bp)</th><th>Change (bp)</th><th>Movement</th><th>Dates</th></tr></thead>
      <tbody>{curve.spreads.map(spread => <tr key={spread.id}><th>{spread.id}</th><td>{number(spread.value_bp)}</td><td>{number(spread.change_bp)}</td>
        <td>{words(spread.movement)}<small>{words(spread.status)}{spread.reason && ` · ${spread.reason}`}</small></td>
        <td>{spread.previous_asof || "—"} → {spread.asof || "—"}</td></tr>)}</tbody>
    </table></div>}
  </div>;
}

export default function CountryFunding({ data }: { data?: CountryFundingCollection }) {
  const [code, setCode] = useState("JP");
  const [section, setSection] = useState("policy");
  const [curveId, setCurveId] = useState("");
  if (!data || data.schema !== "seiche.country-funding-catalog.v1" || !data.desks?.length) return (
    <section className="india-desk"><h2>Country funding and sovereign references</h2>
      <p>Country observations are unavailable in this response. Reload the money-market data to try again.</p></section>
  );
  const desk = data.desks.find(item => item.country === code) || data.desks[0];
  const selected = desk.sections.find(item => item.id === section) || desk.sections[0];
  const curve = desk.curves.find(item => item.id === curveId) || desk.curves[0];
  const availableCodes = new Set(data.desks.map(item => item.country));
  return <section className="india-desk" aria-labelledby="country-funding-title">
    <div className="india-heading"><label htmlFor="funding-country">Country </label>
      <select id="funding-country" value={desk.country} onChange={event => { setCode(event.target.value); setCurveId(""); }}>
        {data.countries.filter(item => availableCodes.has(item.code)).map(item => <option key={item.code} value={item.code}>{item.name}</option>)}
      </select><h2 id="country-funding-title">{desk.headline}</h2>
      <p>{desk.coverage.available} of {desk.coverage.declared} declared series have observations; {desk.coverage.current} are current under their source schedules. Coverage is partial.</p>
      {desk.funding_market_id === "EA-EUR" && <p>Policy and funding references apply to the euro area. Sovereign references below are specific to {desk.country_name}.</p>}
    </div>
    {desk.curves.length > 1 && <div className="india-sections" role="group" aria-label="Sovereign reference source">
      {desk.curves.map(item => <button type="button" key={item.id} aria-pressed={item.id === curve.id} onClick={() => setCurveId(item.id)}>{words(item.kind)}</button>)}
    </div>}
    {curve ? <CurveView curve={curve} /> : <p>No publishable sovereign curve is available for this country.</p>}
    {desk.sovereign_spread_to_germany && <p>Monthly spread to Germany: <strong>{number(desk.sovereign_spread_to_germany.value_bp)} bp</strong> · {desk.sovereign_spread_to_germany.asof?.slice(0, 7) || "unavailable"}. {desk.sovereign_spread_to_germany.reason}</p>}
    <h3>Funding evidence · {selected.scope}</h3>
    <div className="india-sections" role="group" aria-label="Country funding section">
      {desk.sections.map(item => <button key={item.id} type="button" aria-pressed={item.id === selected.id} onClick={() => setSection(item.id)}>{words(item.id)}</button>)}
    </div><Metrics metrics={selected.metrics} caption={`${selected.scope} ${words(selected.id)} · observation dates and units`} />
    {desk.known_gaps.length > 0 && <div><h3>Coverage gaps</h3><ul>{desk.known_gaps.map(gap => <li key={gap}>{gap}</li>)}</ul></div>}
    <details><summary>Sources and interpretation</summary><ul>{desk.caveats.map(note => <li key={note}>{note}</li>)}</ul>
      <ul>{desk.sources.map(source => <li key={source.source_url}><a href={source.source_url} target="_blank" rel="noreferrer">{source.publisher || "Official source"}</a>
        {source.terms && <small>{source.terms}</small>}</li>)}</ul>
      <p><a href={`${API_BASE}/api/v2/country-funding/${desk.country}`}>Read this country's data through the API</a></p>
    </details>
  </section>;
}
