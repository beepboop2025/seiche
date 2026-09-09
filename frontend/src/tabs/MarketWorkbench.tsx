import { useEffect, useId, useMemo, useState } from "react";
import { API_BASE } from "../apiBase";
import MarketSeriesExplorer from "./MarketSeriesExplorer";
import {
  chinaHistoryCsv, filterFxRows, fxHistoryCsv, normalizeWorkbench, seriesPlot,
  type ChinaObservation, type FxRow, type HistoryPoint, type MarketWorkbenchData,
} from "../marketWorkbench";
import "../styles-workbench.css";

type View = "funding" | "forex" | "china";
type Resource = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: MarketWorkbenchData };
const CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "CNY", "INR", "KRW", "MXN", "BRL", "ZAR", "NZD", "DKK", "HKD", "MYR", "NOK", "SEK", "SGD", "TWD", "THB", "LKR"];
const WINDOWS = [{ days: 90, label: "90 days" }, { days: 365, label: "1 year" }, { days: 1095, label: "3 years" }, { days: 3650, label: "10 years" }];

function number(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
}
function change(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value > 0 ? "+" : ""}${number(value, 2)}%`;
}
function clock(value: unknown): string {
  if (typeof value !== "string" || !value) return "not reported";
  return value.replace("T", " ").replace(/(?:\.\d+)?(?:Z|\+00:00)$/, " UTC");
}
function status(value: string): string { return value.replaceAll("_", " "); }
function download(content: string, filename: string, type: string) {
  const href = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = href; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

function Exports({ data, kind }: { data: MarketWorkbenchData; kind: "forex" | "china" }) {
  const name = kind === "forex" ? `${data.selection.provider}-${data.selection.base}-${data.selection.quote}` : data.china.selected_series ?? "china";
  const haveHistory = kind === "forex" ? data.forex.history.length > 0 : data.china.history.length > 0;
  return <div className="wb-exports" aria-label="Export current selection">
    <button type="button" disabled={!haveHistory} onClick={() => download(kind === "forex" ? fxHistoryCsv(data) : chinaHistoryCsv(data), `seiche-${name}.csv`, "text/csv;charset=utf-8")}>Download history CSV</button>
    <button type="button" onClick={() => download(JSON.stringify(data.raw, null, 2) + "\n", `seiche-${name}-evidence.json`, "application/json")}>Download evidence JSON</button>
  </div>;
}

function HistoryChart({ points, title, unit, annual = false }: { points: HistoryPoint[]; title: string; unit: string; annual?: boolean }) {
  const id = useId();
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 600px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 600px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const width = compact ? 480 : 800;
  const plot = useMemo(() => seriesPlot(points, width), [points, width]);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => setHover(null), [points]);
  if (!plot) return <div className="wb-empty"><strong>No history is available for this selection.</strong><p>Try another currency or indicator. Missing observations are left empty.</p></div>;
  const highlighted = plot.points[hover ?? plot.points.length - 1] ?? plot.points[plot.points.length - 1];
  const start = plot.points[0], end = plot.points[plot.points.length - 1];
  return <figure className="wb-chart">
    <div className="wb-chart-reading" aria-live="polite"><time>{highlighted.date}</time><strong>{number(highlighted.value, 6)}</strong><span>{unit}</span></div>
    <svg viewBox={`0 0 ${width} 264`} className={compact ? "is-compact" : undefined} role="img" aria-labelledby={`${id}-title ${id}-description`}
      tabIndex={0} onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        setHover((index) => Math.max(0, Math.min(plot.points.length - 1, (index ?? plot.points.length - 1) + (event.key === "ArrowLeft" ? -1 : 1))));
      }} onPointerLeave={() => setHover(null)} onPointerMove={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const x = (event.clientX - bounds.left) / bounds.width * width;
        let nearest = 0;
        plot.points.forEach((point, i) => { if (Math.abs(point.x - x) < Math.abs(plot.points[nearest].x - x)) nearest = i; });
        setHover(nearest);
      }}>
      <title id={`${id}-title`}>{title}</title>
      <desc id={`${id}-description`}>{plot.points.length} observations from {start.date} to {end.date}, in {unit}. Horizontal spacing follows observation dates. Arrow keys inspect individual observations.</desc>
      {[0, .5, 1].map((fraction) => <g key={fraction}><line className="wb-gridline" x1="80" x2={width - 40} y1={22 + fraction * 200} y2={22 + fraction * 200} /><text className="wb-axis" x="72" y={26 + fraction * 200} textAnchor="end">{number(plot.max - fraction * (plot.max - plot.min), 4)}</text></g>)}
      <path className="wb-price-line" d={plot.path} />
      {(annual || plot.points.length < 24) && plot.points.map((point) => <circle className="wb-chart-point" key={point.date} cx={point.x} cy={point.y} r="3"><title>{point.date}: {number(point.value, 6)} {unit}</title></circle>)}
      <line className="wb-crosshair" x1={highlighted.x} x2={highlighted.x} y1="22" y2="222" /><circle className="wb-selected-point" cx={highlighted.x} cy={highlighted.y} r="4" />
      <text className="wb-axis" x="80" y="250">{start.date}</text><text className="wb-axis" x={width - 40} y="250" textAnchor="end">{end.date}</text>
    </svg>
    <figcaption><span>{plot.points.length.toLocaleString()} {annual ? "annual" : "matched-date"} observations</span><span>{annual ? "Lines connect reported annual periods." : "Reference observations; lines bridge gaps between reported dates."}</span></figcaption>
  </figure>;
}

function ChangeComparison({ row }: { row: FxRow }) {
  const changes: Array<[string, number | null]> = [["1 obs", row.change_1obs_pct], ["5 obs", row.change_5obs_pct], ["20 obs", row.change_20obs_pct], ["60 obs", row.change_60obs_pct]];
  const maximum = Math.max(.001, ...changes.map(([, value]) => Math.abs(value ?? 0)));
  return <div className="wb-changes" aria-label="Changes over prior observations">
    {changes.map(([label, value]) => <div className="wb-change" key={label}><span>{label}</span><strong>{change(value)}</strong><div className="wb-change-track" aria-hidden="true"><i style={{ width: `${Math.abs(value ?? 0) / maximum * 50}%`, left: value !== null && value < 0 ? `${50 - Math.abs(value) / maximum * 50}%` : "50%" }} /></div></div>)}
    <p>Positive change means more {row.quote_currency} per {row.base_currency}. Horizons count observations, including source holidays and gaps.</p>
  </div>;
}

function FxTable({ rows, selected, onSelect }: { rows: FxRow[]; selected: string; onSelect: (code: string) => void }) {
  return <div className="wb-table-scroll" tabIndex={0} role="region" aria-label="Foreign exchange comparison table"><table className="wb-table">
    <caption>Daily reference rates for the selected base. Select a pair to inspect its history.</caption>
    <thead><tr><th scope="col">Pair / evidence</th><th scope="col">Rate</th><th scope="col">Date</th><th scope="col">1 obs</th><th scope="col">5 obs</th><th scope="col">20 obs</th><th scope="col">60 obs</th><th scope="col">20 obs vol.</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.quote_currency} className={row.quote_currency === selected ? "is-selected" : ""}>
      <th scope="row"><button type="button" aria-pressed={row.quote_currency === selected} onClick={() => onSelect(row.quote_currency)}>{row.pair}</button><small>{status(row.evidence_status)} · {status(row.status)}</small></th>
      <td>{number(row.value, 5)}</td><td>{row.as_of ?? "unavailable"}</td><td>{change(row.change_1obs_pct)}</td><td>{change(row.change_5obs_pct)}</td><td>{change(row.change_20obs_pct)}</td><td>{change(row.change_60obs_pct)}</td><td>{row.realized_vol_20obs_pct === null ? "—" : `${number(row.realized_vol_20obs_pct, 2)}%`}</td>
    </tr>)}</tbody>
  </table>{!rows.length && <p className="wb-empty">No pairs match this search.</p>}</div>;
}

function Forex({ data, query, setQuery, setQuote }: { data: MarketWorkbenchData; query: string; setQuery: (query: string) => void; setQuote: (quote: string) => void }) {
  const row = data.forex.rows.find((row) => row.quote_currency === data.selection.quote);
  const rows = filterFxRows(data.forex.rows, query);
  return <div className="wb-forex">
    <section className="wb-pair-detail" aria-labelledby="wb-pair-title">
      <div className="wb-section-heading"><div><h2 id="wb-pair-title">{data.selection.base} / {data.selection.quote}</h2><p>{data.forex.quote_convention} · {data.selection.provider === "ecb" ? "ECB references" : "Federal Reserve H.10"}</p></div><Exports data={data} kind="forex" /></div>
      {row && <div className="wb-pair-summary"><strong>{number(row.value, 6)}</strong><span>{row.unit}<small>{row.as_of ?? "No observation date"}</small></span><span className="wb-state">{status(row.status)} · {status(row.evidence_status)}</span></div>}
      {row?.reason && <p className="wb-notice">{row.reason}</p>}
      <HistoryChart points={data.forex.history} title={`${data.selection.base}/${data.selection.quote} reference history`} unit={row?.unit ?? `${data.selection.quote} per ${data.selection.base}`} />
      {row && <ChangeComparison row={row} />}
      <details className="wb-evidence"><summary>Source records and calculation method</summary>
        <p>These are official reference observations. They do not represent dealer bid/ask quotes or executable prices.</p>
        {row?.sources.map((source) => <div className="wb-source" key={`${source.mnemonic}-${source.source_id}`}><strong>{source.source_id}</strong><span>{source.raw_unit}</span><span>Retrieved {clock(source.fetched_at)}</span>{source.source_url && <a href={source.source_url} target="_blank" rel="noreferrer">Publisher series</a>}</div>)}
        <ul>{data.forex.methodology.map((item) => <li key={item}>{item}</li>)}</ul>
      </details>
    </section>
    <section aria-labelledby="wb-fx-table-title"><div className="wb-section-heading"><div><h2 id="wb-fx-table-title">Currency comparison</h2><p>{data.forex.coverage.available_pairs} of {data.forex.coverage.declared_pairs} pairs available. Observation dates may differ between pairs.</p></div><label className="wb-search">Search pairs or sources<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="CNY, EUR, DEX…" /></label></div><FxTable rows={rows} selected={data.selection.quote} onSelect={setQuote} /></section>
    <details className="wb-evidence"><summary>Selected pair history table</summary><div className="wb-table-scroll" tabIndex={0} role="region" aria-label="Selected pair history"><table className="wb-table"><thead><tr><th scope="col">Observation date</th><th scope="col">{row?.unit ?? "Value"}</th></tr></thead><tbody>{data.forex.history.slice().reverse().map((point) => <tr key={point.date}><th scope="row">{point.date}</th><td>{number(point.value, 6)}</td></tr>)}</tbody></table></div></details>
  </div>;
}

function ChinaTable({ rows, onSelect, selected }: { rows: ChinaObservation[]; onSelect: (id: string) => void; selected: string | null }) {
  return <div className="wb-table-scroll" tabIndex={0} role="region" aria-label="China economic indicators"><table className="wb-table wb-china-table"><caption>Latest annual observation for each accepted series. Values retain their published units.</caption><thead><tr><th scope="col">Indicator</th><th scope="col">Value</th><th scope="col">Unit</th><th scope="col">Period</th><th scope="col">Channel</th></tr></thead><tbody>{rows.map((row) => <tr key={row.series_id} className={selected === row.series_id ? "is-selected" : ""}><th scope="row"><button type="button" aria-pressed={selected === row.series_id} onClick={() => onSelect(row.series_id)}>{row.label}</button><small>{row.series_id}</small></th><td>{number(row.value)}</td><td>{row.unit}</td><td>{row.period_end.slice(0, 4)}</td><td>{row.market_channels.map(status).join(", ") || "structural"}</td></tr>)}</tbody></table>{!rows.length && <p className="wb-empty">No accepted indicators match this search.</p>}</div>;
}

function China({ data, onSelect, query, setQuery, channel, setChannel }: { data: MarketWorkbenchData; onSelect: (id: string) => void; query: string; setQuery: (value: string) => void; channel: string; setChannel: (value: string) => void }) {
  const china = data.china;
  const selected = china.series.find((row) => row.series_id === china.selected_series) ?? china.history[china.history.length - 1];
  const rows = china.series.filter((row) => `${row.label} ${row.series_id} ${row.unit}`.toLowerCase().includes(query.trim().toLowerCase()) && (!channel || row.market_channels.includes(channel)));
  const points = useMemo(() => china.history.filter((row) => row.value !== null).map((row) => ({ date: row.period_end, value: row.value as number })), [china.history]);
  const context = china.economic_context;
  const clocks = (context?.clocks ?? {}) as Record<string, unknown>;
  const rights = (context?.rights ?? {}) as Record<string, unknown>;
  return <div className="wb-china">
    <section className="wb-china-intro"><div><h2>China’s funding and external balance sheet</h2><p>Trace money growth, bank credit, reserves and external flows through Palimpsest’s accepted economic observations. Annual indicators describe the economic structure around daily money and currency markets.</p></div><span className="wb-state">{status(china.status)}{context ? " · annual · provisional" : ""}</span></section>
    {china.reason && <p className="wb-notice">{china.reason}</p>}
    {china.fx && <div className="wb-china-fx"><strong>USD / CNY</strong><span>{number(china.fx.value, 5)} CNY per USD</span><span>{china.fx.as_of ?? "unavailable"}</span><span>20 obs {change(china.fx.change_20obs_pct)}</span><small>Daily reference rate; a separate clock from annual economic data.</small></div>}
    <div className="wb-channel-grid">{china.channels.map((item) => <article key={item.id}><h3>{item.title}</h3><p>{item.reading}</p><span>{item.available_series} available series</span></article>)}</div>
    {selected && <section className="wb-pair-detail" aria-labelledby="wb-china-series-title"><div className="wb-section-heading"><div><h2 id="wb-china-series-title">{selected.label}</h2><p>{selected.unit} · Annual observations · {selected.series_id}</p></div><Exports data={data} kind="china" /></div><HistoryChart points={points} title={selected.label} unit={selected.unit} annual />
      <dl className="wb-clocks"><div><dt>Observation period</dt><dd>{selected.period_start} to {selected.period_end}</dd></div><div><dt>Source release</dt><dd>{clock(selected.released_at)}</dd></div><div><dt>Palimpsest collected</dt><dd>{clock(selected.collected_at)}</dd></div><div><dt>Seiche accepted</dt><dd>{clock(selected.accepted_at)}</dd></div></dl>
      <p className="wb-source">{selected.evidence_url && <a href={selected.evidence_url} target="_blank" rel="noreferrer">Open source evidence</a>}<span>Historical values reflect the accepted export’s available vintages.</span></p>
    </section>}
    <section aria-labelledby="wb-china-register-title"><div className="wb-section-heading"><div><h2 id="wb-china-register-title">Economic indicator register</h2><p>{china.series.length} accepted series; {rows.length} shown.</p></div><div className="wb-filter-row"><label className="wb-search">Search indicators<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Reserves, credit, trade…" /></label><label>Channel<select value={channel} onChange={(event) => setChannel(event.target.value)}><option value="">All channels</option><option value="money_market">Money market</option><option value="capital_market">Capital market</option></select></label></div></div><ChinaTable rows={rows} selected={china.selected_series} onSelect={onSelect} /></section>
    {!!china.history.length && <details className="wb-evidence"><summary>Annual history and revision evidence</summary><div className="wb-table-scroll" tabIndex={0} role="region" aria-label="Annual China series history"><table className="wb-table"><thead><tr><th scope="col">Period</th><th scope="col">Value</th><th scope="col">Unit</th><th scope="col">Source release</th><th scope="col">Collected</th><th scope="col">Evidence</th></tr></thead><tbody>{china.history.slice().reverse().map((row) => <tr key={row.period_end}><th scope="row">{row.period_end}</th><td>{number(row.value, 6)}</td><td>{row.unit}</td><td>{clock(row.released_at)}</td><td>{clock(row.collected_at)}</td><td>{row.evidence_url ? <a href={row.evidence_url} target="_blank" rel="noreferrer">Source</a> : "not reported"}</td></tr>)}</tbody></table></div></details>}
    <section className="wb-gap-section" aria-labelledby="wb-gap-title"><h2 id="wb-gap-title">Coverage still needed</h2><p>These gaps identify what is required for a fuller China money-market view.</p><div className="wb-gaps">{china.gaps.map((gap) => <article key={gap.id}><h3>{gap.label}</h3><span className="wb-state">{status(gap.status)}</span><p>{gap.reason}</p></article>)}</div></section>
    {context && <details className="wb-evidence"><summary>Economic dataset provenance and rights</summary><p>{String(rights.attribution ?? "World Bank WDI via Palimpsest")} · {String(rights.license ?? "See evidence JSON")}</p><dl className="wb-clocks">{Object.entries(clocks).map(([key, value]) => <div key={key}><dt>{status(key)}</dt><dd>{clock(value)}</dd></div>)}</dl><p>Annual structural context remains separate from the live CN-CNY gauge and does not become a trading signal. Source review and complete acceptance evidence accompany the JSON export.</p></details>}
  </div>;
}

export default function MarketWorkbench() {
  const [view, setView] = useState<View>(() => {
    const part = window.location.hash.split("/")[1];
    return part === "funding" || part === "china" ? part : "forex";
  });
  const [base, setBase] = useState("USD");
  const [provider, setProvider] = useState<"h10" | "ecb">("h10");
  const [quote, setQuote] = useState("CNY");
  const [days, setDays] = useState(365);
  const [chinaSeries, setChinaSeries] = useState<string | null>(null);
  const [seriesOptions, setSeriesOptions] = useState<Array<{ id: string; label: string }>>([]);
  const [resource, setResource] = useState<Resource>({ status: "loading" });
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState("");
  const [chinaQuery, setChinaQuery] = useState("");
  const [chinaChannel, setChinaChannel] = useState("");
  const [currencyOptions, setCurrencyOptions] = useState<{ provider: "h10" | "ecb"; codes: string[] }>({ provider: "h10", codes: CURRENCIES });
  const active = view !== "funding";

  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    let mounted = true;
    const timeout = window.setTimeout(() => controller.abort(), 20_000);
    const params = new URLSearchParams({ base, quote, days: String(days), provider });
    if (chinaSeries) params.set("china_series", chinaSeries);
    setResource({ status: "loading" });
    void fetch(`${API_BASE}/api/v2/market-workbench?${params}`, { signal: controller.signal, credentials: "omit", headers: { Accept: "application/json" } })
      .then(async (response) => {
        if (!response.ok || !(response.headers.get("content-type") ?? "").includes("json")) throw new Error(`The workbench returned HTTP ${response.status}.`);
        return normalizeWorkbench(await response.json(), { base, quote, days, provider, china_series: chinaSeries });
      }).then((data) => {
        if (!mounted) return;
        setResource({ status: "ready", data });
        setCurrencyOptions({ provider: data.selection.provider ?? "h10", codes: data.forex.currencies });
        setSeriesOptions(data.china.series.map((row) => ({ id: row.series_id, label: row.label })));
      }).catch((error: unknown) => {
        if (mounted) setResource({ status: "error", message: error instanceof DOMException && error.name === "AbortError" ? "The workbench request timed out." : error instanceof Error ? error.message : "The workbench could not be loaded." });
      }).finally(() => window.clearTimeout(timeout));
    return () => { mounted = false; controller.abort(); window.clearTimeout(timeout); };
  }, [active, base, quote, days, provider, chinaSeries, reload]);

  useEffect(() => {
    if (!active) return undefined;
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") setReload((value) => value + 1); }, 60_000);
    return () => window.clearInterval(interval);
  }, [active]);

  // Hide a previous selection in the render that changes a control, before
  // the request effect runs. A displayed number always belongs to its label.
  const data = resource.status === "ready"
    && resource.data.selection.base === base && resource.data.selection.quote === quote
    && resource.data.selection.days === days && resource.data.selection.provider === provider
    && (resource.data.selection.china_series || null) === chinaSeries ? resource.data : null;
  const currencies = currencyOptions.provider === provider ? currencyOptions.codes : provider === "h10" ? CURRENCIES : ["USD", "CNY"];
  const changeView = (next: View) => { setView(next); window.history.replaceState(null, "", `#workbench/${next}`); };
  return <div className="wb-shell">
    <header className="wb-heading"><div><h1>Market workbench</h1><p>Follow the observation. Compare the currency. Read the economic structure.</p></div><div className="wb-heading-meta"><span>Funding / FX / China</span><small>{data ? `Retrieved view ${clock(data.generated_at)}` : "Public source observations"}</small></div></header>
    <nav className="wb-view-nav" aria-label="Workbench views">{([ ["funding", "Funding", "Rates, volumes and distributions"], ["forex", "Forex", "Reference rates and currency crosses"], ["china", "China", "Economic structure and funding channels"] ] as const).map(([id, title, description]) => <button type="button" key={id} aria-pressed={view === id} className={view === id ? "is-active" : ""} onClick={() => changeView(id)}><strong>{title}</strong><span>{description}</span></button>)}</nav>
    {view === "funding" ? <MarketSeriesExplorer /> : <>
      <div className="wb-toolbar">
        {view === "forex" ? <><label>Reference source<select value={provider} onChange={(event) => { setProvider(event.target.value as "h10" | "ecb"); setBase("USD"); setQuote("CNY"); }}><option value="h10">Federal Reserve H.10</option><option value="ecb">ECB references</option></select></label><label>Base currency<select value={base} onChange={(event) => { const next = event.target.value; setBase(next); if (next === quote) setQuote(base); }}>{currencies.map((code) => <option key={code}>{code}</option>)}</select></label><span className="wb-pair-slash" aria-hidden="true">/</span><label>Quote currency<select value={quote} onChange={(event) => setQuote(event.target.value)}>{currencies.filter((code) => code !== base).map((code) => <option key={code}>{code}</option>)}</select></label><label>History window<select value={days} onChange={(event) => setDays(Number(event.target.value))}>{WINDOWS.map((window) => <option value={window.days} key={window.days}>{window.label}</option>)}</select></label></>
          : <label className="wb-series-select">Economic indicator<select value={chinaSeries ?? data?.china.selected_series ?? ""} onChange={(event) => setChinaSeries(event.target.value || null)}><option value="">Featured indicator</option>{seriesOptions.map((series) => <option key={series.id} value={series.id}>{series.label}</option>)}</select></label>}
        <button type="button" className="wb-refresh" disabled={resource.status === "loading"} onClick={() => setReload((value) => value + 1)}>Refresh observations</button>
      </div>
      {resource.status === "loading" && <div className="wb-empty wb-loading" role="status"><strong>Loading the selected observations…</strong><p>Reading reference-rate history and accepted China economic evidence.</p></div>}
      {resource.status === "error" && <div className="wb-empty wb-error" role="alert"><strong>{resource.message}</strong><p>The selected values could not be verified. Use Refresh observations to retry.</p><button type="button" onClick={() => setReload((value) => value + 1)}>Retry workbench</button></div>}
      {data && (view === "forex" ? <Forex data={data} query={query} setQuery={setQuery} setQuote={setQuote} /> : <China data={data} onSelect={setChinaSeries} query={chinaQuery} setQuery={setChinaQuery} channel={chinaChannel} setChannel={setChinaChannel} />)}
    </>}
  </div>;
}
