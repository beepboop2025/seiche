import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { GEOGRAPHY_BOUNDARY_SOURCE, GEOGRAPHY_PLACES } from '../geographyPlaces';
import {
  PRESETS, createEvidenceLoader, formatNumber, geographyQuery, geographyState, imageURL, moveBox,
  placeViewport, rainfallCSV, readBytes, researchBrief, validDate, validPlace,
  type Box, type District, type DisplayedImagery, type Evidence, type GeographyView, type Month,
} from '../geographyModel';
import '../styles-geography.css';

const message = (error: unknown) => error instanceof Error ? error.message : 'The source could not be loaded. Retry later.';
const publicView = (view: GeographyView) => `https://liquilens.in/banking/geography/?${geographyQuery(view)}`;
const queryFromLocation = () => window.location.hash.split('?')[1] || window.location.search;

function SatelliteViewer({ view, onView, onDisplay }: {
  view: GeographyView; onView: (imagery: DisplayedImagery) => void; onDisplay: (imagery: DisplayedImagery | null) => void;
}) {
  const [before, setBefore] = useState(view.before), [after, setAfter] = useState(view.after), [swipe, setSwipe] = useState(50);
  const [status, setStatus] = useState('Loading satellite images…'), [error, setError] = useState(false), [busy, setBusy] = useState(false), [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<(DisplayedImagery & { urls: [string, string] }) | null>(null);
  const generation = useRef(0), active = useRef<AbortController>(), urls = useRef<string[]>([]), panel = useRef<HTMLElement>(null);
  const pointer = useRef<{ id: number; x: number; y: number }>();
  const { box } = view, boxKey = box.join(','), today = new Date().toISOString().slice(0, 10);
  const region = Object.entries(PRESETS).find(([, preset]) => preset.bbox.every((n, i) => n === box[i]))?.[0] || 'custom';

  useEffect(() => {
    const version = ++generation.current;
    const controller = new AbortController(); active.current?.abort(); active.current = controller;
    urls.current.forEach(URL.revokeObjectURL); urls.current = []; setLoaded(null); onDisplay(null);
    setBefore(view.before); setAfter(view.after); setBusy(true); setError(false); setStatus('Loading both dated images from NASA GIBS…');
    const timer = window.setTimeout(() => controller.abort(), 30000), pending: string[] = [];
    void (async () => {
      try {
        const blobs = await Promise.all([view.before, view.after].map(async date => {
          const response = await fetch(imageURL(date, box), { signal: controller.signal, credentials: 'omit' });
          if (!response.ok || !response.headers.get('content-type')?.startsWith('image/')) throw new Error('Image service unavailable for this date or view.');
          return new Blob([await readBytes(response, 8 * 1024 * 1024)], { type: response.headers.get('content-type') || 'image/jpeg' });
        }));
        if (version !== generation.current) return;
        blobs.forEach(blob => pending.push(URL.createObjectURL(blob)));
        await Promise.all(pending.map(url => { const image = new Image(); image.src = url; return image.decode(); }));
        if (version !== generation.current) return;
        const snapshot = { before: view.before, after: view.after, box: [...box] as Box };
        urls.current = [...pending]; setLoaded({ ...snapshot, urls: [pending[0], pending[1]] }); onDisplay(snapshot);
        setStatus(`Showing ${view.before} and ${view.after}. Clouds and swath gaps may remain.`);
      } catch (e) {
        if (version !== generation.current) return;
        controller.abort(); setError(true); onDisplay(null);
        setStatus(e instanceof Error && e.name === 'AbortError' ? 'Image request timed out. Retry or choose another date.' : `${message(e)} Retry or choose another date.`);
      } finally {
        window.clearTimeout(timer); pending.forEach(url => { if (!urls.current.includes(url)) URL.revokeObjectURL(url); });
        if (version === generation.current) setBusy(false);
      }
    })();
    return () => { generation.current++; controller.abort(); window.clearTimeout(timer); urls.current.forEach(URL.revokeObjectURL); urls.current = []; };
  }, [view.before, view.after, boxKey, retry, onDisplay]);

  function request(target = box, earlier = before, later = after) {
    if (!validDate(earlier) || !validDate(later) || earlier >= later) { setError(true); setStatus('Choose two valid dates, with the earlier image before the later one.'); return; }
    if (earlier === view.before && later === view.after && target.join(',') === boxKey) setRetry(n => n + 1);
    else onView({ before: earlier, after: later, box: target });
  }
  function navigate(scale = 1, dx = 0, dy = 0) { request(moveBox(box, scale, dx, dy), view.before, view.after); }
  function panEnd(event: ReactPointerEvent<HTMLDivElement>) {
    if (!pointer.current || event.pointerId !== pointer.current.id) return;
    const dx = event.clientX - pointer.current.x, dy = event.clientY - pointer.current.y; pointer.current = undefined;
    if (Math.hypot(dx, dy) < 10) return;
    const rect = event.currentTarget.getBoundingClientRect(); navigate(1, -dx / rect.width * (box[2] - box[0]), dy / rect.height * (box[3] - box[1]));
  }
  async function fullscreen() {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await panel.current?.requestFullscreen(); }
    catch { setError(true); setStatus('Full screen is unavailable in this browser. Use browser zoom to enlarge the image.'); }
  }
  return <section className="geo-panel geo-imagery-panel" ref={panel} aria-labelledby="geography-image-title">
    <div className="geo-section-heading"><div><h2 id="geography-image-title">Satellite comparison</h2><p className="geo-meta">NASA GIBS / Terra MODIS true colour</p></div><span className="geo-source-badge">250 m source layer</span></div>
    <form className="geo-image-form" onSubmit={e => { e.preventDefault(); request(); }}>
      <label>Image area<select value={region} onChange={e => request([...PRESETS[e.target.value].bbox], view.before, view.after)}><option value="custom" disabled>Selected extent / custom view</option>{Object.entries(PRESETS).map(([key, p]) => <option value={key} key={key}>{p.name}</option>)}</select></label>
      <label>Earlier image<input type="date" value={before} min="2015-11-24" max={today} required onChange={e => setBefore(e.target.value)} /></label>
      <label>Later image<input type="date" value={after} min="2015-11-24" max={today} required onChange={e => setAfter(e.target.value)} /></label>
      <button type="submit">Load images</button>
    </form>
    <p className={`geo-status${error ? ' geo-error' : ''}`} role="status" aria-live="polite">{status}</p>
    <div className="geo-viewer-wrap">
      <div className="geo-viewer" style={{ aspectRatio: (box[2] - box[0]) / (box[3] - box[1]) }} role="group" tabIndex={0} aria-busy={busy} aria-label="Satellite comparison. Drag or use arrow keys to pan; plus and minus zoom."
        onPointerDown={e => { if (!e.isPrimary || e.button !== 0) return; pointer.current = { id: e.pointerId, x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerUp={panEnd} onPointerCancel={() => { pointer.current = undefined; }}
        onKeyDown={e => { const w = box[2] - box[0], h = box[3] - box[1]; const actions: Record<string, [number, number?, number?]> = { ArrowLeft: [1, -w * .25, 0], ArrowRight: [1, w * .25, 0], ArrowUp: [1, 0, h * .25], ArrowDown: [1, 0, -h * .25], '+': [.6], '=': [.6], '-': [1 / .6] }; if (actions[e.key]) { e.preventDefault(); navigate(...actions[e.key]); } }}>
        {loaded ? <><img src={loaded.urls[1]} alt={`NASA Terra MODIS true-colour imagery requested for ${loaded.after}, area ${loaded.box.join(', ')}`} draggable={false} /><img className="geo-earlier-image" src={loaded.urls[0]} alt={`NASA Terra MODIS true-colour imagery requested for ${loaded.before}, area ${loaded.box.join(', ')}`} draggable={false} style={{ clipPath: `inset(0 ${100 - swipe}% 0 0)` }} /><span className="geo-divider" style={{ left: `${swipe}%` }} /><span className="geo-image-label geo-earlier">Earlier {loaded.before}</span><span className="geo-image-label geo-later">Later {loaded.after}</span></> : <div className="geo-image-empty"><strong>{busy ? 'Retrieving both dated images' : 'Imagery unavailable'}</strong><span>{busy ? 'The evidence panel remains available while images load.' : 'Check the dates and load the images again.'}</span></div>}
      </div>
      <div className="geo-map-controls"><button type="button" aria-label="Zoom in" onClick={() => navigate(.6)}>+</button><button type="button" aria-label="Zoom out" onClick={() => navigate(1 / .6)}>−</button><button type="button" onClick={() => request([...PRESETS.india.bbox], view.before, view.after)}>India</button><button type="button" onClick={fullscreen}>Full screen</button></div>
    </div>
    <div className="geo-swipe"><span>Earlier</span><input type="range" min="0" max="100" value={swipe} disabled={!loaded || busy} onChange={e => setSwipe(Number(e.target.value))} aria-label="Reveal earlier or later image" aria-valuetext={`${swipe}% earlier image, ${100 - swipe}% later image`} /><span>Later</span><output>{swipe}%</output></div>
    <div className="geo-imagery-notes"><p className="geo-meta">WGS84 extent: {box.map(n => n.toFixed(3)).join(', ')}<br />West, south, east, north. Display resolution varies.</p><p className="geo-muted">Drag to pan; use + / − to zoom. Image comparison provides landscape context. It does not measure land value, parcel boundaries or change.</p></div>
    <details><summary>Imagery and district extent sources</summary><p>Displayed images are requested from NASA GIBS. Clouds, missing swaths and black areas can obscure the surface. No ISRO scenes are downloaded by this viewer.</p><p>District selections use the extent of geoBoundaries 2021 districts with padding for the viewer. This is a geographic frame, not a cadastral overlay. Measurements retain their own dates.</p><p>{GEOGRAPHY_BOUNDARY_SOURCE.credit}</p><p>{GEOGRAPHY_BOUNDARY_SOURCE.citation} State assignment: {GEOGRAPHY_BOUNDARY_SOURCE.state_assignment.upstream}; {GEOGRAPHY_BOUNDARY_SOURCE.state_assignment.licence}.</p><p><a href={GEOGRAPHY_BOUNDARY_SOURCE.download} target="_blank" rel="noopener noreferrer">Pinned district boundary source</a></p><p><a href="https://nasa-gibs.github.io/gibs-api-docs/" target="_blank" rel="noopener noreferrer">NASA GIBS documentation</a> · <a href="https://bhoonidhi.nrsc.gov.in/bhoonidhi/" target="_blank" rel="noopener noreferrer">ISRO Bhoonidhi scene access</a> · <a href={GEOGRAPHY_BOUNDARY_SOURCE.licence_url} target="_blank" rel="noopener noreferrer">geoBoundaries / ODbL attribution</a></p></details>
  </section>;
}

function MonthlyRainfall({ months }: { months: Month[] }) {
  const [selected, setSelected] = useState(months.at(-1)?.month || '');
  const row = months.find(m => m.month === selected) || months.at(-1);
  if (!row) return <p className="geo-muted">Monthly history is not available.</p>;
  const maximum = Math.max(1, ...months.flatMap(m => [m.mm, m.normal].filter((v): v is number => v !== null)));
  const ceiling = Math.ceil(maximum / 50) * 50, plotLeft = 48, plotWidth = 576, floor = 166, plotHeight = 134;
  return <section className="geo-months"><div className="geo-section-heading"><h4>Monthly rainfall</h4><label>Observation month<select aria-label="Rainfall observation month" value={row.month} onChange={e => setSelected(e.target.value)}>{months.map(m => <option key={m.month}>{m.month}</option>)}</select></label></div>
    <p className="geo-rainfall-reading" aria-live="polite"><strong>{formatNumber(row.mm)} <small>mm</small></strong><span>{row.month}<br />Normal {formatNumber(row.normal)} mm / departure {formatNumber(row.departure)}%</span></p>
    {row.lowNormal && <p className="geo-muted">Normal is below 10 mm; small amounts can produce large departures.</p>}
    <svg className="geo-chart" viewBox="0 0 640 204" role="img" aria-label="Observed and normal monthly rainfall in millimetres. Month labels appear along the horizontal axis; full values are in the table below.">
      <text x="3" y="15" className="geo-chart-label">mm</text>
      {[0, .5, 1].map(ratio => <g key={ratio}><line className="geo-grid-line" x1={plotLeft} x2="628" y1={floor - ratio * plotHeight} y2={floor - ratio * plotHeight} /><text className="geo-chart-label" x="40" y={floor - ratio * plotHeight + 4} textAnchor="end">{formatNumber(ceiling * ratio)}</text></g>)}
      {months.map((m, i) => { const step = plotWidth / months.length, x = plotLeft + i * step; return <g key={m.month}>
        {m.month === row.month && <rect className="geo-selected-month" x={x - 1} y="25" width={step} height="144" />}
        {(['normal', 'mm'] as const).map((key, j) => m[key] === null ? null : <rect key={key} className={key === 'mm' ? 'geo-bar-observed' : 'geo-bar-normal'} x={x + j * step * .4} y={floor - m[key]! / ceiling * plotHeight} width={Math.max(1, step * .34)} height={Math.max(1, m[key]! / ceiling * plotHeight)}><title>{m.month} {key === 'mm' ? 'observed' : 'normal'}: {formatNumber(m[key])} mm</title></rect>)}
        {(i === 0 || i === months.length - 1 || i % Math.ceil(months.length / 4) === 0) && <text className="geo-chart-label" x={x + step / 2} y="188" textAnchor="middle">{m.month}</text>}
      </g>; })}
    </svg>
    <p className="geo-chart-legend"><span><i className="observed" />Observed rainfall</span><span><i className="normal" />1991–2020 normal</span><span>Missing = no bar</span></p>
    <details><summary>Monthly values and source</summary><div className="geo-table-wrap"><table><caption>CHIRPS rainfall. Blank measurements are shown as not available.</caption><thead><tr>{['Month', 'Rainfall (mm)', 'Normal (mm)', 'Departure (%)', 'Context'].map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead><tbody>{months.map(m => <tr key={m.month}><th scope="row">{m.month}</th><td>{formatNumber(m.mm)}</td><td>{formatNumber(m.normal)}</td><td>{formatNumber(m.departure)}</td><td>{m.lowNormal ? 'Normal below 10 mm' : ''}</td></tr>)}</tbody></table></div></details>
  </section>;
}

function DistrictView({ district, select, standalone }: { district: District; select: (id: string) => void; standalone: boolean }) {
  return <article className="geo-district">{!standalone && <div className="geo-section-heading"><h3>{district.name}, {district.state}</h3><button className="geo-secondary" onClick={() => select(district.id)}>Inspect district</button></div>}
    <div className="geo-metrics">{district.metrics.map(m => <article key={m.label}><h4>{m.label}</h4><strong>{m.value}</strong><p>{m.date}</p><p className="geo-muted">{m.detail}</p></article>)}</div>
    <MonthlyRainfall key={district.id} months={district.months} />
    <p className="geo-muted">Declared drought years: {district.drought === null ? 'Not available' : district.drought.length ? district.drought.join(', ') : 'None listed in this source'}.{district.droughtSource ? ` Source: ${district.droughtSource}.` : ''}</p>
  </article>;
}

function DistrictEvidence({ view, select, onData }: { view: GeographyView; select: (id: string, offset?: number) => void; onData: (data: Evidence | null) => void }) {
  const loader = useMemo(() => createEvidenceLoader(), []), version = useRef(0);
  const [state, setState] = useState(''), [query, setQuery] = useState(''), [exactId, setExactId] = useState('');
  const [data, setData] = useState<Evidence | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  const states = useMemo(() => [...new Set(GEOGRAPHY_PLACES.map(p => p.state))].sort(), []);
  const places = useMemo(() => GEOGRAPHY_PLACES.filter(p => !state || p.state === state), [state]);
  useEffect(() => {
    const token = ++version.current; loader.cancel(); setData(null); onData(null); setError('');
    if (!view.evidence) { setBusy(false); return; }
    setBusy(true);
    const chosen = GEOGRAPHY_PLACES.find(p => p.id === view.evidence);
    if (chosen) { setQuery(`${chosen.name}, ${chosen.state}`); setState(chosen.state); }
    void loader.load(view.evidence, view.offset).then(result => { if (token === version.current && result) { setData(result); onData(result); } }).catch(e => { if (token === version.current) setError(message(e)); }).finally(() => { if (token === version.current) setBusy(false); });
    return () => { version.current++; loader.cancel(); };
  }, [view.evidence, view.offset, loader, retry, onData]);
  function validationError(text: string) { version.current++; loader.cancel(); setData(null); onData(null); setBusy(false); setError(text); }
  function choose(id: string, offset = 0) { if (id === view.evidence && offset === view.offset) setRetry(n => n + 1); else select(id, offset); }
  function find(event: FormEvent) {
    event.preventDefault(); const q = query.trim().toLowerCase(), hits = places.filter(p => `${p.name}, ${p.state}`.toLowerCase() === q || p.name.toLowerCase() === q || p.id === q);
    if (hits.length === 1) choose(hits[0].id); else validationError(hits.length > 1 ? 'More than one district has this name. Include its state or select from the list.' : 'Choose a district from the list.');
  }
  return <section className="geo-panel geo-evidence-panel" aria-labelledby="geography-evidence-title">
    <div className="geo-section-heading"><div><h2 id="geography-evidence-title">District evidence</h2><p className="geo-meta">Independent measurement dates and sources</p></div><span className="geo-source-badge">Research</span></div>
    <div className="geo-search"><label>State<select aria-label="Filter districts by state" value={state} onChange={e => { setState(e.target.value); setQuery(''); }}><option value="">All India</option>{states.map(s => <option key={s}>{s}</option>)}</select></label><button className="geo-secondary" disabled={!state} onClick={() => choose(`in-state-${state.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`)}>Browse state</button></div>
    <form className="geo-search" onSubmit={find}><label>District<input list="seiche-geography-districts" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search a district" autoComplete="off" /></label><datalist id="seiche-geography-districts">{places.map(p => <option key={p.id} value={`${p.name}, ${p.state}`} />)}</datalist><button type="submit">Explore</button></form>
    <details className="geo-exact-id"><summary>Cooperative-bank or exact place ID</summary><form className="geo-search" onSubmit={e => { e.preventDefault(); if (validPlace(exactId)) choose(exactId); else validationError('Enter a valid district, state or cooperative-bank ID.'); }}><label>Evidence ID<input value={exactId} onChange={e => setExactId(e.target.value.trim())} maxLength={120} placeholder="in-dccb-rajasthan-jaipur" required /></label><button type="submit">Load</button></form></details>
    <p role="status" aria-live="polite" className={`geo-status${error ? ' geo-error' : ''}`}>{busy ? 'Loading district evidence…' : error || (data ? `${data.status} / response ${data.generated}` : 'Choose a district or begin with a research example.')}</p>
    {error && view.evidence && <button className="geo-secondary" onClick={() => setRetry(n => n + 1)}>Retry evidence</button>}
    {!data && !busy && !error && <div className="geo-empty-evidence"><h3>A place to start your research</h3><p>Compare the imagery, then inspect dated rainfall, vegetation and soil observations. District selections frame the satellite view using the 2021 boundary extent.</p><button onClick={() => choose('in-dist-rajasthan-jaipur')}>Explore Jaipur district</button><p className="geo-muted">Bank mappings describe areas of operation. They do not establish lending shares or credit risk.</p></div>}
    {data && <div className="geo-results"><header className="geo-selection-heading"><h3>{data.name}</h3><p className="geo-meta">{data.id}</p>{data.reason && <p>{data.reason}</p>}</header>
      {data.total > data.districts.length && <p className="geo-page-note">Showing {data.districts.length} of {data.total} districts. Exports contain this page only.</p>}
      {data.districts.map(d => <DistrictView key={d.id} district={d} standalone={data.id.startsWith('in-dist-')} select={id => choose(id)} />)}
      {!data.districts.length && <p className="geo-muted">No district observations are available for this selection.</p>}
      {data.total > 1 && <nav className="geo-pagination" aria-label="District pages"><button className="geo-secondary" disabled={data.offset === 0} onClick={() => choose(data.id, Math.max(0, data.offset - 5))}>Previous</button><span>{data.offset + 1}–{data.offset + data.districts.length} / {data.total}</span><button className="geo-secondary" disabled={data.nextOffset === null} onClick={() => { if (data.nextOffset !== null) choose(data.id, data.nextOffset); }}>Next</button></nav>}
      {!!data.institutions.length && <details><summary>Linked cooperative banks ({data.institutions.length})</summary><ul>{data.institutions.map(bank => <li key={bank.id}><button className="geo-text-button" onClick={() => choose(bank.id)}>{bank.id}</button> <span className="geo-muted">{bank.basis}</span></li>)}</ul></details>}
      <details><summary>Sources and interpretation limits</summary><ul>{data.sources.map((s, i) => <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{s.label}</a> : s.label}<p className="geo-muted">{s.detail}</p></li>)}</ul><ul>{data.limits.map((limit, i) => <li key={i}>{limit}</li>)}</ul></details>
    </div>}
  </section>;
}

function PrintBrief({ data, view, displayed }: { data: Evidence; view: GeographyView; displayed: DisplayedImagery | null }) {
  return <article className="geo-print-brief"><header><p>Seiche / LiquiLens public-source research</p><h1>{data.name}</h1><p>Geography research brief / response dated {data.generated}</p></header>
    <p>Selection: {data.id}. Districts included: {data.districts.length} of {data.total}; page offset {data.offset}. {data.total > data.districts.length ? 'This is a partial page, not the full footprint.' : ''}</p>
    <p>Imagery: {displayed ? `NASA GIBS Terra MODIS true colour, ${displayed.before} and ${displayed.after}. Extent: ${displayed.box.join(', ')}.` : 'Images were not displayed successfully for this view.'} Image and measurement dates are independent.</p>
    {data.districts.map(d => <section key={d.id}><h2>{d.name}, {d.state}</h2><table><thead><tr><th>Observation</th><th>Value</th><th>Date / period</th><th>Source / context</th></tr></thead><tbody>{d.metrics.map(m => <tr key={m.label}><th>{m.label}</th><td>{m.value}</td><td>{m.date}</td><td>{m.detail}</td></tr>)}</tbody></table><p>Declared drought years: {d.drought === null ? 'Not available' : d.drought.join(', ') || 'None listed in source'}. {d.droughtSource}</p></section>)}
    <h2>Sources and limits</h2><ul>{data.sources.map((source, i) => <li key={i}>{source.label}: {source.detail}. {source.url}</li>)}</ul><p>District extents: {GEOGRAPHY_BOUNDARY_SOURCE.credit}; vintage {GEOGRAPHY_BOUNDARY_SOURCE.year_represented}; {GEOGRAPHY_BOUNDARY_SOURCE.licence}. {GEOGRAPHY_BOUNDARY_SOURCE.download}</p><ul>{data.limits.map((limit, i) => <li key={i}>{limit}</li>)}</ul>
    <p>Image comparison does not establish a land value, parcel boundary, planning permission or bank credit risk. Bank links are areas of operation, not lending shares. Human review is required.</p><p>Reproduce this view: {publicView(view)}</p>
  </article>;
}

export default function Geography() {
  const [view, setView] = useState<GeographyView>(() => geographyState(queryFromLocation()));
  const [data, setData] = useState<Evidence | null>(null), [displayed, setDisplayed] = useState<DisplayedImagery | null>(null), [notice, setNotice] = useState('');
  const current = data && data.id === view.evidence && data.offset === view.offset ? data : null;
  const currentImagery = displayed && displayed.before === view.before && displayed.after === view.after && displayed.box.join(',') === view.box.join(',') ? displayed : null;
  const selectedPlace = GEOGRAPHY_PLACES.find(p => p.id === view.evidence);
  const select = useCallback((id: string, offset = 0) => { setData(null); setNotice(''); setView(previous => ({ ...previous, evidence: id, offset, box: previous.evidence === id ? previous.box : placeViewport(id, GEOGRAPHY_PLACES) || previous.box })); }, []);
  const updateImagery = useCallback((imagery: DisplayedImagery) => { setDisplayed(null); setNotice(''); setView(previous => ({ ...previous, ...imagery })); }, []);
  const receiveData = useCallback((next: Evidence | null) => setData(next), []);
  const receiveImagery = useCallback((next: DisplayedImagery | null) => setDisplayed(next), []);
  useEffect(() => { history.replaceState(null, '', `${location.pathname}${location.search}#geography/?${geographyQuery(view)}`); }, [view]);
  useEffect(() => {
    const update = () => { if (/^#geography(?:\/|\?|$)/i.test(location.hash)) setView(geographyState(queryFromLocation())); };
    window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update);
  }, []);
  async function share() {
    try { await navigator.clipboard.writeText(`${location.origin}${location.pathname}${location.search}#geography/?${geographyQuery(view)}`); setNotice('View link copied with image dates, extent, evidence selection and page.'); }
    catch { setNotice('Copy the browser address to share this research view.'); }
  }
  function download(kind: 'json' | 'csv') {
    if (!current) return;
    const content = kind === 'json' ? JSON.stringify(researchBrief(current, view, currentImagery, GEOGRAPHY_BOUNDARY_SOURCE), null, 2) : rainfallCSV(current);
    const url = URL.createObjectURL(new Blob([content], { type: kind === 'json' ? 'application/json' : 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `${current.id}-page-${current.offset}.${kind}`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice(kind === 'json' ? 'Research brief downloaded with original evidence, source dates and image request links.' : 'Monthly rainfall CSV downloaded. Blank measurements remain missing.');
  }
  const realAssets = `https://liquilens.in/banking/real-assets/${selectedPlace ? `?evidence=${encodeURIComponent(selectedPlace.id)}&offset=0` : ''}`;
  return <div className="geo-shell">
    <header className="geo-intro"><div><h1>India geography</h1><p>Satellite context and dated district evidence for financial research.</p></div><div className="geo-intro-links"><a href={publicView(view)}>Open in LiquiLens</a><a href={realAssets}>Land and planning research</a></div></header>
    <nav className="geo-workflows" aria-label="Research starting points"><span>Start with</span><button className="geo-secondary" onClick={() => select('in-dist-rajasthan-jaipur')}>District conditions</button><button className="geo-secondary" onClick={() => select('in-dccb-rajasthan-jaipur')}>Lender footprint</button><button className="geo-secondary" onClick={() => select('in-state-rajasthan')}>State comparison</button></nav>
    <div className="geo-workspace-bar"><div><strong>{current?.name || selectedPlace && `${selectedPlace.name}, ${selectedPlace.state}` || (view.evidence ? 'Selected evidence' : 'India overview')}</strong><span>{selectedPlace ? '2021 district extent / independent observation dates' : 'Select a place to inspect its source-linked evidence'}</span></div><div className="geo-export-actions"><button className="geo-secondary" onClick={share}>Copy view link</button><button className="geo-secondary" disabled={!current} onClick={() => download('json')}>Download JSON brief</button><button className="geo-secondary" disabled={!current?.districts.some(d => d.months.length)} onClick={() => download('csv')}>Rainfall CSV</button><button className="geo-secondary" disabled={!current} onClick={() => window.print()}>Print brief</button></div></div>
    {notice && <p className="geo-export-notice" role="status">{notice}</p>}
    <div className="geo-workspace"><SatelliteViewer view={view} onView={updateImagery} onDisplay={receiveImagery} /><DistrictEvidence view={view} select={select} onData={receiveData} /></div>
    <section className="geo-collaboration"><div><h2>Bring a real research question</h2><p>Use a defined geography, source permissions and independent reference observations to scope a pilot. Potential outputs include a dated district report, a reproducible data feed and a validation note.</p></div><div><a href={`mailto:mrinal@liquilens.in?subject=${encodeURIComponent('Geography research pilot')}&body=${encodeURIComponent(`I would like to discuss a geography research pilot.\n\nResearch question:\nGeography:\nData rights and validation partner:\n\nReference view: ${publicView(view)}`)}`}>Discuss a research pilot</a><a href={realAssets}>Continue to land and planning</a></div></section>
    <p className="geo-footnote">Public-source research. District observations do not change Seiche’s funding indicators or approve credit. NASA imagery, district measurements and land or planning records retain separate sources, dates and permissions.</p>
    {current && <PrintBrief data={current} view={view} displayed={currentImagery} />}
  </div>;
}
