import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { GEOGRAPHY_PLACES } from '../geographyPlaces';
import { PRESETS, createEvidenceLoader, formatNumber, imageURL, imageryState, moveBox, readBytes, validDate, validPlace, type Box, type District, type Evidence, type Month } from '../geographyModel';
import '../styles-geography.css';

const message = (error: unknown) => error instanceof Error ? error.message : 'The source could not be loaded. Retry later.';
function SatelliteViewer() {
  const initial = useMemo(() => imageryState(window.location.hash.split('?')[1] || window.location.search), []);
  const [before, setBefore] = useState(initial.before), [after, setAfter] = useState(initial.after), [box, setBox] = useState<Box>(initial.box);
  const [region, setRegion] = useState(Object.entries(PRESETS).find(([, preset]) => preset.bbox.every((n, i) => n === initial.box[i]))?.[0] || 'custom'), [swipe, setSwipe] = useState(50), [status, setStatus] = useState('Loading satellite images…');
  const [error, setError] = useState(false), [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState<{ urls: [string, string]; before: string; after: string; box: Box } | null>(null);
  const generation = useRef(0), active = useRef<AbortController>(), urls = useRef<string[]>([]), panel = useRef<HTMLElement>(null);
  const pointer = useRef<{ id: number; x: number; y: number }>();
  const today = new Date().toISOString().slice(0, 10);
  function clear() { urls.current.forEach(URL.revokeObjectURL); urls.current = []; setLoaded(null); }
  async function load(target: Box, earlier = before, later = after) {
    const version = ++generation.current; active.current?.abort(); clear(); setBusy(false);
    if (!validDate(earlier) || !validDate(later) || earlier >= later) { setError(true); setStatus('Choose two valid dates, with the earlier image before the later one.'); return; }
    setBox(target); const controller = new AbortController(); active.current = controller;
    const timer = window.setTimeout(() => controller.abort(), 30000); const pending: string[] = [];
    setBusy(true); setError(false); setStatus('Loading both images from NASA GIBS…');
    try {
      const blobs = await Promise.all([earlier, later].map(async date => {
        const response = await fetch(imageURL(date, target), { signal: controller.signal, credentials: 'omit' });
        if (!response.ok || !response.headers.get('content-type')?.startsWith('image/')) throw new Error('Image service unavailable for this date or view.');
        return new Blob([await readBytes(response, 8 * 1024 * 1024)], { type: response.headers.get('content-type') || 'image/jpeg' });
      }));
      if (version !== generation.current) return;
      blobs.forEach(blob => pending.push(URL.createObjectURL(blob)));
      await Promise.all(pending.map(url => { const image = new Image(); image.src = url; return image.decode(); }));
      if (version !== generation.current) return;
      urls.current = [...pending]; setLoaded({ urls: [pending[0], pending[1]], before: earlier, after: later, box: target });
      setStatus(`Images loaded for ${earlier} and ${later}. Cloud and swath gaps may remain.`);
      const q = new URLSearchParams({ before: earlier, after: later, view: target.join(',') });
      history.replaceState(null, '', `${location.pathname}${location.search}#geography/?${q}`);
    } catch (e) {
      if (version !== generation.current) return;
      controller.abort(); clear(); setError(true);
      setStatus(e instanceof Error && e.name === 'AbortError' ? 'Image request timed out. Retry or choose another date.' : `${message(e)} Retry or choose another date.`);
    } finally {
      window.clearTimeout(timer); pending.forEach(url => { if (!urls.current.includes(url)) URL.revokeObjectURL(url); });
      if (version === generation.current) setBusy(false);
    }
  }
  useEffect(() => {
    const onHash = () => {
      if (!/^#geography(?:\/|$)/i.test(location.hash)) return;
      const next = imageryState(location.hash.split('?')[1] || location.search);
      setBefore(next.before); setAfter(next.after);
      setRegion(Object.entries(PRESETS).find(([, preset]) => preset.bbox.every((n, i) => n === next.box[i]))?.[0] || 'custom');
      void load(next.box, next.before, next.after);
    };
    void load(initial.box, initial.before, initial.after); window.addEventListener('hashchange', onHash);
    return () => { window.removeEventListener('hashchange', onHash); generation.current++; active.current?.abort(); urls.current.forEach(URL.revokeObjectURL); };
  }, []);
  function navigate(scale = 1, dx = 0, dy = 0) { setRegion('custom'); void load(moveBox(box, scale, dx, dy)); }
  function panEnd(event: ReactPointerEvent<HTMLDivElement>) {
    if (!pointer.current || event.pointerId !== pointer.current.id) return;
    const dx = event.clientX - pointer.current.x, dy = event.clientY - pointer.current.y; pointer.current = undefined;
    if (Math.hypot(dx, dy) < 10) return;
    const rect = event.currentTarget.getBoundingClientRect(); navigate(1, -dx / rect.width * (box[2] - box[0]), dy / rect.height * (box[3] - box[1]));
  }
  async function share() { if (!loaded) return; try { await navigator.clipboard.writeText(location.href); setStatus('View link copied, including the displayed dates and area.'); } catch { setStatus('Copy the browser address to share this view.'); } }
  async function fullscreen() { try { if (document.fullscreenElement) await document.exitFullscreen(); else await panel.current?.requestFullscreen(); } catch { setError(true); setStatus('Full screen is unavailable in this browser. Use browser zoom to enlarge the image.'); } }
  return <section className="geo-panel" ref={panel} aria-labelledby="geography-image-title">
    <div className="geo-section-heading"><h2 id="geography-image-title">India from above</h2><a href="https://bhoonidhi.nrsc.gov.in/bhoonidhi/" target="_blank" rel="noopener noreferrer">Open ISRO Bhoonidhi ↗</a></div>
    <form className="geo-controls" onSubmit={e => { e.preventDefault(); void load(box); }}>
      <label>View<select value={region} onChange={e => { setRegion(e.target.value); void load([...PRESETS[e.target.value].bbox]); }}><option value="custom" disabled>Custom view</option>{Object.entries(PRESETS).map(([key, p]) => <option value={key} key={key}>{p.name}</option>)}</select></label>
      <label>Earlier image<input type="date" value={before} min="2015-11-24" max={today} required onChange={e => setBefore(e.target.value)} /></label>
      <label>Later image<input type="date" value={after} min="2015-11-24" max={today} required onChange={e => setAfter(e.target.value)} /></label>
      <button type="submit">Load images</button><button type="button" disabled={!loaded || busy} onClick={share}>Copy view link</button>
    </form>
    {loaded && <p className="geo-meta"><a href={`https://liquilens.in/banking/geography/?${new URLSearchParams({ before: loaded.before, after: loaded.after, view: loaded.box.join(',') })}`}>Open this view in LiquiLens ↗</a></p>}
    <p className={`geo-status${error ? ' geo-error' : ''}`} role="status" aria-live="polite">{status}</p>
    <div className="geo-viewer-wrap">
      <div className="geo-viewer" style={{ aspectRatio: (box[2] - box[0]) / (box[3] - box[1]) }} role="group" tabIndex={0} aria-busy={busy} aria-label="Satellite comparison. Drag or use arrow keys to pan; plus and minus zoom."
        onPointerDown={e => { if (!e.isPrimary || e.button !== 0) return; pointer.current = { id: e.pointerId, x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerUp={panEnd} onPointerCancel={() => { pointer.current = undefined; }}
        onKeyDown={e => { const w = box[2] - box[0], h = box[3] - box[1]; const actions: Record<string, [number, number?, number?]> = { ArrowLeft: [1, -w * .25, 0], ArrowRight: [1, w * .25, 0], ArrowUp: [1, 0, h * .25], ArrowDown: [1, 0, -h * .25], '+': [.6], '=': [.6], '-': [1 / .6] }; if (actions[e.key]) { e.preventDefault(); navigate(...actions[e.key]); } }}>
        {loaded ? <><img src={loaded.urls[1]} alt={`NASA Terra MODIS true-colour imagery requested for ${loaded.after}, area ${loaded.box.join(', ')}`} draggable={false} /><img className="geo-earlier-image" src={loaded.urls[0]} alt={`NASA Terra MODIS true-colour imagery requested for ${loaded.before}, area ${loaded.box.join(', ')}`} draggable={false} style={{ clipPath: `inset(0 ${100 - swipe}% 0 0)` }} /><span className="geo-divider" style={{ left: `${swipe}%` }} /><span className="geo-image-label geo-earlier">Earlier · {loaded.before}</span><span className="geo-image-label geo-later">Later · {loaded.after}</span></> : <div className="geo-image-empty">{busy ? 'Retrieving both dated images…' : 'Images unavailable. Check the dates or retry.'}</div>}
      </div>
      <div className="geo-map-controls"><button type="button" aria-label="Zoom in" onClick={() => navigate(.6)}>+</button><button type="button" aria-label="Zoom out" onClick={() => navigate(1 / .6)}>−</button><button type="button" onClick={() => { setRegion('india'); void load([...PRESETS.india.bbox]); }}>India</button><button type="button" onClick={fullscreen}>Full screen</button></div>
    </div>
    <div className="geo-swipe"><span>Earlier image</span><input type="range" min="0" max="100" value={swipe} disabled={!loaded || busy} onChange={e => setSwipe(Number(e.target.value))} aria-label="Reveal earlier or later image" aria-valuetext={`${swipe}% earlier image, ${100 - swipe}% later image`} /><span>Later image</span><output>{swipe}%</output></div>
    <p className="geo-meta">NASA GIBS · Terra MODIS · True colour · 250 m source layer; displayed resolution varies<br />View: {box.map(n => n.toFixed(2)).join(', ')} (west, south, east, north)</p>
    <p className="geo-muted">Drag to move, or use the keyboard controls. Clouds, missing swaths and black areas may obscure the surface. These images are landscape context, not district measurements or parcel boundaries. Date comparison alone does not measure change.</p>
    <details><summary>Image sources and access</summary><p>This viewer requests dated NASA GIBS images directly. It does not contain downloaded ISRO scenes. Bhoonidhi remains the portal for ISRO scene discovery and access under its terms.</p><a href="https://nasa-gibs.github.io/gibs-api-docs/" target="_blank" rel="noopener noreferrer">NASA GIBS documentation</a></details>
  </section>;
}
function MonthlyRainfall({ months }: { months: Month[] }) {
  const [selected, setSelected] = useState(months.at(-1)?.month || ''); const row = months.find(m => m.month === selected) || months.at(-1);
  if (!row) return <p className="geo-muted">Monthly history is not available.</p>;
  const maximum = Math.max(1, ...months.flatMap(m => [m.mm, m.normal].filter((v): v is number => v !== null)));
  return <section className="geo-months"><h4>Explore monthly rainfall</h4><label>Observation month<select aria-label="Rainfall observation month" value={row.month} onChange={e => setSelected(e.target.value)}>{months.map(m => <option key={m.month}>{m.month}</option>)}</select></label>
    <p aria-live="polite">{row.month}: {formatNumber(row.mm)} mm · normal {formatNumber(row.normal)} mm · departure {formatNumber(row.departure)}%.{row.lowNormal ? ' Normal is below 10 mm; small amounts can produce large departures.' : ''}</p>
    <svg className="geo-chart" viewBox="0 0 640 160" role="img" aria-label="Monthly rainfall bars; full values are in the table below.">{months.map((m, i) => { const step = 624 / months.length, x = 8 + i * step; return <g key={m.month}>{(['normal', 'mm'] as const).map((key, j) => m[key] === null ? null : <rect key={key} className={key === 'mm' ? 'geo-bar-observed' : 'geo-bar-normal'} x={x + j * step * .4} y={150 - m[key]! / maximum * 140} width={step * .35} height={Math.max(1, m[key]! / maximum * 140)}><title>{m.month} {key === 'mm' ? 'observed' : 'normal'}: {formatNumber(m[key])} mm</title></rect>)}</g>; })}</svg>
    <p className="geo-muted">Paired bars: muted = normal; green = observed. Missing values have no bar.</p>
    <details><summary>Show all monthly values</summary><div className="geo-table-wrap"><table><caption>CHIRPS rainfall; normal is the 1991–2020 mean.</caption><thead><tr>{['Month', 'Rainfall (mm)', 'Normal (mm)', 'Departure (%)', 'Context'].map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead><tbody>{months.map(m => <tr key={m.month}><th scope="row">{m.month}</th><td>{formatNumber(m.mm)}</td><td>{formatNumber(m.normal)}</td><td>{formatNumber(m.departure)}</td><td>{m.lowNormal ? 'Low normal: less than 10 mm' : ''}</td></tr>)}</tbody></table></div></details>
  </section>;
}
function DistrictView({ district, load, standalone }: { district: District; load: (id: string) => void; standalone: boolean }) {
  return <article className="geo-district">{!standalone && <div className="geo-section-heading"><h3>{district.name}, {district.state}</h3><button onClick={() => load(district.id)}>Open district history</button></div>}
    <div className="geo-metrics">{district.metrics.map(m => <article key={m.label}><h4>{m.label}</h4><strong>{m.value}</strong><p>{m.date}</p><p className="geo-muted">{m.detail}</p></article>)}</div>
    <p>Declared drought years: {district.drought === null ? 'Not available' : district.drought.length ? district.drought.join(', ') : 'None listed in this source'}.{district.droughtSource ? ` Source: ${district.droughtSource}.` : ''}</p>
    <MonthlyRainfall key={district.id} months={district.months} />
  </article>;
}
function DistrictEvidence() {
  const loader = useMemo(() => createEvidenceLoader(), []); const version = useRef(0);
  const [state, setState] = useState(''), [query, setQuery] = useState(''), [exactId, setExactId] = useState('');
  const [data, setData] = useState<Evidence | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [selection, setSelection] = useState<{ id: string; offset: number } | null>(null);
  const states = useMemo(() => [...new Set(GEOGRAPHY_PLACES.map(p => p.state))].sort(), []);
  const places = useMemo(() => GEOGRAPHY_PLACES.filter(p => !state || p.state === state), [state]);
  useEffect(() => () => { version.current++; loader.cancel(); }, [loader]);
  async function load(id: string, offset = 0) {
    const token = ++version.current; loader.cancel(); setData(null); setError(''); setBusy(true); setSelection({ id, offset });
    try { const result = await loader.load(id, offset); if (token === version.current && result) setData(result); }
    catch (e) { if (token === version.current) setError(message(e)); } finally { if (token === version.current) setBusy(false); }
  }
  function validationError(message: string) { version.current++; loader.cancel(); setData(null); setBusy(false); setError(message); }
  function find(event: FormEvent) { event.preventDefault(); const q = query.trim().toLowerCase(), hits = places.filter(p => `${p.name}, ${p.state}`.toLowerCase() === q || p.name.toLowerCase() === q || p.id === q); if (hits.length === 1) void load(hits[0].id); else { validationError(hits.length > 1 ? 'More than one district has this name. Include its state or select from the list.' : 'Choose a district from the list.'); } }
  return <section className="geo-panel" aria-labelledby="geography-evidence-title"><div className="geo-section-heading"><h2 id="geography-evidence-title">Explore district evidence</h2><span className="geo-muted">Independent of the image viewport</span></div>
    <p className="geo-muted">Research from LiquiLens, using 2021 district boundaries. Bank links describe areas of operation, not lending shares. These observations do not change Seiche’s funding indicators or authorize credit decisions.</p>
    <div className="geo-controls"><label>Browse a state<select aria-label="Filter districts by state" value={state} onChange={e => { setState(e.target.value); setQuery(''); }}><option value="">All India</option>{states.map(s => <option key={s}>{s}</option>)}</select></label><button disabled={!state} onClick={() => void load(`in-state-${state.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`)}>Browse state evidence</button>
      <form className="geo-controls" onSubmit={find}><label>Find a district<input list="seiche-geography-districts" value={query} onChange={e => setQuery(e.target.value)} placeholder="Type a district name" autoComplete="off" /></label><datalist id="seiche-geography-districts">{places.map(p => <option key={p.id} value={`${p.name}, ${p.state}`} />)}</datalist><button type="submit">Open district</button></form></div>
    <div className="geo-example-links"><button onClick={() => void load('in-dist-rajasthan-jaipur')}>Jaipur district</button><button onClick={() => void load('in-dccb-rajasthan-jaipur')}>Jaipur cooperative bank</button><button onClick={() => void load('in-state-rajasthan')}>Rajasthan districts</button></div>
    <details><summary>Look up an exact cooperative-bank or district ID</summary><form className="geo-controls" onSubmit={e => { e.preventDefault(); if (validPlace(exactId)) void load(exactId); else validationError('Enter a valid district, state or cooperative-bank ID.'); }}><label>Evidence ID<input value={exactId} onChange={e => setExactId(e.target.value.trim())} maxLength={120} placeholder="in-dccb-rajasthan-jaipur" required /></label><button type="submit">Load evidence</button></form></details>
    <p role="status" aria-live="polite" className={`geo-status${error ? ' geo-error' : ''}`}>{busy ? 'Loading district evidence…' : error || (data ? `${data.name} · ${data.status}. Response dated ${data.generated}; observation dates appear separately.` : 'All India · choose a district or state to load its evidence.')}</p>
    {error && selection && <button onClick={() => void load(selection.id, selection.offset)}>Retry evidence</button>}
    {data && <div className="geo-results"><h3>{data.name}</h3>{data.reason && <p>{data.reason}</p>}{data.districts.map(d => <DistrictView key={d.id} district={d} standalone={data.id.startsWith('in-dist-')} load={id => void load(id)} />)}
      {!data.districts.length && <p className="geo-muted">No district observations are available for this selection.</p>}
      {data.total > 1 && <nav className="geo-pagination" aria-label="District pages"><button disabled={data.offset === 0} onClick={() => void load(data.id, Math.max(0, data.offset - 5))}>Previous districts</button><span>Districts {data.offset + 1}–{data.offset + data.districts.length} of {data.total}</span><button disabled={data.nextOffset === null} onClick={() => { if (data.nextOffset !== null) void load(data.id, data.nextOffset); }}>Next districts</button></nav>}
      {!!data.institutions.length && <details><summary>Linked cooperative banks ({data.institutions.length})</summary><ul>{data.institutions.map(bank => <li key={bank.id}><button onClick={() => void load(bank.id)}>{bank.id}</button> <span className="geo-muted">{bank.basis}</span></li>)}</ul></details>}
      <details><summary>Sources and interpretation limits</summary><ul>{data.sources.map((s, i) => <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{s.label}</a> : s.label}<p className="geo-muted">{s.detail}</p></li>)}</ul><ul>{data.limits.map((limit, i) => <li key={i}>{limit}</li>)}</ul></details>
    </div>}
  </section>;
}
export default function Geography() {
  return <div className="geo-shell"><header className="geo-intro"><div><p className="geo-kicker">India geography · Research</p><h1>See the landscape.<br />Read the evidence.</h1><p>Compare dated satellite images across India, then inspect the district observations behind lender-geography research.</p></div><aside>Satellite imagery and district measurements have independent dates and sources. <a href="https://liquilens.in/banking/geography/">Open the LiquiLens explorer ↗</a></aside></header><SatelliteViewer /><DistrictEvidence /></div>;
}
