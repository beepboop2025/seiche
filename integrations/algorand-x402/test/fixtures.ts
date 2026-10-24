import { SOURCES, type Fetcher } from '../src/evidence.js';
export const NOW = new Date('2026-10-02T13:00:00Z');
export const PAYTO = 'UCEMWXFIEZKCUVXDJJVJWMRR72QCNQHX5SPNCTJ2AYEBRBQMGNGZYHZAVM';
export const BASE = 'https://dossier.example';
export function fixtures() {
 const metric = { id: 'US.SOFR', availability: 'AVAILABLE', status: 'FRESH', redistribution_status: 'allowed', value: 4.5, unit: '%', asof: '2026-10-01', source_url: 'https://www.newyorkfed.org/markets/reference-rates/sofr', published_at: '2026-10-02T12:00:00Z', knowledge_time: '2026-10-02T12:05:00Z', history: [['2026-01-01',999]], evidence_hash: 'fixture' };
 return {
 funding: { schema:'seiche.public.v2', generated_at: NOW.toISOString(), conclusion: { regime:'TEST_ONLY' }, proof: { withheld:true, reason:'fixture_not_a_backtest' }, data_quality: { status_counts: { stale:2 } } },
 risk_context: { schema:'seiche.risk-context.v1',context_only:true, can_authorize_order:false,real_money_eligible:false,clocks:{snapshot_generated_at:NOW.toISOString(),evidence_as_of:'2026-01-01',retired_sources:[{mnemonic:'TED',as_of:'2022-01-21'}]},attestation:{status:'not_evaluated'},limitations:['fixture'] },
 money_markets: { schema:'seiche.global-money-markets.v1',generated_at:NOW.toISOString(),status:'PARTIAL',coverage:{declared_markets:1},markets:[{market_id:'US-USD',status:'LIVE',benchmark:metric,metrics:[{...metric,id:'restricted',redistribution_status:'licensed',value:777},{...metric,id:'stale',status:'STALE',value:888}]}] },
 liquidity: { asof:'2026-10-02',segments:{EQUITY:'PARTIAL'},segment_reports:[{segment:'EQUITY',tier:'PARTIAL',coverage:{blockers:[{code:'FAILED_NEGATIVE_CONTROL'}]}}],report_scope:'public subset' },
 institutions: { as_of:'2026-10-02',historical_evidence:{validated_backtest_eligible:false,reason:'fixture_limits'},rows:[{secret_unapproved_numeric:999}],tiers:{UNKNOWN:1} },
 };
}
export function sourceFetcher(values: Record<string, unknown> = fixtures(), failures: Record<string,number> = {}): Fetcher {
 return (async (url: string|URL|Request) => { const source = SOURCES.find(s=>s.url===String(url)); if(!source)throw new Error('unexpected URL'); const status=failures[source.id]??200;return new Response(JSON.stringify(values[source.id]),{status,headers:{'Content-Type':'application/json'}}); }) as Fetcher;
}
