/** Public, research-only geography contract. Raw JSON is validated before entering the UI. */
export type Box = [number, number, number, number];
export type Metric = { label: string; value: string; date: string; detail: string };
export type Month = { month: string; mm: number | null; normal: number | null; departure: number | null; lowNormal: boolean };
export type District = { id: string; name: string; state: string; metrics: Metric[]; months: Month[]; drought: string[] | null; droughtSource: string };
export type Evidence = { id: string; name: string; generated: string; status: string; reason: string; districts: District[]; offset: number; nextOffset: number | null; total: number; institutions: { id: string; basis: string }[]; sources: { label: string; detail: string; url: string | null }[]; limits: string[] };
export const GEO_API = 'https://api.liquilens.in/api/experimental/v1/banking/lending-geography/';
export const PRESETS: Record<string, { name: string; bbox: Box }> = {
  india: { name: 'India overview', bbox: [66, 5, 99, 38] }, north: { name: 'North India', bbox: [71, 24, 83, 36] },
  west: { name: 'West India', bbox: [67, 18, 79, 30] }, east: { name: 'East and northeast India', bbox: [80, 20, 98, 32] },
  south: { name: 'South India', bbox: [72, 6, 85, 19] }, jaipur: { name: 'Jaipur surroundings', bbox: [74.6, 25.9, 77.1, 28.4] },
};
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const text = (v: unknown): string => typeof v === 'string' && v.length <= 2000 ? v : '';
// JSON fields remain unknown until a particular field validator accepts them.
const record = (v: unknown): Record<string, unknown> => {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error('The geography evidence contract could not be verified.');
  return v as Record<string, unknown>;
};
const fail = (): never => { throw new Error('The geography evidence contract could not be verified.'); };
export const formatNumber = (v: unknown): string => finite(v) ? new Intl.NumberFormat('en-IN', { maximumFractionDigits: 3 }).format(v) : 'Not available';
export function validPlace(v: unknown): v is string { return typeof v === 'string' && v.length <= 120 && /^in-(?:dist|state|dccb|stcb)-[a-z0-9]+(?:-[a-z0-9]+)*$/.test(v); }
export function safeSource(v: unknown): string | null { try { const u = new URL(String(v)); return u.protocol === 'https:' && !u.username && !u.password ? u.href : null; } catch { return null; } }
const day = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v;
const month = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-(?:0[1-9]|1[0-2])$/.test(v);
function observationDate(v: unknown): v is string {
  if (day(v)) return true;
  if (typeof v !== 'string' || !/^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$/.test(v)) return false;
  const year = Number(v.slice(0, 4)), week = Number(v.slice(6));
  const jan1 = new Date(Date.UTC(year, 0, 1)).getUTCDay();
  return week < 53 || jan1 === 4 || (jan1 === 3 && new Date(Date.UTC(year, 1, 29)).getUTCMonth() === 1);
}
function num(v: unknown, nonnegative = false): number | null { if (v == null) return null; if (!finite(v) || nonnegative && v < 0) return fail(); return v; }
const unit = (v: number | null, suffix: string): string => v === null ? 'Not available' : `${formatNumber(v)}${suffix}`;
export function recentMonths(value: unknown): Month[] {
  if (value == null) return [];
  const r = record(value), values = r.mm, normals = r.normal_mm, departures = r.departure_pct;
  if (!month(r.from) || !Array.isArray(values) || values.length > 120 || !Array.isArray(normals) || !Array.isArray(departures) || values.length !== normals.length || values.length !== departures.length) return fail();
  const start = r.from, low = new Set(Array.isArray(r.low_normal_months) ? r.low_normal_months : []);
  return values.map((v, i) => { const d = new Date(`${start}-01T00:00:00Z`); d.setUTCMonth(d.getUTCMonth() + i); const label = d.toISOString().slice(0, 7); return { month: label, mm: num(v, true), normal: num(normals[i], true), departure: num(departures[i]), lowNormal: low.has(label) }; });
}
function districtModel(value: unknown, sources: Record<string, unknown>): District {
  const d = record(value);
  if (!validPlace(d.id) || !d.id.startsWith('in-dist-') || !text(d.name) || !text(d.state)) return fail();
  const metrics: Metric[] = [ ['jjas', 'Monsoon rainfall', 'June–September'], ['ond', 'Post-monsoon rainfall', 'October–December'], ['fiscal_year', 'Full fiscal-year rainfall', 'April–March'], ['fiscal_year_to_date', 'Fiscal year to date', 'April onward'] ].map(([key, label, period]) => {
    if (d[key] == null) return { label, value: 'Not available', date: 'Date unavailable', detail: 'No observation supplied.' };
    const r = record(d[key]);
    if (!(typeof r.year === 'number' && Number.isInteger(r.year) && r.year >= 1900 && r.year <= 2200 || typeof r.year === 'string' && /^\d{4}-\d{2}$/.test(r.year)) || r.through != null && !month(r.through)) return fail();
    const amount = num(r.mm, true), normal = num(r.normal_mm, true), departure = num(r.departure_pct);
    return { label, value: unit(amount, ' mm'), date: `${r.year} · ${period}${r.through ? ` · through ${r.through}` : ''}`, detail: `Normal ${unit(normal, ' mm')} · departure ${unit(departure, '%')}${departure !== null && text(r.category) ? ` · ${r.category}` : ''}` };
  });
  for (const [key, label, field, suffix] of [['vegetation_health', 'Vegetation health', 'vhi', ' / 100'], ['soil_moisture', 'Soil moisture', 'sm_m3_m3', ' m³/m³']]) {
    if (d[key] == null) { metrics.push({ label, value: 'Not available', date: 'Date unavailable', detail: 'No observation supplied for this district.' }); continue; }
    const r = record(d[key]), s = record(sources[key]);
    if (!observationDate(r.as_of) || s.as_of !== r.as_of || !text(s.product) || !text(s.credit)) return fail();
    const v = num(r[field], true); if (v !== null && v > (key === 'vegetation_health' ? 100 : 1)) return fail();
    metrics.push({ label, value: unit(v, suffix), date: `${r.as_of} · historical observation`, detail: `${s.product} · ${s.credit}` });
  }
  let drought: string[] | null = null, droughtSource = '';
  if (d.declared_drought != null) { const r = record(d.declared_drought); if (!Array.isArray(r.years) || r.years.some(y => typeof y !== 'string' || !/^\d{4}-\d{2}$/.test(y))) return fail(); drought = r.years as string[]; droughtSource = text(r.source); }
  return { id: d.id, name: text(d.name), state: text(d.state), metrics, months: recentMonths(d.recent_months), drought, droughtSource };
}
export function evidenceModel(value: unknown, expectedId: string, expectedOffset = 0): Evidence {
  const d = record(value);
  if (d.schema !== 'liquilens.lending-geography.v1' || d.scope !== 'research' || d.score_authority !== false || d.scoring_eligible !== false || d.training_corpus_changed !== false || !validPlace(d.id) || d.id !== expectedId || !day(d.as_of) || !['observed', 'mapped', 'unmapped', 'not_covered', 'unavailable'].includes(String(d.status))) return fail();
  const model: Evidence = { id: d.id, name: text(d.name) || text(d.state) || d.id, generated: d.as_of, status: String(d.status), reason: text(d.reason), districts: [], offset: 0, nextOffset: null, total: 0, institutions: [], sources: [], limits: [] };
  if (['unmapped', 'not_covered', 'unavailable'].includes(model.status)) return model;
  if (!['district', 'institution', 'state'].includes(String(d.kind))) return fail();
  const sources = record(d.sources), rows = d.kind === 'district' ? [d.district] : d.districts;
  if (!Array.isArray(rows) || rows.length > 5) return fail();
  if (d.kind !== 'district') {
    const offset = d.offset, total = d.districts_total, next = d.next_offset;
    if (typeof offset !== 'number' || !Number.isInteger(offset) || offset !== expectedOffset || typeof total !== 'number' || !Number.isInteger(total) || total < 0 || total > 1000 || offset > total || offset + rows.length > total || next != null && (typeof next !== 'number' || !Number.isInteger(next) || next !== offset + rows.length || next <= offset || next >= total) || next == null && offset + rows.length < total) return fail();
    model.offset = offset; model.total = total; model.nextOffset = next == null ? null : next as number;
  }
  model.districts = rows.map(row => districtModel(row, sources));
  if (d.kind === 'district') { if (model.districts[0].id !== d.id) return fail(); model.name = `${model.districts[0].name}, ${model.districts[0].state}`; model.total = 1; }
  model.institutions = (Array.isArray(d.institutions) ? d.institutions : []).flatMap(v => { const r = typeof v === 'string' ? { id: v } : record(v); return validPlace(r.id) && /^in-(?:dccb|stcb)-/.test(r.id) ? [{ id: r.id, basis: text(r.basis).replaceAll('_', ' ') }] : []; }).slice(0, 500);
  model.sources = Object.entries(sources).filter(([,v]) => v && typeof v === 'object' && !Array.isArray(v)).map(([key, v]) => { const s = record(v); return { label: text(s.product) || text(s.title) || key.replaceAll('_', ' '), detail: [s.credit, s.author, s.licence, s.as_of, s.through && `Through ${s.through}`, s.vintage && `Boundary vintage ${s.vintage}`].filter(v => typeof v === 'string').join(' · '), url: safeSource(s.url) }; });
  model.limits = (Array.isArray(d.limits) ? d.limits : []).filter((v): v is string => typeof v === 'string').slice(0, 20);
  return model;
}
export async function readBytes(response: Response, limit: number): Promise<Uint8Array<ArrayBuffer>> {
  if (!response.ok) throw new Error(response.status === 429 ? 'The free rate limit was reached. Wait a minute and retry.' : `The source returned HTTP ${response.status}. Retry later.`);
  const reader = response.body?.getReader(); if (!reader) throw new Error('The source returned an empty response.');
  const chunks: Uint8Array[] = []; let size = 0;
  try { while (true) { const { done, value } = await reader.read(); if (done) break; size += value.byteLength; if (size > limit) { await reader.cancel(); throw new Error('The response exceeds the display limit.'); } chunks.push(value); } } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let at = 0; for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.byteLength; } return bytes;
}
export function createEvidenceLoader(fetcher: typeof fetch = globalThis.fetch, timeoutMs = 15000) {
  let sequence = 0, controller: AbortController | undefined;
  return { cancel() { sequence++; controller?.abort(); }, async load(id: string, offset = 0): Promise<Evidence | null> {
    if (!validPlace(id) || !Number.isInteger(offset) || offset < 0 || offset > 1000) throw new Error('Choose a district, state or valid cooperative-bank ID.');
    const current = ++sequence; controller?.abort(); const active = new AbortController(); controller = active; let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; active.abort(); }, timeoutMs);
    try { const response = await fetcher(`${GEO_API}${encodeURIComponent(id)}?offset=${offset}`, { signal: active.signal, credentials: 'omit', headers: { Accept: 'application/json' } });
      if (response.status === 404) throw new Error('This district, state or bank is not covered. Choose from the list or check its exact ID.');
      const bytes = await readBytes(response, 256 * 1024), data: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
      return current === sequence ? evidenceModel(data, id, offset) : null;
    } catch (e) { if (current !== sequence) return null; if (timedOut) throw new Error('Evidence request timed out. Retry to load this place.'); throw e; } finally { clearTimeout(timer); }
  } };
}
export function validDate(v: unknown, today = new Date().toISOString().slice(0, 10)): v is string { return day(v) && v >= '2015-11-24' && v <= today; }
export function validBox(v: unknown): v is Box { return Array.isArray(v) && v.length === 4 && v.every(finite) && v[0] >= -180 && v[2] <= 180 && v[1] >= -85 && v[3] <= 85 && v[2] - v[0] >= .25 && v[2] - v[0] <= 90 && v[3] - v[1] >= .25 && v[3] - v[1] <= 90; }
export function moveBox(box: Box, scale = 1, dx = 0, dy = 0): Box {
  if (!validBox(box) || ![scale, dx, dy].every(finite) || scale <= 0) throw new Error('Invalid map view.');
  const w = Math.min(90, Math.max(.25, (box[2] - box[0]) * scale)), h = Math.min(90, Math.max(.25, (box[3] - box[1]) * scale));
  const x = Math.min(180 - w / 2, Math.max(-180 + w / 2, (box[0] + box[2]) / 2 + dx)), y = Math.min(85 - h / 2, Math.max(-85 + h / 2, (box[1] + box[3]) / 2 + dy));
  return [x - w / 2, y - h / 2, x + w / 2, y + h / 2].map(n => Number(n.toFixed(6))) as Box;
}
export function imageURL(date: string, box: Box): string {
  if (!validDate(date) || !validBox(box)) throw new Error('Choose valid observation dates and a map view.');
  return `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?${new URLSearchParams({ SERVICE: 'WMS', REQUEST: 'GetMap', VERSION: '1.1.1', LAYERS: 'MODIS_Terra_CorrectedReflectance_TrueColor', STYLES: '', FORMAT: 'image/jpeg', SRS: 'EPSG:4326', BBOX: box.join(','), WIDTH: '1024', HEIGHT: String(Math.min(2048, Math.max(128, Math.round(1024 * (box[3] - box[1]) / (box[2] - box[0]))))), TIME: date })}`;
}
export function imageryState(query: string): { before: string; after: string; box: Box } {
  const q = new URLSearchParams(query), box = q.get('view')?.split(',').map(Number), before = q.get('before'), after = q.get('after');
  return { before: validDate(before) ? before : '2025-11-02', after: validDate(after) ? after : '2026-11-02', box: validBox(box) ? box : [...PRESETS.india.bbox] };
}
