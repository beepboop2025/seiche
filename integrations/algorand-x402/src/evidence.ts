/** Read-only, fixed-origin evidence assembly. Source clocks never become fetch clocks. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type RecordJson = { [key: string]: Json };
export type Fetcher = typeof fetch;
const MAX_SOURCE_BYTES = 1_048_576;
const TIMEOUT_MS = 12_000;
export const SOURCES = [
  { id: 'funding', product: 'Seiche', url: 'https://api.seiche.info/api/public', clock: 'generated_at', maxAgeSeconds: 7200 },
  { id: 'risk_context', product: 'Seiche', url: 'https://api.seiche.info/api/trade-safety/risk-context', clock: 'clocks.snapshot_generated_at', maxAgeSeconds: 7200 },
  { id: 'money_markets', product: 'Seiche', url: 'https://api.seiche.info/api/v2/money-markets', clock: 'generated_at', maxAgeSeconds: 7200 },
  { id: 'liquidity', product: 'Undertow', url: 'https://api.seiche.info/undertow/x402/summary', clock: 'asof', maxAgeSeconds: 172800 },
  { id: 'institutions', product: 'LiquiLens', url: 'https://api.liquilens.in/api/failure-radar/board', clock: 'as_of', maxAgeSeconds: 172800 },
] as const;
export type SourceId = typeof SOURCES[number]['id'];
export interface SourceResult {
  id: SourceId; product: string; url: string; status: 'AVAILABLE' | 'UNAVAILABLE' | 'STALE';
  captured_at: string; source_clock: string | null; source_age_seconds: number | null;
  reason: string | null; http_status: number | null; raw_sha256: string | null; data: RecordJson | null;
}
export function record(value: unknown): value is RecordJson {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function at(value: RecordJson, path: string): Json | undefined {
  return path.split('.').reduce<Json | undefined>((x, key) => record(x) ? x[key] : undefined, value);
}
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (record(value)) return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
export async function sha256(bytes: Uint8Array | string): Promise<string> {
  const input = typeof bytes === 'string' ? new TextEncoder().encode(bytes) : bytes;
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', input as Uint8Array<ArrayBuffer>))].map(b => b.toString(16).padStart(2, '0')).join('');
}
function pick(row: RecordJson, keys: string[]): RecordJson {
  return Object.fromEntries(keys.filter(k => k in row).map(k => [k, row[k]]));
}
function schemaValid(id: SourceId, d: RecordJson): boolean {
  if (id === 'funding') return d.schema === 'seiche.public.v2' && record(d.conclusion) && record(d.proof) && record(d.data_quality);
  if (id === 'risk_context') return d.schema === 'seiche.risk-context.v1' && record(d.clocks) && d.context_only === true && d.can_authorize_order === false && d.real_money_eligible === false;
  if (id === 'money_markets') return d.schema === 'seiche.global-money-markets.v1' && Array.isArray(d.markets) && record(d.coverage);
  if (id === 'liquidity') return Array.isArray(d.segment_reports) && (Array.isArray(d.segments) || record(d.segments));
  return Array.isArray(d.rows) && record(d.tiers);
}
async function readBounded(response: Response): Promise<Uint8Array> {
  const length = Number(response.headers.get('content-length'));
  if (length > MAX_SOURCE_BYTES) throw new Error('source_too_large');
  if (!response.body) throw new Error('source_empty');
  const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.length; if (size > MAX_SOURCE_BYTES) throw new Error('source_too_large');
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const part of chunks) { bytes.set(part, offset); offset += part.length; }
  return bytes;
}
export async function fetchSource(source: typeof SOURCES[number], fetcher: Fetcher = fetch, now = new Date()): Promise<SourceResult> {
  const out: SourceResult = { id: source.id, product: source.product, url: source.url, status: 'UNAVAILABLE', captured_at: now.toISOString(), source_clock: null, source_age_seconds: null, reason: null, http_status: null, raw_sha256: null, data: null };
  try {
    const res = await fetcher(source.url, { headers: { Accept: 'application/json' }, redirect: 'manual', signal: AbortSignal.timeout(TIMEOUT_MS) });
    out.http_status = res.status;
    if (!res.ok) { out.reason = `upstream_http_${res.status}`; await res.body?.cancel(); return out; }
    const bytes = await readBounded(res);
    out.raw_sha256 = await sha256(bytes);
    const data: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    if (!record(data) || !schemaValid(source.id, data)) { out.reason = 'source_schema_invalid'; return out; }
    const clock = at(data, source.clock);
    // Unknown clocks never acquire apparent freshness from a successful fetch.
    if (typeof clock !== 'string' || !/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(clock) || !Number.isFinite(Date.parse(clock))) { out.reason = 'source_clock_unknown'; return out; }
    out.source_clock = clock;
    out.source_age_seconds = Math.floor((now.getTime() - Date.parse(clock)) / 1000);
    if (out.source_age_seconds < -300) { out.reason = 'source_clock_in_future'; return out; }
    if (out.source_age_seconds > source.maxAgeSeconds) { out.status = 'STALE'; out.reason = 'source_snapshot_stale'; return out; }
    out.status = 'AVAILABLE'; out.data = data; return out;
  } catch (error) {
    out.reason = error instanceof Error && error.message === 'source_too_large' ? 'source_too_large' : 'source_read_failed';
    return out;
  }
}
const METADATA = ['id','mnemonic','label','semantic_role','availability','status','asof','event_time','published_at','knowledge_time','cadence','expected_next_update','missed_publication_opportunities','freshness_basis','observation_age_days','source','source_url','source_tier','redistribution_status','revision_status','revision_id','evidence_hash','confidence','formula','formula_version','explanation'];
function metric(value: Json | undefined, now: Date): RecordJson | null {
  if (!record(value)) return null;
  const out = pick(value, METADATA);
  const permitted = value.redistribution_status === 'allowed';
  const observation = typeof value.asof === 'string' ? Date.parse(value.asof) : NaN;
  const published = typeof value.published_at === 'string' ? Date.parse(value.published_at) : NaN;
  const known = typeof value.knowledge_time === 'string' ? Date.parse(value.knowledge_time) : NaN;
  const clocksValid = Number.isFinite(published) && Number.isFinite(known)
    && published <= now.getTime() + 300000 && known <= now.getTime() + 300000
    && published <= known + 300000 && observation <= published + 86400000;
  const finiteValue = typeof value.value === 'number' && Number.isFinite(value.value)
    && (value.canonical_value === undefined || (typeof value.canonical_value === 'number' && Number.isFinite(value.canonical_value)));
  const usable = finiteValue && clocksValid && permitted && value.availability === 'AVAILABLE' && ['FRESH', 'AGING'].includes(String(value.status)) && Number.isFinite(observation) && observation <= now.getTime() + 300000;
  // Descriptive metadata is retained; unknown/restricted/stale observations do not become paid numbers.
  out.numeric_observation_included = usable;
  if (usable) Object.assign(out, pick(value, ['value','unit','canonical_value','canonical_unit']));
  else out.withheld_reason = !permitted ? 'redistribution_not_explicitly_allowed' : 'observation_or_clocks_unavailable_stale_or_invalid';
  return out;
}
function compact(id: SourceId, data: RecordJson, now: Date): RecordJson {
  if (id === 'funding') return pick(data, ['schema','generated_at','conclusion','proof','editorial','data_quality']);
  if (id === 'risk_context') return pick(data, ['schema','status','reason','state','evidence_class','rights_status','context_only','executable','real_money_eligible','can_authorize_order','source_snapshot_version','regime','stress_index','coverage_pct','staleness','clocks','attestation','limitations','disclaimer']);
  if (id === 'liquidity') return pick(data, ['asof','funding_regime','segments','segment_reports','report_scope','evidence_url','full_fidelity','disclaimer']);
  if (id === 'institutions') {
    // Institution rows can contain private/mixed-rights numerics. Preserve public aggregate status only.
    return { ...pick(data, ['as_of', 'historical_evidence', 'method_note', 'quadrant_rule']), scope: 'India failure-radar public aggregate metadata only', tiers: data.tiers, row_count: (data.rows as Json[]).length, excluded_stale_count: Array.isArray(data.excluded_stale) ? data.excluded_stale.length : null, excluded_invalid_evidence_count: Array.isArray(data.excluded_invalid_evidence) ? data.excluded_invalid_evidence.length : null, detail_policy: 'Open the source for institution-level evidence; no raw institution financial data is redistributed.' };
  }
  return {
    ...pick(data, ['schema','generated_at','status','coverage','methodology','caveats','legal_notices']),
    markets: (data.markets as Json[]).filter(record).slice(0, 32).map(m => ({
      ...pick(m, ['market_id','display_name','currency','timezone','status','support_status','known_gaps','faults']),
      benchmark: metric(m.benchmark, now), policy_anchor: metric(m.policy_anchor, now),
      metrics: Array.isArray(m.metrics) ? m.metrics.filter(record).slice(0, 32).map(v => metric(v, now)) : [],
    })),
  };
}
export async function assemble(fetcher: Fetcher = fetch, now = new Date()) {
  const sources = await Promise.all(SOURCES.map(s => fetchSource(s, fetcher, now)));
  const available = sources.filter(s => s.status === 'AVAILABLE');
  const sections = await Promise.all(sources.map(async s => {
    const content = s.data ? compact(s.id, s.data, now) : null;
    const { data: _, ...provenance } = s;
    return { ...provenance, evidence_id: `seiche-dossier:${s.id}`, content_sha256: content ? await sha256(canonical(content)) : null, content };
  }));
  const payload = {
    schema: 'seiche.evidence-dossier.v1', generated_at: now.toISOString(),
    status: available.length === sources.length ? 'AVAILABLE' : 'PARTIAL',
    products: ['Seiche', 'Undertow', 'LiquiLens'],
    context_only: true, real_money_eligible: false, can_authorize_order: false,
    coverage: { available: available.length, total: sources.length, unavailable: sources.filter(s => s.status !== 'AVAILABLE').map(s => ({ id: s.id, status: s.status, reason: s.reason })) },
    // Two independent products must actually respond before any settlement is attempted.
    chargeable: sources.some(s => s.id === 'funding' && s.status === 'AVAILABLE') && sources.some(s => s.id === 'liquidity' && s.status === 'AVAILABLE') && sources.some(s => s.id === 'money_markets' && s.status === 'AVAILABLE'),
    sections,
    limitations: [
      'Research context only; not investment advice, an executable quote, or authority to trade.',
      'Payment purchases bounded assembly, normalization and provenance packaging of public evidence; original sources remain publicly accessible.',
      'Snapshot freshness is not observation freshness. Publication and observation clocks remain independent.',
      'Product-specific ratings remain independent. No joint risk score or causal inference is computed.',
      'Missing, stale, withheld and failed-validation evidence is not a calm or safe reading.',
      'SHA-256 fingerprints detect changes to captured bytes; they do not authenticate providers or prove the correctness of research.',
      'A complete dossier may be PARTIAL because a product or evidence gate is unavailable. Coverage is disclosed before payment.',
    ],
  };
  return { ...payload, integrity: { algorithm: 'SHA-256', canonicalization: 'sorted-key-json-v1', payload_sha256: await sha256(canonical(payload)), authenticated: false } };
}
export type Dossier = Awaited<ReturnType<typeof assemble>>;
export function preview(dossier: Dossier) {
  return { schema: 'seiche.evidence-preview.v1', generated_at: dossier.generated_at, status: dossier.status, chargeable: dossier.chargeable, coverage: dossier.coverage,
    sources: dossier.sections.map(({ content: _, content_sha256: __, ...s }) => s), limitations: dossier.limitations,
    unlocks: 'Five product evidence sections, explicit availability and validation limits, per-section content fingerprints, and redistribution-allowed dated money-market observations.',
  };
}
