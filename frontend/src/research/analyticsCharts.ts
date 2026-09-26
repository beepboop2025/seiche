/** Small SVG research charts. Source adapters own validation and financial meaning. */
export type Point = {x: number; y: number | null; label?: string; note?: string; id?: string};
export type Series = {name: string; points: Point[]; color?: string};
export type Row = {label: string; value: number | null; note?: string; color?: string; start?: number; end?: number};
export type MatrixRow = {label: string; values: (number | null)[]; notes?: string[]};
export type ChartSpec = {
  id: string; number: string; title: string; description: string; unit: string;
  kind?: 'line' | 'scatter' | 'bar' | 'matrix' | 'waterfall'; series?: Series[]; rows?: Row[];
  matrix?: MatrixRow[]; columns?: string[]; xType?: 'date' | 'linear' | 'log'; xLabel?: string;
  domain?: [number, number]; zero?: boolean; maxGapDays?: number; note: string;
  sources: {label: string; url: string}[]; onSelect?: (id: string) => void; empty?: string;
};
export const COLORS = ['#91baff', '#e8b76f', '#80d1c5', '#c0a5eb', '#ec91a2', '#bdcf83'];
const NS = 'http://www.w3.org/2000/svg';
const narrow = () => typeof matchMedia !== 'undefined' && matchMedia('(max-width:600px)').matches;
const chartWidth = () => narrow() ? 400 : 680;
if (typeof matchMedia !== 'undefined') matchMedia('(max-width:600px)').addEventListener('change', () => {
  document.querySelectorAll('.ac-card').forEach(card => card.dispatchEvent(new Event('analytics:resize')));
});
export const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
export const number = (value: number, digits = 2) => new Intl.NumberFormat('en-US', value !== 0 && Math.abs(value) < 10 ** -digits ? {maximumSignificantDigits: 3} : {maximumFractionDigits: digits}).format(value);
export const compact = (value: number) => new Intl.NumberFormat('en-US', {notation: 'compact', maximumFractionDigits: 1}).format(value);
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag); if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls; return node;
}
function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>, text?: string): SVGElementTagNameMap[K] {
  const node = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text !== undefined) node.textContent = text; return node;
}
export function csv(rows: unknown[][]): string {
  return rows.map(row => row.map(value => {
    let text = value == null ? '' : String(value);
    if (typeof value !== 'number' && /^[\s\u0000-\u001f]*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  }).join(',')).join('\r\n') + '\r\n';
}
export function download(name: string, text: string, type = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const anchor = el('a'); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function dateValue(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : null;
}
/** An exact-date join: no forward fills or interpolation across reporting clocks. */
export function difference(a: Point[], b: Point[], scale = 1): Point[] {
  const right = new Map(b.map(p => [p.x, p]));
  return a.flatMap(p => { const q = right.get(p.x);
    return q && finite(p.y) && finite(q.y) ? [{x: p.x, y: (p.y - q.y) * scale, label: p.label}] : [];
  });
}
export function windowPoints(points: Point[], days: number | null, end?: number): Point[] {
  if (!days || !points.length) return points;
  const last = end ?? Math.max(...points.map(p => p.x));
  return points.filter(p => p.x >= last - days * 86400000 && p.x <= last);
}
export function changes(points: Point[], lag = 1, scale = 1): Point[] {
  return points.slice(lag).map((p, i) => ({...p, y: finite(p.y) && finite(points[i].y) ? (p.y - points[i].y!) * scale : null}));
}
function chartRows(spec: ChartSpec): unknown[][] {
  if (spec.kind === 'matrix') return [['Record', ...(spec.columns || []), 'Notes'], ...(spec.matrix || []).map(r => [r.label, ...r.values, r.notes?.join(' | ') || ''])];
  if (spec.rows) return [['Record', spec.unit, 'Start', 'End', 'Note'], ...spec.rows.map(r => [r.label, r.value, r.start, r.end, r.note])];
  return [['Series', spec.xLabel || 'Observation date', spec.unit, 'Label', 'Note'], ...(spec.series || []).flatMap(s => s.points.map(p => [s.name, spec.xType === 'date' ? new Date(p.x).toISOString().slice(0, 10) : p.x, p.y, p.label, p.note]))];
}
function axisLabel(value: number, spec: ChartSpec): string {
  return spec.xType === 'date' ? new Date(value).toLocaleDateString('en-GB', {month: 'short', year: '2-digit', timeZone: 'UTC'}) : compact(value);
}
function domain(values: number[], zero = false): [number, number] {
  let low = Math.min(...values), high = Math.max(...values);
  if (!values.length) return [0, 1];
  if (zero) {low = Math.min(0, low); high = Math.max(0, high);}
  const pad = Math.max((high - low) * .1, Math.abs(high) * .025, .01);
  return [zero && low === 0 ? 0 : low - pad, zero && high === 0 ? 0 : high + pad];
}
function frame(label: string, height = 270) {
  const root = svg('svg', {viewBox: `0 0 ${chartWidth()} ${height}`, role: 'img', 'aria-label': label, class: 'ac-svg'});
  root.append(svg('title', {}, label)); return root;
}
const ink = '#dbe3ef', muted = '#9eaabc', grid = '#283342';
function label(root: SVGSVGElement, x: number, y: number, text: string, anchor = 'start', color = muted) {
  root.append(svg('text', {x, y, 'text-anchor': anchor, fill: color, 'font-size': 12, 'font-family': 'system-ui, sans-serif'}, text));
}
function renderPlot(spec: ChartSpec, active: Set<string>, readout: HTMLElement): SVGSVGElement | null {
  const series = (spec.series || []).filter(s => active.has(s.name));
  const points = series.flatMap(s => s.points).filter(p => finite(p.x) && finite(p.y) && (spec.xType !== 'log' || p.x > 0));
  if (!points.length) return null;
  const root = frame(`${spec.title}. ${spec.xLabel || 'Observation date'}; ${spec.unit}. Arrow keys inspect observations.`);
  const left = narrow() ? 50 : 66, right = chartWidth() - 26, top = 20, bottom = 216;
  const allX = points.map(p => spec.xType === 'log' ? Math.log10(p.x) : p.x);
  let minX = Math.min(...allX), maxX = Math.max(...allX);
  if (spec.kind === 'scatter') [minX, maxX] = domain(allX, spec.zero);
  else if (minX === maxX) {minX -= .5; maxX += .5;}
  const [minY, maxY] = spec.domain || domain(points.map(p => p.y!), spec.zero);
  const x = (v: number) => left + ((spec.xType === 'log' ? Math.log10(v) : v) - minX) / (maxX - minX) * (right - left);
  const y = (v: number) => bottom - (v - minY) / (maxY - minY) * (bottom - top);
  for (let i = 0; i <= 4; i++) {
    const value = minY + i / 4 * (maxY - minY), py = y(value);
    root.append(svg('line', {x1: left, x2: right, y1: py, y2: py, stroke: grid, 'stroke-dasharray': '3 5'}));
    label(root, left - 10, py + 4, Math.abs(value) >= 10000 ? compact(value) : number(value, 2), 'end');
  }
  if (minY < 0 && maxY > 0) root.append(svg('line', {x1: left, x2: right, y1: y(0), y2: y(0), stroke: muted, 'stroke-width': 1}));
  for (let i = 0; i <= 4; i++) {
    const v = minX + i / 4 * (maxX - minX), px = left + i / 4 * (right - left);
    root.append(svg('line', {x1: px, x2: px, y1: top, y2: bottom, stroke: grid, opacity: .35}));
    label(root, px, 238, axisLabel(spec.xType === 'log' ? 10 ** v : v, spec), i === 0 ? 'start' : i === 4 ? 'end' : 'middle');
  }
  label(root, left, 12, spec.unit); label(root, right, 260, spec.xLabel || 'Observation date · UTC', 'end');
  const hits: {p: Point; name: string; color: string}[] = [];
  const labels: {x: number; y: number; width: number}[] = [];
  for (const s of series) {
    const color = s.color || COLORS[(spec.series || []).indexOf(s) % COLORS.length];
    let d = '', previous: Point | null = null;
    for (const p of s.points) {
      if (!finite(p.y) || !finite(p.x) || (spec.xType === 'log' && p.x <= 0)) {previous = null; continue;}
      const gap = spec.xType === 'date' && previous && p.x - previous.x > (spec.maxGapDays || 10) * 86400000;
      d += `${previous && !gap ? 'L' : 'M'}${x(p.x).toFixed(2)},${y(p.y).toFixed(2)} `;
      previous = p; hits.push({p, name: s.name, color});
    }
    if (spec.kind !== 'scatter') root.append(svg('path', {d, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': (spec.series || []).indexOf(s) % 2 ? '6 3' : 'none'}));
    const dots = spec.kind === 'scatter' || s.points.length <= 24 ? s.points : s.points.slice(-1);
    for (const p of dots) {
      if (!finite(p.y) || (spec.xType === 'log' && p.x <= 0)) continue;
      const dot = svg('circle', {cx: x(p.x), cy: y(p.y), r: spec.kind === 'scatter' ? 5 : 3.3, fill: color, stroke: '#0d131c', 'stroke-width': 1.5});
      dot.append(svg('title', {}, `${p.label || s.name}: ${number(p.y, 4)} ${spec.unit}. ${p.note || ''}`));
      if (spec.kind === 'scatter' && p.label) {
        const maxLabel = narrow() ? 10 : 16;
        const text = p.label.length > maxLabel ? p.label.slice(0, maxLabel - 2) + '…' : p.label;
        const width = text.length * 6.5, px = Math.min(right - width, x(p.x) + 8);
        for (const offset of [-10, 20, -26, 36]) {
          const py = y(p.y) + offset;
          if (py < top + 10 || py > bottom || labels.some(l => Math.abs(l.y - py) < 14 && px < l.x + l.width + 5 && px + width > l.x - 5)) continue;
          label(root, px, py, text, 'start', color); labels.push({x: px, y: py, width}); break;
        }
      }
      root.append(dot);
    }
  }
  hits.sort((a, b) => a.p.x - b.p.x);
  const guide = svg('line', {x1: left, x2: left, y1: top, y2: bottom, stroke: muted, 'stroke-dasharray': '3 4', opacity: 0});
  const cursor = svg('circle', {cx: left, cy: top, r: 5, fill: '#fff', opacity: 0}); root.append(guide, cursor);
  const targets = spec.kind === 'scatter' ? hits : hits.filter((h, i) => i === 0 || h.p.x !== hits[i - 1].p.x);
  let selected = targets.length - 1;
  const show = (index: number, announce = false) => {
    selected = Math.max(0, Math.min(targets.length - 1, index)); const h = targets[selected];
    guide.setAttribute('x1', String(x(h.p.x))); guide.setAttribute('x2', String(x(h.p.x))); guide.setAttribute('opacity', '.7');
    cursor.setAttribute('cx', String(x(h.p.x))); cursor.setAttribute('cy', String(y(h.p.y!))); cursor.setAttribute('opacity', '1'); cursor.setAttribute('fill', h.color);
    const clock = spec.xType === 'date' ? new Date(h.p.x).toISOString().slice(0, 10) : h.p.label || `${spec.xLabel}: ${number(h.p.x)}`;
    const exact = spec.kind === 'scatter' ? [h] : hits.filter(item => item.p.x === h.p.x);
    readout.setAttribute('aria-live', announce ? 'polite' : 'off'); readout.setAttribute('aria-atomic', 'true');
    readout.textContent = `${clock} · ` + exact.map(item => `${item.name}: ${number(item.p.y!, 4)} ${spec.unit}`).join(' · ') + (h.p.note ? ` · ${h.p.note}` : '');
  };
  root.setAttribute('tabindex', '0');
  const inspect = (event: PointerEvent) => {
    const box = root.getBoundingClientRect(), px = (event.clientX - box.left) / box.width * chartWidth(), py = (event.clientY - box.top) / box.height * 270;
    const distance = (h: typeof hits[number]) => Math.abs(x(h.p.x) - px) + (spec.kind === 'scatter' ? Math.abs(y(h.p.y!) - py) : 0);
    show(targets.reduce((best, h, i) => distance(h) < distance(targets[best]) ? i : best, 0));
  };
  root.addEventListener('pointermove', inspect); root.addEventListener('pointerdown', inspect);
  root.addEventListener('focus', () => show(selected, true));
  root.addEventListener('keydown', event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {event.preventDefault(); show(event.key === 'Home' ? 0 : event.key === 'End' ? targets.length - 1 : selected + (event.key === 'ArrowRight' ? 1 : -1), true);}
    if (event.key === 'Enter' && targets[selected].p.id) spec.onSelect?.(targets[selected].p.id!);
  });
  root.addEventListener('click', () => {if (targets[selected].p.id) spec.onSelect?.(targets[selected].p.id!);});
  if (spec.kind !== 'scatter') show(selected);
  return root;
}
function renderBars(spec: ChartSpec, readout: HTMLElement) {
  const rows = spec.rows || [];
  if (!rows.some(r => finite(r.value))) return null;
  const waterfall = spec.kind === 'waterfall';
  const height = Math.max(250, rows.length * 36 + 68), root = frame(`${spec.title}. ${spec.unit}.`, height);
  const left = narrow() ? 128 : 168, right = chartWidth() - (narrow() ? 72 : 88), top = 25;
  const [lo, hi] = spec.domain || domain(rows.flatMap(r => waterfall && finite(r.start) && finite(r.end) ? [r.start, r.end] : finite(r.value) ? [r.value] : []), true);
  const x = (v: number) => left + (v - lo) / (hi - lo) * (right - left);
  for (let i = 0; i <= 4; i++) {
    const v = lo + i / 4 * (hi - lo), px = x(v);
    root.append(svg('line', {x1: px, x2: px, y1: top - 4, y2: height - 40, stroke: grid, 'stroke-dasharray': '3 5'}));
    label(root, px, height - 20, compact(v), 'middle');
  }
  label(root, right, 13, spec.unit, 'end');
  rows.forEach((r, i) => {
    const py = top + i * (height - 70) / rows.length;
    const maxLabel = narrow() ? 17 : 25;
    label(root, left - 12, py + 16, r.label.length > maxLabel ? r.label.slice(0, maxLabel - 2) + '…' : r.label, 'end');
    if (!finite(r.value)) {label(root, left + 8, py + 16, 'Unavailable'); return;}
    const from = waterfall ? r.start || 0 : 0, to = waterfall ? r.end ?? r.value : r.value;
    const color = r.color || (r.value < 0 ? COLORS[1] : COLORS[0]);
    const rect = svg('rect', {x: Math.min(x(from), x(to)), y: py + 3, width: Math.max(1, Math.abs(x(to) - x(from))), height: 18, rx: 2, fill: color, tabindex: 0, role: 'img', 'aria-label': `${r.label}: ${number(r.value, 4)} ${spec.unit}. ${r.note || ''}`});
    const describe = () => {readout.textContent = `${r.label} · ${number(r.value!, 4)} ${spec.unit} · ${r.note || ''}`;};
    rect.addEventListener('pointerenter', describe); rect.addEventListener('focus', describe); root.append(rect);
    label(root, right + 12, py + 16, number(r.value, Math.abs(r.value) < 1 ? 3 : 2), 'start', ink);
  });
  root.append(svg('line', {x1: x(0), x2: x(0), y1: top - 4, y2: height - 40, stroke: muted}));
  return root;
}
function renderMatrix(spec: ChartSpec, readout: HTMLElement) {
  const rows = spec.matrix || [], columns = spec.columns || [];
  if (!rows.length || !columns.length) return null;
  const height = Math.max(250, rows.length * 37 + 66), root = frame(`${spec.title}. ${spec.unit}. Missing values are labelled with a dash.`, height);
  const left = narrow() ? 106 : 153, width = (chartWidth() - left - 27) / columns.length, step = (height - 60) / rows.length;
  const max = spec.domain?.[1] || Math.max(1, ...rows.flatMap(r => r.values.filter(finite)));
  label(root, 4, 22, spec.unit.length > 18 ? 'Disclosures' : spec.unit);
  columns.forEach((c, i) => {const max = Math.max(4, Math.floor(width / 6.5)); label(root, left + i * width + width / 2, 22, c.length > max ? c.slice(0, max - 1) + '…' : c, 'middle');});
  rows.forEach((r, i) => {
    const maxLabel = narrow() ? 13 : 23;
    label(root, left - 12, 53 + i * step, r.label.length > maxLabel ? r.label.slice(0, maxLabel - 2) + '…' : r.label, 'end');
    r.values.forEach((value, j) => {
      const px = left + j * width, py = 35 + i * step;
      const rect = svg('rect', {x: px + 2, y: py, width: width - 4, height: step - 5, rx: 3, fill: finite(value) ? COLORS[0] : '#212a37', 'fill-opacity': finite(value) ? .13 + .62 * Math.min(1, Math.max(0, value / max)) : 1, tabindex: 0, role: 'img', 'aria-label': `${r.label}, ${columns[j]}: ${finite(value) ? number(value, 3) + ' ' + spec.unit : 'Unavailable'}. ${r.notes?.[j] || ''}`});
      const describe = () => {readout.textContent = `${r.label} · ${columns[j]}: ${finite(value) ? number(value, 4) + ' ' + spec.unit : 'Unavailable'} · ${r.notes?.[j] || ''}`;};
      rect.addEventListener('pointerenter', describe); rect.addEventListener('focus', describe); root.append(rect);
      label(root, px + width / 2, py + (step - 5) / 2 + 4, finite(value) ? number(value, 2) : '—', 'middle', ink);
    });
  });
  return root;
}
export function chartCard(spec: ChartSpec): HTMLElement {
  const card = el('article', undefined, 'ac-card'); card.id = spec.id; card.dataset.chart = spec.kind || 'line';
  const header = el('header', undefined, 'ac-card-head'), text = el('div');
  text.append(el('span', `${spec.number} / ${spec.kind === 'matrix' ? 'MATRIX' : spec.kind === 'scatter' ? 'RELATIONSHIP' : spec.kind === 'waterfall' ? 'RECONCILIATION' : spec.kind === 'bar' ? 'COMPARISON' : spec.xType === 'date' ? 'TIME SERIES' : 'CURVE'}`, 'ac-eyebrow'));
  const heading = el('h3', spec.title); heading.id = `${spec.id}-heading`; card.setAttribute('aria-labelledby', heading.id);
  text.append(heading, el('p', spec.description, 'ac-description')); header.append(text);
  const expand = el('button', '↗', 'ac-expand'); expand.type = 'button'; expand.setAttribute('aria-label', `Expand ${spec.title}`); header.append(expand); card.append(header);
  const visual = el('div', undefined, 'ac-visual'), readout = el('p', 'Hover or focus the chart to inspect values. Use arrow keys on time series.', 'ac-readout');
  const active = new Set((spec.series || []).map(s => s.name));
  const draw = () => {
    const plot = spec.kind === 'matrix' ? renderMatrix(spec, readout) : spec.rows ? renderBars(spec, readout) : renderPlot(spec, active, readout);
    visual.replaceChildren(plot || el('p', spec.empty || 'There are no qualified observations for this view. Missing data is not zero.', 'ac-empty'));
    card.dataset.available = String(Boolean(plot));
  };
  card.addEventListener('analytics:resize', draw);
  if (spec.series?.length) {
    const legend = el('div', undefined, 'ac-legend'); legend.setAttribute('aria-label', 'Displayed series');
    spec.series.forEach((s, i) => {
      const button = el('button', s.name); button.type = 'button'; button.setAttribute('aria-pressed', 'true');
      button.style.setProperty('--series-color', s.color || COLORS[i % COLORS.length]);
      button.addEventListener('click', () => {if (active.has(s.name)) {if (active.size === 1) return; active.delete(s.name);} else active.add(s.name); button.setAttribute('aria-pressed', String(active.has(s.name))); draw();});
      legend.append(button);
    }); card.append(legend);
  }
  draw(); card.append(visual, readout, el('p', spec.note, 'ac-note'));
  const footer = el('footer', undefined, 'ac-card-foot'), details = el('details');
  details.append(el('summary', 'Data & sources'));
  const tableWrap = el('div', undefined, 'ac-table-wrap'), rows = chartRows(spec);
  let tableBuilt = false;
  details.addEventListener('toggle', () => {
    if (!details.open || tableBuilt) return; tableBuilt = true;
    const table = el('table'); table.append(el('caption', `${spec.title} · ${spec.unit}`));
    const head = el('thead'), tr = el('tr'); rows[0].forEach(v => {const th = el('th', String(v)); th.scope = 'col'; tr.append(th);}); head.append(tr); table.append(head);
    const tbody = el('tbody');
    rows.slice(1).forEach(row => {const tr = el('tr'); row.forEach(value => tr.append(el('td', value == null ? 'Unavailable' : String(value)))); tbody.append(tr);});
    table.append(tbody); tableWrap.append(table);
  });
  details.append(tableWrap);
  spec.sources.forEach(source => {const a = el('a', source.label); a.href = source.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; details.append(a);});
  const exportCSV = el('button', '↓ CSV'); exportCSV.type = 'button'; exportCSV.setAttribute('aria-label', `Export ${spec.title} as CSV`);
  exportCSV.addEventListener('click', () => download(`${spec.id}.csv`, csv([...rows, [], ['Method', spec.note], ...spec.sources.map(s => ['Source', s.label, s.url])])));
  const exportSVG = el('button', '↓ SVG'); exportSVG.type = 'button'; exportSVG.setAttribute('aria-label', `Export ${spec.title} as SVG`);
  exportSVG.addEventListener('click', () => {
    const source = visual.querySelector('svg'); if (!source) return;
    const copy = source.cloneNode(true) as SVGSVGElement; copy.setAttribute('xmlns', NS); copy.style.background = '#101722';
    copy.prepend(svg('desc', {}, `${spec.title}. ${spec.note}. ${spec.sources.map(s => s.url).join(' ')}`));
    download(`${spec.id}.svg`, new XMLSerializer().serializeToString(copy), 'image/svg+xml');
  });
  footer.append(details, exportCSV, exportSVG); card.append(footer);
  expand.addEventListener('click', () => {
    const placeholder = document.createComment('expanded chart'), dialog = el('dialog', undefined, 'ac-dialog'), close = el('button', 'Close expanded chart ×', 'ac-close');
    close.type = 'button'; dialog.setAttribute('aria-label', spec.title); card.before(placeholder); dialog.append(close, card); document.body.append(dialog);
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {placeholder.replaceWith(card); dialog.remove(); expand.focus();}, {once: true});
    dialog.showModal(); close.focus();
  });
  return card;
}
export function dashboard(target: HTMLElement, options: {eyebrow: string; title: string; description: string}) {
  target.className = 'ac-dashboard';
  const header = el('header', undefined, 'ac-dashboard-head'), copy = el('div');
  copy.append(el('p', options.eyebrow, 'ac-eyebrow'), el('h2', options.title), el('p', options.description, 'ac-intro'));
  const status = el('p', 'Loading dated observations…', 'ac-status'); status.setAttribute('role', 'status');
  header.append(copy, status);
  const controls = el('div', undefined, 'ac-controls'), stats = el('div', undefined, 'ac-stats'), grid = el('div', undefined, 'ac-grid');
  target.replaceChildren(header, controls, stats, grid); return {controls, stats, grid, status};
}
export function stats(target: HTMLElement, rows: [string, string, string][]) {
  target.replaceChildren(...rows.map(([label, value, note]) => {const item = el('div'); item.append(el('span', label), el('strong', value), el('small', note)); return item;}));
}
export function selectControl(label: string, options: [string, string][], value: string, change: (value: string) => void) {
  const wrapper = el('label', undefined, 'ac-control'), select = el('select'); select.setAttribute('aria-label', label); wrapper.append(el('span', label), select);
  options.forEach(([key, text]) => {const option = el('option', text); option.value = key; select.append(option);}); select.value = value;
  select.addEventListener('change', () => change(select.value)); return wrapper;
}
