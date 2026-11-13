/** Dated presentation only. No interpolation, scoring or cross-currency ranking. */
export type Row = Record<string, any>;
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter(x => x && typeof x === 'object') : [];
export const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
export const shown = (v: unknown, unit = '') => finite(v) ? `${v.toLocaleString('en-US', {maximumFractionDigits: 3})}${unit ? ' ' + unit : ''}` : 'Unavailable';
const publishers: Record<string, string> = {boc_valet:'Bank of Canada',ecb_benchmark:'European Central Bank',ecb_policy:'European Central Bank',ecb_yield_curve:'European Central Bank',ecb_compounded:'European Central Bank',nyfed_rates:'Federal Reserve Bank of New York',nyfed_facilities:'Federal Reserve Bank of New York',fed_prates:'Federal Reserve',fred:'FRED',fiscaldata:'U.S. Treasury'};
export const publisher = (value: unknown) => typeof value === 'string' ? publishers[value] || value.replaceAll('_',' ') : 'Publisher unavailable';
export const clock = (value: unknown) => typeof value === 'string' && value ? value.replace('T',' ').replace('+00:00',' UTC').replace(/Z$/, ' UTC') : 'Not supplied';
export function sourceClocks(snapshot: Row, atlas: Row | null, catalog: Row | null): Row[] {
  const board = rows(snapshot?.provenance).map(p => ({...p, id: 'board:' + p.mnemonic, name:p.label || p.mnemonic, date:p.asof, captured:p.fetched_at, cadence:p.publication_frequency || p.freq, state:String(p.staleness || 'UNKNOWN').toUpperCase()}));
  const markets = rows(atlas?.markets).flatMap(m => rows(m.metrics).map(p => ({...p,id:'atlas:'+p.id,name:p.label || p.id,market:m.market_id,date:p.asof,captured:p.knowledge_time,state:String(p.status || 'UNKNOWN').toUpperCase()})));
  const found = new Set(markets.map(p => p.id.replace('atlas:','')));
  const rates = rows(catalog?.markets).flatMap(m => rows(m.instruments).filter(p => !found.has(p.instrument_id)).map(p => ({...p,id:'rates:'+p.instrument_id,name:p.label || p.instrument_id,market:m.market_id,date:p.last_observation,captured:p.knowledge_time || p.fetched_at,state:String(p.status || 'UNKNOWN').toUpperCase()})));
  return [...board,...markets,...rates];
}
export type PlotPoint = {x:number; label:string; value:number|null; comparison?:number|null; date?:string; source?:string; status?:string};
export function historyPoints(history: unknown): PlotPoint[] {
  if (!Array.isArray(history)) return [];
  return history.filter(p => Array.isArray(p) && /^\d{4}-\d{2}-\d{2}$/.test(p[0]) && (p[1] === null || finite(p[1])))
    .map(p => ({x:Date.parse(p[0]+'T00:00:00Z'),label:p[0],date:p[0],value:p[1]})).filter(p => Number.isFinite(p.x));
}
/** A missing node or absent calendar day breaks the stroke. The table retains exact observations. */
export function segments(points: PlotPoint[], key: 'value'|'comparison', maxGap = Infinity): PlotPoint[][] {
  const result: PlotPoint[][] = []; let segment: PlotPoint[] = [];
  for (const point of points) {
    if (!finite(point[key]) || (segment.length && point.x - segment[segment.length-1].x > maxGap)) {
      if (segment.length) result.push(segment); segment = [];
    }
    if (finite(point[key])) segment.push(point);
  }
  if (segment.length) result.push(segment);
  return result;
}
