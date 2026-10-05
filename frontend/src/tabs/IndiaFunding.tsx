import { useState } from "react";
import "../styles-india-funding.css";

type Metric = { id: string; label: string; value: number | null; unit: string; asof: string | null;
  status: string; source_url?: string; security?: string | null; tenor_years?: number };
type Spread = { id: string; status: string; asof: string | null; previous_asof: string | null;
  value_bp: number | null; change_bp: number | null; short_change_bp: number | null;
  long_change_bp: number | null; movement: string; reason: string | null };
export type IndiaDesk = { schema: string; headline: string; status: string; generated_at: string;
  coverage: { available: number; current: number; declared: number };
  sections: Array<{ id: string; metrics: Metric[] }>;
  curve: { nodes: Metric[]; fixed_tenor_reason: string };
  monthly_curve: { nodes: Metric[]; spreads: Spread[]; methodology: string };
  spreads: Spread[]; policy_comparison: { value_bp: number | null; asof: string | null; policy_asof: string | null; interpretation: string };
  liquidity_summary: { asof: string | null; net_injection_crore: number | null; direction: string };
  mechanism: { status: string; explanation: string; alternative_explanations: string[] };
  caveats: string[]; sources: Array<{ title: string; url: string }> };

const number = (value: number | null | undefined, places = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-IN", { maximumFractionDigits: places, minimumFractionDigits: places }) : "—";
const words = (value: string) => value.replaceAll("_", " ");
const label = (metric: Metric) => words(metric.label).replace(/^RBI /, "").replace(/ WEEKLY$/, " (weekly)");
const status = (metric: Metric) => metric.status === "UNKNOWN" ? "Publication time unknown" : words(metric.status);

export default function IndiaFunding({ data }: { data?: IndiaDesk }) {
  const [section, setSection] = useState("policy");
  const [curveMode, setCurveMode] = useState<"benchmark" | "monthly">("benchmark");
  if (!data || data.schema !== "seiche.india-funding-curve.v1") return (
    <section className="india-desk"><h2>India funding and sovereign curve</h2>
      <p>The India evidence desk is unavailable in this response. Reload the money-market data to try again.</p></section>
  );
  const monthly = curveMode === "monthly";
  const curveNodes = monthly ? data.monthly_curve.nodes : data.curve.nodes;
  const spreads = monthly ? data.monthly_curve.spreads : data.spreads;
  const chartDate = curveNodes.map(node => node.asof || "").sort().at(-1);
  const nodes = curveNodes.filter(node => node.asof === chartDate && node.value !== null && Number.isFinite(node.value));
  const minimum = nodes.length ? Math.floor(Math.min(...nodes.map(n => n.value!)) * 2) / 2 - 0.25 : 0;
  const maximum = nodes.length ? Math.ceil(Math.max(...nodes.map(n => n.value!)) * 2) / 2 + 0.25 : 1;
  const x = (tenor: number) => 48 + tenor / 40 * 700;
  const y = (value: number) => 220 - (value - minimum) / (maximum - minimum) * 180;
  const selected = data.sections.find(item => item.id === section) || data.sections[0];
  return <section className="india-desk" aria-labelledby="india-title">
    <div className="india-heading"><span>IN-INR · FUNDING → SOVEREIGN CURVE</span>
      <h2 id="india-title">{data.headline}</h2>
      <p>{data.coverage.available} of {data.coverage.declared} declared series have observations; {data.coverage.current} are current under their source schedules.</p>
    </div>
    <div className="india-summary">
      <article><span>CALL − DATED REPO REFERENCE</span><strong>{number(data.policy_comparison.value_bp)} bp</strong>
        <p>{data.policy_comparison.interpretation}</p><small>Call: {data.policy_comparison.asof || "unavailable"} · policy reference: {data.policy_comparison.policy_asof || "unavailable"}</small></article>
      <article><span>RBI NET INJECTION / ABSORPTION</span><strong>₹{number(data.liquidity_summary.net_injection_crore)} crore</strong>
        <p>Positive means injection; negative means absorption.</p><small>{data.liquidity_summary.asof || "Observation unavailable"} · {words(data.liquidity_summary.direction)}</small></article>
      <article><span>MECHANISM · {words(data.mechanism.status).toUpperCase()}</span>
        <p>{data.mechanism.explanation}</p><small>Other explanations: {data.mechanism.alternative_explanations.join("; ")}.</small></article>
    </div>
    <div className="india-curve"><h3>{monthly ? "Monthly maturity curve" : "Government-security benchmarks"}</h3>
      <div className="india-sections" role="group" aria-label="India curve source">
        <button type="button" aria-pressed={!monthly} onClick={() => setCurveMode("benchmark")}>Daily benchmarks</button>
        <button type="button" aria-pressed={monthly} onClick={() => setCurveMode("monthly")}>Monthly maturity curve</button>
      </div>
      <p>{monthly ? data.monthly_curve.methodology : "Each point is a named bond at an approximate tenor. Missing tenors remain visible in the table."}</p>
      <p>Chart observation: {chartDate || "unavailable"}. Only points from this date are plotted. The bond auctions section below holds separate primary-issuance references, including 40Y where available.</p>
      {nodes.length ? <svg viewBox="0 0 800 270" role="img" aria-label={monthly ? "Indian month-end SGL yields by maturity" : "Available Indian government bond benchmark yields by approximate tenor"}>
        {[minimum, (minimum + maximum) / 2, maximum].map(value => <g key={value}>
          <line x1="48" x2="748" y1={y(value)} y2={y(value)} className="india-grid" />
          <text x="40" y={y(value) + 4} textAnchor="end">{number(value, 2)}%</text></g>)}
        {[1, 5, 10, 15, 20, 30, 40].map(tenor => <text key={tenor} x={x(tenor)} y="247" textAnchor="middle">{tenor}Y</text>)}
        {nodes.map(node => <g key={node.id}><circle cx={x(node.tenor_years!)} cy={y(node.value!)} r="5">
          <title>{node.security || `${node.tenor_years}Y month-end`} · {number(node.value, 4)}% · {node.asof} · {node.status}</title></circle>
          <text x={x(node.tenor_years!)} y={y(node.value!) - 12} textAnchor="middle">{number(node.value, 2)}</text></g>)}
      </svg> : <p>No dated sovereign yields are available.</p>}
      <div className="india-scroll"><table><caption>Source observations; yields in per cent</caption><thead><tr><th>{monthly ? "Maturity" : "Approx. tenor"}</th><th>Yield</th><th>{monthly ? "Series" : "Security"}</th><th>Observation</th><th>Status</th></tr></thead>
        <tbody>{curveNodes.map(node => <tr key={node.id}><th>{node.tenor_years}Y</th><td>{number(node.value, 4)}</td><td>{monthly ? "Month-end SGL" : node.security?.replace("bond:", "") || "—"}</td><td>{node.asof || "—"}</td><td>{status(node)}</td></tr>)}</tbody></table></div>
      {!monthly && <p className="india-note">{data.curve.fixed_tenor_reason}</p>}
    </div>
    <h3>{monthly ? "Historical monthly spreads" : "Curve spreads and movement"}</h3><p>Long yield minus short yield. {monthly ? "Changes compare the two stated month ends; they do not describe today's market." : "Movement compares the two stated dates using the same securities."}</p>
    <div className="india-scroll"><table><caption>Positive spread change means steepening; negative means flattening</caption><thead><tr><th>Spread</th><th>Level, bp</th><th>Change, bp</th><th>Short / long change, bp</th><th>Movement</th><th>Compared dates</th></tr></thead>
      <tbody>{spreads.map(spread => <tr key={spread.id}><th>{spread.id}</th><td>{number(spread.value_bp)}</td><td>{number(spread.change_bp)}</td><td>{number(spread.short_change_bp)} / {number(spread.long_change_bp)}</td><td>{words(spread.movement)}{spread.reason && <small>{spread.reason}</small>}</td><td>{spread.previous_asof || "—"} → {spread.asof || "—"}</td></tr>)}</tbody></table></div>
    <h3>Funding evidence</h3><div className="india-sections" role="group" aria-label="Funding evidence section">
      {data.sections.map(item => <button key={item.id} type="button" aria-pressed={section === item.id} onClick={() => setSection(item.id)}>{words(item.id)}</button>)}</div>
    <div className="india-scroll"><table><caption>{words(selected.id)} · source dates and units</caption><thead><tr><th>Series</th><th>Value</th><th>Unit</th><th>Observation</th><th>Status</th></tr></thead>
      <tbody>{selected.metrics.map(metric => <tr key={metric.id}><th>{metric.source_url ? <a href={metric.source_url} target="_blank" rel="noreferrer">{label(metric)}</a> : label(metric)}{metric.security && <small>{metric.security}</small>}</th><td>{number(metric.value, metric.unit === "%" ? 4 : 2)}</td><td>{metric.unit}</td><td>{metric.asof || "—"}</td><td>{status(metric)}</td></tr>)}</tbody></table></div>
    <details><summary>Sources and interpretation</summary><ul>{data.caveats.map(note => <li key={note}>{note}</li>)}</ul>
      <ul>{data.sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a></li>)}</ul>
      <p><a href="https://api.seiche.info/api/v2/india-funding">Read the India API</a> · MCP: money_market_context with section india.</p>
    </details>
  </section>;
}
